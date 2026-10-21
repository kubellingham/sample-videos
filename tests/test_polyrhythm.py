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
