import math

import pytest

from satisfy import formats
from satisfy.config import VideoConfig
from satisfy.palettes import resolve
from satisfy.scene import Circle


def _cfg(**kw):
    base = dict(format="shrinking_ring", seed=11, duration=12.0, params={"tries": 1})
    base.update(kw)
    return VideoConfig(**base)


def _sim(cfg, fps):
    return formats.get(cfg.format).simulate(cfg, resolve(cfg.palette, cfg.seed), fps)


def _events(tl):
    return [(round(e.t, 9), e.kind, round(e.x, 6), round(e.y, 6)) for e in tl.events]


def test_registry_discovers_formats():
    assert "shrinking_ring" in formats.available()


def test_same_seed_same_video():
    a, b = _sim(_cfg(), 15), _sim(_cfg(), 15)
    assert _events(a.timeline) == _events(b.timeline)
    assert a.n_frames == b.n_frames
    ca = [o for o in a.frames[-1].items if isinstance(o, Circle)]
    cb = [o for o in b.frames[-1].items if isinstance(o, Circle)]
    assert [(o.x, o.y, o.r) for o in ca] == [(o.x, o.y, o.r) for o in cb]


def test_different_seed_differs():
    assert _events(_sim(_cfg(seed=1), 15).timeline) != _events(_sim(_cfg(seed=2), 15).timeline)


def test_preview_and_final_share_physics():
    """The frame rate only changes sampling, never the simulation."""
    lo, hi = _sim(_cfg(), 15), _sim(_cfg(), 60)
    assert _events(lo.timeline) == _events(hi.timeline)
    assert abs(lo.length - hi.length) < 1 / 15
    # frame k at 15 fps is frame 4k at 60 fps
    for k in (0, 10, lo.n_frames // 2):
        a = [o for o in lo.frames[k].items if isinstance(o, Circle)][0]
        b = [o for o in hi.frames[4 * k].items if isinstance(o, Circle)][0]
        assert (a.x, a.y) == (b.x, b.y)


def test_motion_starts_on_frame_1():
    res = _sim(_cfg(), 60)
    b0 = [o for o in res.frames[0].items if isinstance(o, Circle)][0]
    b1 = [o for o in res.frames[1].items if isinstance(o, Circle)][0]
    assert math.hypot(b1.x - b0.x, b1.y - b0.y) > 5


def test_opens_mid_flight_with_a_quick_first_bounce():
    from satisfy.formats.shrinking_ring import ShrinkingRing

    for seed in (1, 2, 3, 7):
        res = _sim(_cfg(seed=seed), 60)
        first = res.timeline.of_kind("bounce")[0]
        assert 0.0 < first.t < 0.3
        assert res.prelude, "pre-roll frames feed the trail on frame 0"
        ball = [o for o in res.frames[0].items if isinstance(o, Circle)][0]
        assert ball.r == 2 * 22.0 == ShrinkingRing.defaults["ball_radius"]
        before = [o for o in res.prelude[-1].items if isinstance(o, Circle)][0]
        assert math.hypot(ball.x - before.x, ball.y - before.y) > 5  # already moving at frame 0


def test_video_ends_with_an_ending():
    res = _sim(_cfg(), 15)
    tl = res.timeline
    assert tl.ending in ("escape", "trapped", "timeout")
    assert tl.of_kind("escape", "freeze")
    assert res.length <= 12.0 + 1e-9


def test_spawn_mode_adds_balls():
    res = _sim(_cfg(params={"tries": 1, "mode": "spawn"}, duration=10.0), 15)
    assert res.timeline.counts().get("spawn", 0) >= 3


def test_pacing_prefers_endings_in_window():
    cfg = _cfg(duration=28.0, params={"tries": 8})
    cls = formats.get(cfg.format)
    v, rehearsal = cls.pick_variant(cfg, resolve("neon", cfg.seed))
    tl = cls(cfg, resolve("neon", cfg.seed), variant=v).run(fps=None)
    assert _events(tl) == _events(rehearsal)
    assert tl.ending != "timeout"
    assert 0.55 * 28 <= tl.length <= 28


def _escape_seed():
    cls = formats.get("shrinking_ring")
    for seed in range(1, 40):
        cfg = VideoConfig(format="shrinking_ring", seed=seed)
        _, tl = cls.pick_variant(cfg, resolve("neon", seed))
        if tl.ending == "escape":
            return seed
    pytest.skip("no escape seed found")


def test_escape_slow_motion_lands_on_the_burst():
    cfg = VideoConfig(format="shrinking_ring", seed=_escape_seed())
    lo, hi = _sim(cfg, 15), _sim(cfg, 60)
    assert _events(lo.timeline) == _events(hi.timeline)  # slow motion is fps-independent
    tl = hi.timeline
    (slow,), (esc,) = tl.of_kind("slowmo"), tl.of_kind("escape")
    assert slow.data["duration"] == pytest.approx(0.4, abs=1e-3)
    assert esc.t - slow.t == pytest.approx(0.4, abs=1e-3)  # burst right after the slow stretch
    assert esc.t - esc.data["pt"] == pytest.approx(0.3, abs=1e-3)  # 0.1 s of physics shown over 0.4 s
    # the burst is first drawn on exactly the frame its sound starts on
    from satisfy.scene import Arc
    from satisfy.timeline import frame_of

    for res in (lo, hi):
        f = frame_of(esc.t, res.fps)
        assert len([o for o in res.frames[f - 1].items if isinstance(o, Arc)]) == 1  # ring intact
        assert len([o for o in res.frames[f].items if isinstance(o, Arc)]) > 1  # shattered
    # at quarter speed the ball moves a quarter as far per frame
    f0 = math.ceil(slow.t * 60) + 4
    a, b = [[o for o in hi.frames[f].items if isinstance(o, Circle)][0] for f in (f0, f0 + 1)]
    c, d = [[o for o in hi.frames[f].items if isinstance(o, Circle)][0] for f in (f0 - 40, f0 - 39)]
    assert math.hypot(b.x - a.x, b.y - a.y) < 0.6 * math.hypot(d.x - c.x, d.y - c.y) + 1


def test_ring_layout_and_final_third_squeeze():
    from satisfy.formats.shrinking_ring import ShrinkingRing

    sim = ShrinkingRing(_cfg(duration=28.0), resolve("neon", 1))
    sim.setup()
    p = sim.p
    assert 0.83 <= 2 * (p.ring_radius + p.ring_width) / 1080 <= 0.87
    t2, t3 = sim.t_squeeze, sim.t_end
    early = (sim._ring_radius(0) - sim._ring_radius(t2)) / t2
    late = (sim._ring_radius(t2) - sim._ring_radius(t3)) / (t3 - t2)
    assert late > 2 * early
    assert t2 == pytest.approx(t3 * 2 / 3)


def test_video_loops_back_to_frame_0():
    import numpy as np

    from satisfy.render import Renderer

    cfg = _cfg(duration=8.0, hook_text="Loop?")
    res = _sim(cfg, 15)
    assert res.n_frames - res.loop_start == round(0.5 * 15)
    r = Renderer(res, resolve("neon", cfg.seed), cfg.hook_text, 180, 320)
    first = r.render(0).astype(int)
    hold = r.render(res.loop_start - 1).astype(int)
    last = r.render(res.n_frames - 1).astype(int)
    assert np.abs(last - first).mean() < 0.25 * np.abs(hold - first).mean()


def test_text_stays_out_of_the_shorts_ui():
    from satisfy import SAFE_BOTTOM, SAFE_RIGHT
    from satisfy.render import Renderer
    from satisfy.scene import Label

    for seed in (7, _escape_seed()):
        cfg = VideoConfig(format="shrinking_ring", seed=seed, hook_text="Will it escape before it fills the ring?")
        res = _sim(cfg, 15)
        r = Renderer(res, resolve("neon", seed), cfg.hook_text, 180, 320)
        assert r.hook_bounds.right() <= SAFE_RIGHT and r.hook_bounds.bottom() <= SAFE_BOTTOM
        for scene in res.frames:
            for o in scene.overlay:
                if isinstance(o, Label) and o.text and o.size > 1:
                    box = Renderer.label_layout(o)[3]
                    assert box.right() <= SAFE_RIGHT and box.bottom() <= SAFE_BOTTOM, o.text
                    # laid out inside the safe area by the format itself, not moved by the renderer
                    assert abs((box.left() + box.right()) / 2 - o.x) < 1, o.text
    # the renderer moves a label that would cross into the UI
    box = Renderer.label_layout(Label("999 BOUNCES", 1000, 1800, 80, stroke=(0, 0, 0), stroke_width=8))[3]
    assert box.right() <= SAFE_RIGHT and box.bottom() <= SAFE_BOTTOM


def test_unknown_param_is_rejected():
    with pytest.raises(ValueError, match="Unknown params"):
        _sim(_cfg(params={"bogus": 1}), 15)


@pytest.mark.parametrize("fps", [15, 60])
def test_every_bounce_is_drawn_on_its_sound_frame(fps):
    """Each bounce's ripple first appears (at its starting radius) on the frame
    its note is placed on: picture and sound agree to the frame, slow motion included."""
    from satisfy.scene import Ripple
    from satisfy.timeline import frame_of

    cfg = VideoConfig(format="shrinking_ring", seed=_escape_seed())
    res = _sim(cfg, fps)

    def ripple_at(f, e):
        return any(isinstance(o, Ripple) and abs(o.x - e.x) < 0.01 and abs(o.y - e.y) < 0.01
                   for o in res.frames[f].items)

    bounces = res.timeline.of_kind("bounce")
    assert len(bounces) > 10
    for e in bounces:
        f = frame_of(e.t, fps)
        assert ripple_at(f, e) and not ripple_at(f - 1, e), (e.t, f)
