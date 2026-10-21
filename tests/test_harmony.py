"""Harmony engine: plan, chord-tone hits, pad and bass, loudness."""

import numpy as np
import pytest

from satisfy.audio import SR, harmony, loudness, render_audio, synth
from satisfy.audio.sound_sets import get
from satisfy.config import VideoConfig
from satisfy.pipeline import simulate
from satisfy.timeline import Event, Timeline


def test_plan_defaults_and_options():
    h = harmony.plan({}, seed=1)
    assert h.key_name == "C" and h.progression == "I-V-vi-IV"
    assert 70 <= h.tempo <= 90 and h.tempo == int(h.tempo)
    assert [c.name for c in h.chords] == ["I", "V", "vi", "IV"]
    assert [c.tones for c in h.chords] == [(0, 4, 7), (7, 11, 2), (9, 0, 4), (5, 9, 0)]
    bar = 4 * 60 / h.tempo
    assert [c.t for c in h.chords] == pytest.approx([0, bar, 2 * bar, 3 * bar]) and h.cycle == pytest.approx(4 * bar)
    assert h.chord_at(bar * 5.5).name == "V"  # the progression repeats
    assert h.tones(0.0) == [60, 64, 67, 72, 76, 79, 84]  # C4..C6
    # random picks from the curated list; fixing one option leaves the others' draws alone
    seen = {harmony.plan({"progression": "random"}, s).progression for s in range(40)}
    assert seen <= set(harmony.PROGRESSIONS) and len(seen) > 3
    for s in range(10):
        a, b = harmony.plan({"key": "random"}, s), harmony.plan({"key": "random", "tempo": 77}, s)
        assert a.tonic == b.tonic and b.tempo == 77
    assert harmony.plan({"progression": "ii-V-I", "key": "D"}, 1).chords[0].tones == (4, 7, 11)  # Em in D


def test_parse_progression():
    assert harmony.parse_progression("vi-IV-I-V") == [(9, "min", "vi"), (5, "maj", "IV"), (0, "maj", "I"),
                                                      (7, "maj", "V")]
    assert harmony.parse_progression("ii-vii°-I")[1] == (11, "dim", "vii°")
    with pytest.raises(ValueError):
        harmony.parse_progression("I-X-V")


def _ring(seed=7, **sound):
    cfg = VideoConfig(format="shrinking_ring", seed=seed, sound_set="harmony", sound=sound)
    return simulate(cfg, 15)[0], cfg


def test_hits_play_chord_tones_without_repeats():
    sim, cfg = _ring()
    s = get("harmony")(cfg.sound, cfg.seed)
    s.prepare(sim.timeline)
    tl = s.assign(sim.timeline)
    hits = [e for e in tl.events if e.kind == "bounce" and e.data["notes"]]
    assert len(hits) > 40
    for e in hits:
        tones = set(s.h.tones(e.t, 60, 84))
        assert {m for m, _ in e.data["notes"]} <= tones  # chord tones, C4..C6
        base = 0.42 + 0.4 * e.strength
        assert 0.85 - 1e-6 <= e.data["notes"][0][1] / base <= 1.15 + 1e-6  # velocity +/-15%
    melody = [e.data["notes"][0][0] for e in hits]
    assert all(a != b for a, b in zip(melody, melody[1:]))  # no immediate repeats
    dyads = sum(len(e.data["notes"]) == 2 for e in hits) / len(hits)
    assert 0.05 < dyads < 0.3
    assert len({s.h.chord_index(e.t) for e in hits}) == 4  # follows the progression


def test_register_hint_orders_simultaneous_notes():
    events = [Event(0.5, "note", strength=0.7, data={"register": k / 5}) for k in range(6)]
    tl = Timeline(events=events, length=4.0, music=harmony.plan({}, 1).to_dict())
    s = get("harmony")({}, 1)
    s.prepare(tl)
    picks = [e.data["notes"][0][0] for e in s.assign(tl).events]
    assert len(set(picks)) == 6 and picks[0] < picks[-1]  # all different, low register lowest


def test_bed_follows_the_chords():
    h = harmony.progression_harmony(0, "I-V-vi-IV", 80.0, 1)
    tl = Timeline(events=[], length=h.cycle, music=h.to_dict())
    audio = render_audio(tl, "harmony", {"pad": 0.0}, 1, 15, int(h.cycle * 15))  # bass only
    mono = audio.mean(axis=0)
    bar = int(h.bar * SR)
    for i, root_hz in enumerate((65.41, 98.0, 110.0, 87.31)):  # C2 G2 A2 F2
        seg = mono[i * bar + SR // 10: (i + 1) * bar - SR // 10]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1 / SR)
        low = (f > 50) & (f < 125)
        assert abs(f[low][np.argmax(spec[low])] - root_hz) < 2.0, i


def test_loudness_meter_and_target():
    t = np.arange(SR * 5) / SR
    sine = np.stack([0.1 * np.sin(2 * np.pi * 997 * t)] * 2)
    assert loudness.integrated_lufs(sine) == pytest.approx(-20.0, abs=0.1)  # BS.1770 reference tone
    sim, cfg = _ring()
    audio = render_audio(sim.timeline, "harmony", {}, cfg.seed, 15, sim.n_frames)
    assert loudness.integrated_lufs(audio) == pytest.approx(-14.0, abs=0.1)
    assert np.abs(audio).max() <= 10 ** (-1 / 20) + 1e-4
    # any set can ask for a loudness target
    quiet = render_audio(sim.timeline, "pentatonic", {"loudness": -18}, cfg.seed, 15, sim.n_frames)
    assert loudness.integrated_lufs(quiet) == pytest.approx(-18.0, abs=0.1)


def test_limiter_never_exceeds_ceiling_and_wraps():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.3, (2, SR * 2))
    x[:, 100] = 3.0  # a peak right at the start
    x[:, -50] = -2.5  # and right at the end
    y = loudness.limit(x, -1.0, circular=True)
    assert np.abs(y).max() <= 10 ** (-1 / 20) + 1e-9
    g = y[0] / x[0]
    # circular: the gain is already down at the very end, ducking for the start's peak
    assert g[-1] < 0.6 and g[0] < 0.6


@pytest.mark.parametrize("name", ["music_box", "celesta", "felt_piano", "kalimba"])
def test_new_instruments(name):
    for hz in (261.63, 523.25, 1046.5):
        v = synth.INSTRUMENTS[name](hz, 1.0)
        assert np.abs(v).max() == pytest.approx(1.0, abs=0.3) and np.abs(v[-48:]).max() < 1e-3  # no click at the cut
        spec = np.abs(np.fft.rfft(v[: SR // 2]))
        f = np.fft.rfftfreq(SR // 2, 1 / SR)
        assert abs(f[np.argmax(spec)] - hz) < 4  # the fundamental is the strongest partial
