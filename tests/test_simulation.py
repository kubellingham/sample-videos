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


def test_first_bounce_is_quick():
    res = _sim(_cfg(), 60)
    first = res.timeline.of_kind("bounce")[0]
    assert first.t < 0.8


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
    cfg = _cfg(duration=40.0, params={"tries": 8})
    cls = formats.get(cfg.format)
    v = cls.pick_variant(cfg, resolve("neon", cfg.seed))
    tl = cls(cfg, resolve("neon", cfg.seed), variant=v).run(fps=None)
    assert tl.ending != "timeout"
    assert 0.55 * 40 <= tl.length <= 40


def test_unknown_param_is_rejected():
    with pytest.raises(ValueError, match="Unknown params"):
        _sim(_cfg(params={"bogus": 1}), 15)
