"""MIDI import: reading files, and formats choreographed to a score."""

import struct

import pytest

from satisfy import config
from satisfy.audio import harmony, midi
from satisfy.audio.sound_sets import get
from satisfy.config import VideoConfig
from satisfy.formats.base import PHYSICS_HZ
from satisfy.formats.polyrhythm_loop import PolyrhythmLoop
from satisfy.formats.shrinking_ring import ShrinkingRing
from satisfy.palettes import resolve
from satisfy.pipeline import simulate

ODE = "assets/midi/ode_to_joy.mid"


def _chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack(">I", len(body)) + body


def test_reads_running_status_tempo_changes_and_skips_drums(tmp_path):
    # format 1: a tempo track (120 BPM, then 60 BPM after two beats; 3/4) and a note track
    # written with running status and note-on velocity 0 as note-off, plus a drum hit
    tempo = (b"\x00\xff\x51\x03" + (500_000).to_bytes(3, "big") + b"\x00\xff\x58\x04\x03\x02\x18\x08"
             + b"\x87\x40\xff\x51\x03" + (1_000_000).to_bytes(3, "big") + b"\x00\xff\x2f\x00")
    notes = (b"\x00\x90\x3c\x64" b"\x00\x40\x50" b"\x83\x60\x3c\x00" b"\x00\x40\x00"
             b"\x83\x60\x43\x5a" b"\x83\x60\x80\x43\x00" b"\x00\x99\x24\x64" b"\x81\x70\x89\x24\x00"
             b"\x00\xff\x2f\x00")
    path = tmp_path / "x.mid"
    path.write_bytes(_chunk(b"MThd", struct.pack(">HHH", 1, 2, 480)) + _chunk(b"MTrk", tempo)
                     + _chunk(b"MTrk", notes))
    s = midi.load(path)
    assert [(n.pitch, round(n.t, 6), round(n.dur, 6), n.vel) for n in s.notes] == [
        (60, 0.0, 0.5, 100), (64, 0.0, 0.5, 80), (67, 1.0, 1.0, 90)]  # the second beat-pair runs at 60 BPM
    assert s.tempo == pytest.approx(120) and s.meter == (3, 4)
    assert s.bars == pytest.approx([0.0, 2.0]) and s.length == pytest.approx(2.0)


def test_write_read_round_trip_and_grouping(tmp_path):
    path = tmp_path / "y.mid"
    midi.write(path, [(0, 1, 60, 100), (0, 1, 48, 70), (1, 0.5, 62, 90), (1.5, 2.5, 64, 80)], tempo=90)
    s = midi.load(path)
    beat = 60 / 90
    assert [(round(t / beat, 4), [n.pitch for n in g]) for t, g in s.onsets()] == [
        (0.0, [48, 60]), (1.0, [62]), (1.5, [64])]
    assert s.length == pytest.approx(4 * beat, abs=1e-4)
    looped = s.segment(9 * beat)  # two whole passes fit
    assert len(looped.notes) == 8 and looped.length == pytest.approx(8 * beat, abs=1e-4)
    with pytest.raises(FileNotFoundError):
        midi.load(tmp_path / "missing.mid")


def test_chords_estimated_from_the_score():
    s = midi.load(ODE)
    h = harmony.from_score(s)
    assert h.key_name == "C" and h.source == "midi" and h.cycle == pytest.approx(s.length)
    bar = s.bars[1]
    got = [(round(c.t / bar, 2), c.name) for c in h.chords]
    assert got[:4] == [(0.0, "C"), (1.0, "G"), (2.0, "C"), (3.0, "G")]
    assert (7.5, "C") in got  # the bass moves to C halfway through bar 8


def test_polyrhythm_rings_follow_the_score():
    cfg = config.load("configs/polyrhythm_midi.yaml")
    res, pal = simulate(cfg, 15)
    tl = res.timeline
    score = midi.load(ODE)
    assert tl.periodic and res.length == pytest.approx(score.length) and res.n_frames == round(score.length * 15)
    sim = PolyrhythmLoop(cfg, pal)
    sim.setup()
    pitches = sorted({n.pitch for n in score.notes})
    assert sim.n == len(pitches)
    onsets = {round(n.t, 6): set() for n in score.notes}
    for n in score.notes:
        onsets[round(n.t, 6)].add(n.pitch)
    played = {}
    for e in tl.events:
        frac, age = sim._phase(e.data["ring"], e.t)
        assert min(frac, 1 - frac) < 1e-9 and age < 1e-9  # its dot is at the top as the note plays
        for m, _ in e.data["notes"]:
            assert pitches.index(m) == e.data["ring"]  # one ring per pitch, lowest outermost
            played.setdefault(round(e.t, 6), set()).add(m)
    assert played == onsets  # every note, at its time
    assert tl.music["source"] == "midi"


@pytest.mark.parametrize("ending", ["trapped", "escape"])
def test_ring_bounces_on_every_note(ending):
    cfg = config.load("configs/shrinking_ring_midi.yaml")
    cfg.params["midi_ending"] = ending
    pal = resolve(cfg.palette, cfg.seed)
    sim = ShrinkingRing(cfg, pal)
    sim.run(fps=None)
    groups = midi.load(ODE).onsets()
    off = 0.2 - groups[0][0]  # the first note lands 0.2 s in
    assert len(sim.contacts) >= len(groups) - 2  # (nearly) every note is a bounce...
    assert all(abs(actual - planned) <= 2.5 / PHYSICS_HZ for planned, actual in sim.contacts)  # ...on time
    res, _ = simulate(cfg, 15)
    tl = res.timeline
    times = sorted(e.t for e in tl.events if e.kind in ("bounce", "note"))
    assert times == pytest.approx([t + off for t, _ in groups])  # every note plays, at its time
    last = times[-1]
    assert tl.ending == ending
    if ending == "trapped":
        assert tl.of_kind("freeze")[0].t == pytest.approx(last)  # the freeze lands on the last note
    else:
        beats = (tl.of_kind("escape")[0].t - last) / (60 / 100)
        assert abs(beats - round(beats)) < 0.05 and round(beats) >= 1  # the burst lands on a beat


def test_free_physics_plays_the_score_in_order():
    """Without choreography a MIDI file still gives the pitches: hit k plays note group k."""
    cfg = VideoConfig(format="shrinking_ring", seed=5, sound_set="harmony", sound={"midi": ODE},
                      params={"sync": "free"})
    res, _ = simulate(cfg, 15)
    s = get("harmony")(cfg.sound, cfg.seed)
    s.prepare(res.timeline)
    tl = s.assign(res.timeline)
    played = [sorted(m for m, _ in e.data["notes"]) for e in tl.events if e.kind == "bounce" and e.data["notes"]]
    groups = [sorted(n.pitch for n in g) for _, g in midi.load(ODE).onsets()]
    assert len(played) > 20 and played == [groups[k % len(groups)] for k in range(len(played))]
