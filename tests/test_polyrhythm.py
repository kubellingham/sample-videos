"""polyrhythm_loop: the video is exactly one cycle and loops with no seam."""

import numpy as np
import pytest

from satisfy import config as config_mod
from satisfy import formats
from satisfy.audio import SR, render_audio
from satisfy.config import VideoConfig
from satisfy.palettes import resolve
from satisfy.render import Renderer
from satisfy.scene import Circle
from satisfy.timeline import Event, Timeline

L = 12.0


def _cfg(duration=L, **params):
    return VideoConfig(format="polyrhythm_loop", seed=1, duration=duration,
                       params={"rings": 6, "base": 3, **params})


def _sim(cfg, fps):
    return formats.get(cfg.format).simulate(cfg, resolve(cfg.palette, cfg.seed), fps)


def test_every_top_crossing_is_one_note():
    tl = _sim(_cfg(), 15).timeline
    for i in range(6):
        laps = 3 + i
        times = sorted(e.t for e in tl.events if e.data["ring"] == i)
        assert times == pytest.approx([k * L / laps for k in range(laps)])  # exact crossing instants
        assert all(e.data["degree"] == i for e in tl.events if e.data["ring"] == i)
    assert sum(1 for e in tl.events if e.t == 0) == 6  # all aligned at the start (and at the loop point)
    assert all(0 <= e.t < L for e in tl.events)  # the crossing at t = L is the next loop's t = 0
    assert tl.periodic and tl.audio_timing == "exact"


def test_video_is_exactly_one_cycle_with_no_crossfade():
    res = _sim(_cfg(), 15)
    assert res.n_frames == L * 15
    assert res.loop_start == res.n_frames
    assert res.timeline.ending == "loop"


def test_default_duration_is_the_formats_own():
    assert config_mod.from_dict({"format": "polyrhythm_loop"}).duration == 45.0
    assert config_mod.from_dict({"format": "shrinking_ring"}).duration == 28.0


def _circles(scene):
    return [(round(o.x, 6), round(o.y, 6), round(o.r, 6)) for o in scene.items if isinstance(o, Circle)]


def test_picture_loops_seamlessly():
    """The frames after the last one are frames 0, 1, 2 again, and the frame
    before frame 0 (pre-roll) is the last frame of the cycle."""
    fps = 15
    loop = _sim(_cfg(), fps)
    longer = _sim(_cfg(duration=L + 3 / fps, cycle=L), fps)  # same cycle, keeps going past the end
    n = loop.n_frames
    assert longer.n_frames == n + 3
    assert _circles(loop.prelude[-1]) == _circles(loop.frames[-1])
    pal = resolve("aurora", 1)
    a, b = Renderer(loop, pal, "Loop", 90, 160), Renderer(longer, pal, "Loop", 90, 160)
    for k in range(3):
        diff = np.abs(a.render(k).astype(int) - b.render(n + k).astype(int))
        assert diff.max() <= 2 and diff.mean() < 0.01, k


def _note_timeline(events, periodic):
    return Timeline(events=events, ending="loop", end_t=L, length=L, periodic=periodic, audio_timing="exact")


def test_audio_wraps_around_seamlessly():
    fps, n_frames = 15, int(L * 15)
    events = _sim(_cfg(), fps).timeline.events
    loop = render_audio(_note_timeline(events, True), "pentatonic", {"instrument": "glock"}, 1, fps, n_frames)
    # the same notes played continuously for three cycles: the middle cycle is what a looping viewer hears
    three = [Event(t=e.t + c * L, kind=e.kind, x=e.x, y=e.y, strength=e.strength, data=e.data)
             for c in range(3) for e in events]
    cont = render_audio(_note_timeline(three, False), "pentatonic", {"instrument": "glock"}, 1, fps, 3 * n_frames)
    n = n_frames * SR // fps
    mid = cont[:, n: 2 * n]
    scale = float(np.sum(mid * loop) / np.sum(loop * loop))  # loudness normalisation differs slightly
    peak = np.abs(loop).max()
    assert np.abs(mid - scale * loop).max() < 0.02 * peak
    # Across the seam the loop must play exactly what the continuous performance plays next:
    # the loop's end into its start == the end of cycle 2 into the start of cycle 3.
    looped = np.concatenate([loop[:, -SR // 10:], loop[:, : SR // 2]], axis=1) * scale
    continuous = cont[:, 2 * n - SR // 10: 2 * n + SR // 2]
    assert np.abs(looped - continuous).max() < 0.02 * peak
    # so the step at the seam is the step a continuous performance makes there (the chord's attack)
    assert abs(scale * (loop[0, 0] - loop[0, -1]) - (cont[0, 2 * n] - cont[0, 2 * n - 1])) < 0.02 * peak


def test_exact_audio_timing_is_sample_accurate():
    t = 1.2345
    tl = Timeline(events=[Event(t, "note", data={"degree": 3})], ending="none", length=3.0, audio_timing="exact")
    audio = np.abs(render_audio(tl, "pentatonic", {}, 1, 15, 45)).max(axis=0)
    start = round(t * SR)
    assert audio[: start - 1].max() < 1e-3 and audio[start: start + 96].max() > 0.05


# ------------------------------------------------------------- 16th-note grid
def _grid_cfg(**sound):
    return VideoConfig(format="polyrhythm_loop", seed=1, sound_set="harmony", duration=45.0,
                       sound=sound, params={"rings": 12})


def test_grid_hits_land_on_sixteenths_and_the_progression_fills_the_cycle():
    from satisfy.formats.polyrhythm_loop import PolyrhythmLoop

    res = _sim(_grid_cfg(), 15)
    tl, m = res.timeline, res.timeline.music
    assert tl.periodic and m["tempo"] == pytest.approx(72) and m["bars"] == 12
    assert res.length == pytest.approx(m["cycle"]) == pytest.approx(40.0) and res.n_frames == 600
    sixteenth = m["cycle"] / m["sixteenths"]
    assert all(abs(e.t / sixteenth - round(e.t / sixteenth)) < 1e-9 for e in tl.events)
    assert [c["name"] for c in m["chords"]] == ["I", "V", "vi", "IV"]  # exactly once per cycle
    assert [c["t"] for c in m["chords"]] == pytest.approx([0, 10, 20, 30])  # 3 bars each
    sim = PolyrhythmLoop(_grid_cfg(), resolve("aurora", 1))
    sim.setup()
    assert all(m["sixteenths"] % laps == 0 for laps in sim.laps) and len(set(sim.laps)) == 12
    assert sim.laps == sorted(sim.laps)  # outer rings slowest


def test_grid_fit_other_progressions_and_tempos():
    from satisfy.formats.polyrhythm_loop import fit_grid

    tempo, bars, laps = fit_grid(45.0, 12, 3)  # ii-V-I: 15 bars at 80 BPM is exactly 45 s
    assert (tempo, bars) == (80.0, 15) and all(240 % v == 0 for v in laps)
    tempo, bars, _ = fit_grid(45.0, 12, 4, tempo=75.0)  # a fixed tempo is kept (to within 1%)
    assert tempo == pytest.approx(75.0, rel=0.01) and (240 * bars / tempo * 15) % 1 == pytest.approx(0, abs=1e-9)
    assert fit_grid(45.0, 3, 4, laps=[4, 6, 8])[2] == [4, 6, 8]
    with pytest.raises(ValueError, match="16th-note cycle"):
        fit_grid(45.0, 3, 4, laps=[3, 4, 5])  # needs 60 bars: far too long


def test_harmony_loop_wraps_around_seamlessly():
    """The periodic harmony render (pad, bass, chord-tone hits, tails) equals the
    middle of three back-to-back cycles playing the same notes."""
    from dataclasses import replace

    from satisfy.audio.sound_sets import get

    cfg = VideoConfig(format="polyrhythm_loop", seed=2, sound_set="harmony", duration=20.0,
                      params={"rings": 5, "min_interval": 4})
    res = _sim(cfg, 15)
    tl, n_frames = res.timeline, res.n_frames
    L = res.length
    loop = render_audio(tl, "harmony", {}, 2, 15, n_frames)
    s = get("harmony")({}, 2)
    s.prepare(tl)
    assigned = s.assign(tl)  # the pitches the loop plays (shared by every cycle)
    three = [replace(e, t=e.t + c * L) for c in range(3) for e in assigned.events]
    cont_tl = replace(tl, events=three, periodic=False, length=3 * L, end_t=3 * L, ending="none", loop_t=0.0)
    cont = render_audio(cont_tl, "harmony", {}, 2, 15, 3 * n_frames)
    n = n_frames * SR // 15
    mid = cont[:, n: 2 * n]
    scale = float(np.sum(mid * loop) / np.sum(loop * loop))
    peak = np.abs(loop).max()
    assert np.abs(mid - scale * loop).max() < 0.01 * peak
    looped = np.concatenate([loop[:, -SR // 10:], loop[:, : SR // 2]], axis=1) * scale
    assert np.abs(looped - cont[:, 2 * n - SR // 10: 2 * n + SR // 2]).max() < 0.005 * peak
