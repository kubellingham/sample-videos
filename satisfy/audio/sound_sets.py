"""Sound sets: turn timeline events into a stereo mix.

Each bounce plays "the next note" of the set. Events are placed on the exact
audio sample where their frame starts (48 kHz divides evenly by the frame
rate), so sound and picture line up to the frame.

A config picks a set with ``sound_set`` and can tune it with a ``sound:``
mapping (keys listed in each class's ``options``).
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import numpy as np

from ..timeline import Event, Timeline, frame_of
from . import synth
from .melodies import MELODIES, key_to_pc, melody_midi, midi_to_hz
from .synth import SR, Mixer

KEYS = ["C", "D", "E", "F", "G", "A"]
SCALES = {"major": [0, 2, 4, 7, 9], "minor": [0, 3, 5, 7, 10]}
TRIADS = {"major": [0, 4, 7, 12], "minor": [0, 3, 7, 12]}


class SoundSet:
    name = ""
    description = ""
    options: dict = {}
    reverb_mix = 0.22
    reverb_time = 1.8

    def __init__(self, opts: dict, seed: int):
        known = {"min_gap": 0.055, "volume": 1.0, **self.options}
        unknown = set(opts) - set(known)
        if unknown:
            raise ValueError(f"Unknown options for sound set '{self.name}': {', '.join(sorted(unknown))}. "
                             f"Known: {', '.join(sorted(known))}")
        self.o = SimpleNamespace(**{**known, **opts})
        self.seed = seed
        self.rng = random.Random(f"sound:{self.name}:{seed}")
        self._cache: dict = {}
        self.setup()

    # -- hooks for subclasses ---------------------------------------------
    def setup(self) -> None:
        pass

    def next_note(self, index: int, event: Event) -> int:
        """MIDI note for the ``index``-th played bounce."""
        raise NotImplementedError

    def final_chord(self) -> list[int]:
        """MIDI notes of the closing chord."""
        raise NotImplementedError

    def instrument(self, freq: float, vel: float) -> np.ndarray:
        return synth.INSTRUMENTS[getattr(self.o, "instrument", "marimba")](freq, vel)

    def bed(self, mix: Mixer, timeline: Timeline, fps: int) -> None:
        """Optional background layer under the whole video."""

    # -- shared building blocks ---------------------------------------------
    def voice(self, midi: int) -> np.ndarray:
        key = ("voice", midi)
        if key not in self._cache:
            self._cache[key] = self.instrument(midi_to_hz(midi), 1.0)
        return self._cache[key]

    def play(self, mix: Mixer, midi: int, start: int, vel: float, pan: float = 0.0, send: float = 1.0) -> None:
        mix.add(self.voice(midi), start, gain=vel, pan=pan, send=send)

    def on_bounce(self, mix: Mixer, e: Event, start: int, pan: float, index: int) -> None:
        vel = 0.5 + 0.5 * e.strength
        self.play(mix, self.next_note(index, e), start, vel, pan)

    def on_spawn(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        mix.add(synth.pop(900 + 40 * (e.data.get("count", 0) % 12), seed=start), start, gain=0.22, pan=pan)

    def on_hit(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        mix.add(synth.click(1.0, bright=0.6, seed=start), start, gain=0.25 * e.strength, pan=pan, send=0.3)

    def on_sealed(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        root = self.final_chord()[0]
        mix.add(synth.thump(midi_to_hz(root - 24), dur=0.8), start, gain=0.5)
        self.play(mix, root - 12, start, 0.45)

    def on_escape(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        mix.add(synth.thump(55, dur=1.2), start, gain=0.9)
        mix.add(synth.noise_swell(1.6, 700, 0.7, 0.08, 0.45, seed=self.seed, sweep=2.5), start, gain=0.35, pan=pan)
        chord = self.final_chord()
        run = sorted({n + 12 * o for o in (0, 1) for n in chord})
        for i, m in enumerate(run):
            self.play(mix, m, start + int(i * 0.045 * SR), 0.75 - 0.03 * i, pan=(i / len(run) - 0.5) * 0.8)
        for i, m in enumerate(chord):
            self.play(mix, m, start + int((0.42 + i * 0.03) * SR), 0.7)
        mix.add(synth.pad_chord([midi_to_hz(m - 12) for m in chord[:3]], 2.6, attack=0.05, release=1.8,
                                seed=self.seed), start, gain=0.28)

    def on_freeze(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        chord = self.final_chord()
        mix.add(synth.thump(48, dur=1.0), start, gain=0.75)
        # glassy shimmer
        for i, m in enumerate(chord):
            mix.add(synth.chime(midi_to_hz(m + 24), 1.0), start + int(i * 0.02 * SR), gain=0.25,
                    pan=(i - 1.5) * 0.3)
        for i, m in enumerate(chord):
            self.play(mix, m, start + int(i * 0.06 * SR), 0.65)
        mix.add(synth.pad_chord([midi_to_hz(m - 12) for m in chord[:3]], 3.0, attack=0.25, release=2.0,
                                seed=self.seed), start, gain=0.32)

    # -- render ---------------------------------------------------------------
    def render(self, timeline: Timeline, fps: int, n_frames: int) -> np.ndarray:
        n_samples = n_frames * SR // fps
        mix = Mixer(n_samples)
        self.bed(mix, timeline, fps)
        last_note = -1e9
        last_hit = -1e9
        index = 0
        for e in sorted(timeline.events, key=lambda ev: ev.t):
            start = frame_of(e.t, fps) * SR // fps
            pan = max(-1.0, min(1.0, (e.x - 540.0) / 540.0)) * 0.45
            if e.kind == "bounce":
                if e.t - last_note < float(self.o.min_gap):
                    continue
                last_note = e.t
                self.on_bounce(mix, e, start, pan, index)
                index += 1
            elif e.kind == "hit":
                if e.t - last_hit < 0.03:
                    continue
                last_hit = e.t
                self.on_hit(mix, e, start, pan)
            else:
                handler = getattr(self, f"on_{e.kind}", None)
                if handler is not None:
                    handler(mix, e, start, pan)
        audio = mix.finish(self.reverb_mix, self.reverb_time, seed=self.seed)
        return audio * float(self.o.volume)


def _pick_key(o, rng) -> int:
    key = str(getattr(o, "key", "random"))
    return key_to_pc(rng.choice(KEYS) if key == "random" else key)


class Pentatonic(SoundSet):
    name = "pentatonic"
    description = "Each bounce plays the next note of a pentatonic pattern."
    options = {
        "key": "random",  # C, D, Eb, ... or random (from the seed)
        "scale": "major",  # major | minor
        "octave": 4,
        "octaves": 2,
        "pattern": "updown",  # updown | up | down | walk | random
        "instrument": "marimba",  # marimba | glock | kalimba | pluck | chime
    }

    def setup(self) -> None:
        o = self.o
        self.root = 12 * (int(o.octave) + 1) + _pick_key(o, self.rng)
        steps = SCALES[o.scale]
        self.notes = [self.root + 12 * k + s for k in range(int(o.octaves)) for s in steps]
        self.notes.append(self.root + 12 * int(o.octaves))
        self._walk = 0

    def next_note(self, index: int, event: Event) -> int:
        n, pat = self.notes, self.o.pattern
        if pat == "up":
            return n[index % len(n)]
        if pat == "down":
            return n[::-1][index % len(n)]
        if pat == "updown":
            cycle = n + n[-2:0:-1]
            return cycle[index % len(cycle)]
        if pat == "walk":
            self._walk = max(0, min(len(n) - 1, self._walk + self.rng.choice((-2, -1, -1, 1, 1, 2))))
            return n[self._walk]
        return self.rng.choice(n)

    def final_chord(self) -> list[int]:
        return [self.root + i for i in TRIADS[self.o.scale]]


class Melody(SoundSet):
    name = "melody"
    description = "Each bounce plays the next note of a public-domain melody."
    options = {
        "melody": "random",  # see satisfy/audio/melodies.py
        "instrument": "glock",
        "transpose": 12,  # semitones (glockenspiel sounds best an octave up)
        "double": True,  # soft lower octave for body
    }
    reverb_mix = 0.25

    def setup(self) -> None:
        name = str(self.o.melody)
        if name == "random":
            name = self.rng.choice(sorted(MELODIES))
        self.melody_name = name
        self.seq = [m + int(self.o.transpose) for m in melody_midi(name)]
        info = MELODIES[name]
        tonic = key_to_pc(info["key"])
        # final chord around the melody's lowest octave
        low = min(self.seq)
        root = low - ((low - tonic) % 12)
        self.chord = [root + i for i in TRIADS[info["mode"]]]

    def next_note(self, index: int, event: Event) -> int:
        return self.seq[index % len(self.seq)]

    def on_bounce(self, mix: Mixer, e: Event, start: int, pan: float, index: int) -> None:
        vel = 0.55 + 0.45 * e.strength
        m = self.next_note(index, e)
        self.play(mix, m, start, vel, pan)
        if self.o.double:
            mix.add(synth.marimba(midi_to_hz(m - 12), 1.0), start, gain=vel * 0.35, pan=pan)

    def final_chord(self) -> list[int]:
        return self.chord


class Tactile(SoundSet):
    name = "tactile"
    description = "Dry ASMR pops and clicks, pitched to a pentatonic pattern."
    options = {
        "key": "random",
        "pitch": "scale",  # scale: follow a pentatonic pattern | size: bigger ball, deeper pop
        "octave": 4,
    }
    reverb_mix = 0.08
    reverb_time = 0.7

    def setup(self) -> None:
        root = 12 * (int(self.o.octave) + 1) + _pick_key(self.o, self.rng)
        self.root = root
        self.notes = [root + 12 * k + s for k in range(2) for s in SCALES["major"]] + [root + 24]

    def next_note(self, index: int, event: Event) -> int:
        cycle = self.notes + self.notes[-2:0:-1]
        return cycle[index % len(cycle)]

    def on_bounce(self, mix: Mixer, e: Event, start: int, pan: float, index: int) -> None:
        vel = 0.5 + 0.5 * e.strength
        if self.o.pitch == "size" and "r" in e.data:
            freq = 520.0 * (22.0 / max(8.0, float(e.data["r"]))) ** 0.45
        else:
            freq = midi_to_hz(self.next_note(index, e) - 12)
        freq *= 1.0 + self.rng.uniform(-0.015, 0.015)
        mix.add(synth.pop(freq, seed=index), start, gain=vel * 0.9, pan=pan)
        mix.add(synth.click(1.0, bright=0.9, seed=index), start, gain=vel * 0.5, pan=pan)

    def on_escape(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        mix.add(synth.pop(140, seed=1), start, gain=1.0)
        mix.add(synth.thump(60, dur=0.9), start, gain=0.8)
        mix.add(synth.noise_swell(1.2, 1500, 0.8, 0.04, 0.3, seed=self.seed, sweep=1.5), start, gain=0.25)
        rng = np.random.default_rng(self.seed)
        for i in range(70):  # confetti crackle
            dt = rng.exponential(0.25)
            if dt > 1.6:
                continue
            mix.add(synth.click(1.0, bright=rng.uniform(0.5, 1.0), seed=i), start + int(dt * SR),
                    gain=rng.uniform(0.15, 0.45) * (1 - dt / 1.6), pan=rng.uniform(-0.7, 0.7))
            if i % 6 == 0:
                mix.add(synth.pop(rng.uniform(500, 1400), seed=i), start + int(dt * SR), gain=0.25,
                        pan=rng.uniform(-0.6, 0.6))

    def on_freeze(self, mix: Mixer, e: Event, start: int, pan: float) -> None:
        mix.add(synth.thump(45, dur=1.0), start, gain=0.9)
        rng = np.random.default_rng(self.seed + 1)
        for i in range(45):  # ice crunch
            dt = rng.uniform(0, 0.35) ** 1.5
            mix.add(synth.click(1.0, bright=rng.uniform(0.7, 1.0), seed=100 + i), start + int(dt * SR),
                    gain=rng.uniform(0.2, 0.5), pan=rng.uniform(-0.5, 0.5))
        for i, m in enumerate(self.final_chord()):
            mix.add(synth.chime(midi_to_hz(m + 12), 1.0), start + int(i * 0.05 * SR), gain=0.3)

    def final_chord(self) -> list[int]:
        return [self.root + i for i in TRIADS["major"]]


class Ambient(SoundSet):
    name = "ambient"
    description = "Slow pad chords underneath; bounces ring out as soft chord-tone chimes."
    options = {
        "key": "random",
        "chord_seconds": 4.0,
        "instrument": "chime",
    }
    reverb_mix = 0.38
    reverb_time = 3.0
    # chord tones (semitones from the key root): I maj7, vi7, IV maj7, V sus
    PROGRESSION = [[0, 4, 7, 11], [-3, 0, 4, 7], [-7, -3, 0, 4], [-5, 0, 2, 7]]

    def setup(self) -> None:
        self.root = 48 + _pick_key(self.o, self.rng)  # octave 3

    def bed(self, mix: Mixer, timeline: Timeline, fps: int) -> None:
        seg = float(self.o.chord_seconds)
        end = timeline.end_t if timeline.ending != "none" else mix.n / SR
        t = 0.0
        while t < end:
            chord = self._chord_at(t)
            dur = min(seg, end - t) + 1.8
            pad = synth.pad_chord([midi_to_hz(self.root + s) for s in chord], dur, attack=1.0 if t > 0 else 0.02,
                                  release=1.8, seed=self.seed + int(t))
            mix.add(pad, int(t * SR), gain=0.22, send=0.6)
            t += seg

    def _chord_at(self, t: float) -> list[int]:
        return self.PROGRESSION[int(t // float(self.o.chord_seconds)) % len(self.PROGRESSION)]

    def on_bounce(self, mix: Mixer, e: Event, start: int, pan: float, index: int) -> None:
        vel = 0.45 + 0.4 * e.strength
        self.play(mix, self.next_note(index, e), start, vel, pan)

    def next_note(self, index: int, event: Event) -> int:
        chord = self._chord_at(event.t)
        tones = sorted({self.root + 24 + s for s in chord} | {self.root + 36 + s for s in chord[:2]})
        cycle = tones + tones[-2:0:-1]
        return cycle[index % len(cycle)]

    def final_chord(self) -> list[int]:
        return [self.root + 12 + i for i in (0, 4, 7, 11)]


SOUND_SETS: dict[str, type[SoundSet]] = {cls.name: cls for cls in (Pentatonic, Melody, Tactile, Ambient)}


def get(name: str) -> type[SoundSet]:
    if name not in SOUND_SETS:
        raise ValueError(f"Unknown sound_set '{name}'. Known: {', '.join(sorted(SOUND_SETS))}")
    return SOUND_SETS[name]


def render_audio(timeline: Timeline, sound_set: str, opts: dict, seed: int, fps: int, n_frames: int) -> np.ndarray:
    """Stereo float32 audio, exactly ``n_frames / fps`` seconds long."""
    return get(sound_set)(opts, seed).render(timeline, fps, n_frames)
