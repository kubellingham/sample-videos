"""Harmony: the key, chord progression and tempo of one video.

A :class:`Harmony` says which chord sounds at any moment. It comes either from
a roman-numeral progression (``plan``) or from a MIDI score, whose chords are
estimated bar by bar (``from_score``). Formats use it to line motion up with
the music (``polyrhythm_loop`` fits its cycle to the tempo and progression)
and record it in the timeline (``Timeline.music``), so the sound set plays
exactly what the picture was built for.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import asdict, dataclass, field

from .melodies import key_to_pc

KEYS = ["C", "D", "E", "F", "G", "A", "Bb", "Eb"]
QUALITIES = {"maj": (0, 4, 7), "min": (0, 3, 7), "dim": (0, 3, 6)}
NUMERALS = {"i": 0, "ii": 2, "iii": 4, "iv": 5, "v": 7, "vi": 9, "vii": 11}
PC_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# curated progressions (major keys); "random" picks one from the seed
PROGRESSIONS = [
    "I-V-vi-IV",  # the pop axis
    "vi-IV-I-V",  # the same, from the minor side
    "ii-V-I",  # jazz cadence
    "I-vi-IV-V",  # fifties
    "I-IV-vi-V",
    "IV-V-iii-vi",  # royal road
    "vi-V-IV-V",
    "I-iii-vi-IV",
]
TEMPO_RANGE = (70, 90)  # BPM


@dataclass(frozen=True)
class Chord:
    t: float  # start within the cycle (s)
    root: int  # pitch class
    quality: str  # maj | min | dim
    name: str = ""

    @property
    def tones(self) -> tuple[int, ...]:
        return tuple((self.root + i) % 12 for i in QUALITIES[self.quality])


@dataclass
class Harmony:
    tonic: int  # pitch class of the key
    tempo: float  # BPM (quarter notes)
    chords: list[Chord]  # one cycle, sorted by start
    cycle: float  # seconds; the chords repeat with this period
    beats_per_bar: float = 4.0
    mode: str = "major"
    progression: str = ""
    source: str = "progression"  # progression | midi
    offset: float = 0.0  # video time where the first cycle starts
    _starts: list[float] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._starts = [c.t for c in self.chords]

    # -- time ---------------------------------------------------------------
    @property
    def beat(self) -> float:
        return 60.0 / self.tempo

    @property
    def bar(self) -> float:
        return self.beat * self.beats_per_bar

    def chord_index(self, t: float) -> int:
        u = (t - self.offset) % self.cycle
        return max(0, bisect.bisect_right(self._starts, u + 1e-9) - 1)

    def chord_at(self, t: float) -> Chord:
        return self.chords[self.chord_index(t)]

    def segments(self, t0: float, t1: float) -> list[tuple[float, float, Chord, int]]:
        """Chords sounding within [t0, t1) as (start, end, chord, index in cycle), in video time."""
        out = []
        k = math.floor((t0 - self.offset) / self.cycle)
        while True:
            base = self.offset + k * self.cycle
            if base >= t1:
                break
            for i, c in enumerate(self.chords):
                s = base + c.t
                e = base + (self.chords[i + 1].t if i + 1 < len(self.chords) else self.cycle)
                if e > t0 + 1e-9 and s < t1 - 1e-9:
                    out.append((s, e, c, i))
            k += 1
        return out

    # -- pitches --------------------------------------------------------------
    def tones(self, t: float, lo: int = 60, hi: int = 84) -> list[int]:
        """MIDI notes of the chord at ``t`` between ``lo`` and ``hi`` (inclusive)."""
        pcs = set(self.chord_at(t).tones)
        return [m for m in range(lo, hi + 1) if m % 12 in pcs]

    def final_chord(self) -> list[int]:
        """Tonic triad from the key's note in octave 4 (for endings)."""
        root = 60 + self.tonic
        return [root, root + (3 if self.mode == "minor" else 4), root + 7, root + 12]

    @property
    def key_name(self) -> str:
        return PC_NAMES[self.tonic] + ("m" if self.mode == "minor" else "")

    # -- (de)serialise --------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_starts", None)
        d["key"] = self.key_name
        d["chords"] = [{"t": round(c.t, 6), "root": c.root, "quality": c.quality, "name": c.name}
                       for c in self.chords]
        for k in ("tempo", "cycle", "offset"):
            d[k] = round(d[k], 6)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Harmony":
        return cls(tonic=int(d["tonic"]), tempo=float(d["tempo"]), cycle=float(d["cycle"]),
                   chords=[Chord(float(c["t"]), int(c["root"]), str(c["quality"]), str(c.get("name", "")))
                           for c in d["chords"]],
                   beats_per_bar=float(d.get("beats_per_bar", 4.0)), mode=str(d.get("mode", "major")),
                   progression=str(d.get("progression", "")), source=str(d.get("source", "progression")),
                   offset=float(d.get("offset", 0.0)))


# ----------------------------------------------------------------- progressions
def parse_progression(text: str) -> list[tuple[int, str, str]]:
    """``"I-V-vi-IV"`` -> [(semitones above the tonic, quality, numeral), ...].
    Upper case = major, lower case = minor, a trailing ``°``/``o``/``dim`` = diminished."""
    out = []
    for tok in text.replace(" ", "").replace(",", "-").split("-"):
        if not tok:
            continue
        core = tok.rstrip("°o").removesuffix("dim")
        dim = core != tok
        if core.lower() not in NUMERALS:
            raise ValueError(f"Bad chord '{tok}' in progression '{text}' (use roman numerals like I-V-vi-IV)")
        quality = "dim" if dim else ("maj" if core.isupper() else "min")
        out.append((NUMERALS[core.lower()], quality, tok))
    if not out:
        raise ValueError("Empty progression")
    return out


def plan(opts: dict, seed: int) -> Harmony:
    """Key, progression and tempo from the sound options and the seed.

    Options: ``key`` (C by default, or random), ``progression`` (I-V-vi-IV by
    default, any roman-numeral list, or random from :data:`PROGRESSIONS`),
    ``tempo`` (BPM, or auto: a seeded value in 70-90), ``chord_bars`` (bars
    per chord). The random draws always happen in the same order, so fixing
    one option never changes the others."""
    rng = random.Random(f"harmony:{seed}")
    r_key, r_prog, r_tempo = rng.choice(KEYS), rng.choice(PROGRESSIONS), rng.randint(*TEMPO_RANGE)
    key = str(opts.get("key", "C"))
    tonic = key_to_pc(r_key if key == "random" else key)
    prog = str(opts.get("progression", "I-V-vi-IV"))
    prog = r_prog if prog == "random" else prog
    tempo = opts.get("tempo", "auto")
    tempo = float(r_tempo if str(tempo) == "auto" else tempo)
    if not 20 <= tempo <= 300:
        raise ValueError("tempo must be between 20 and 300 BPM")
    return progression_harmony(tonic, prog, tempo, float(opts.get("chord_bars", 1)))


def progression_harmony(tonic: int, prog: str, tempo: float, chord_bars: float, beats_per_bar: float = 4.0,
                        offset: float = 0.0) -> Harmony:
    steps = parse_progression(prog)
    span = chord_bars * beats_per_bar * 60.0 / tempo
    chords = [Chord(i * span, (tonic + deg) % 12, q, name) for i, (deg, q, name) in enumerate(steps)]
    return Harmony(tonic=tonic, tempo=tempo, chords=chords, cycle=span * len(chords), beats_per_bar=beats_per_bar,
                   progression="-".join(n for *_, n in steps), offset=offset)


# ----------------------------------------------------------------- from MIDI
# Krumhansl-Kessler key profiles
_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _corr(a, b) -> float:
    ma, mb = sum(a) / 12, sum(b) / 12
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) or 1.0
    return num / den


def detect_key(notes) -> tuple[int, str]:
    hist = [0.0] * 12
    for n in notes:
        hist[n.pitch % 12] += max(0.05, n.dur)
    best = max(((_corr(hist, prof[-k:] + prof[:-k]), k, mode)
                for k in range(12) for mode, prof in (("major", _MAJOR), ("minor", _MINOR))))
    return best[1], best[2]


def _sounding(notes, t: float) -> list:
    return [n for n in notes if n.t <= t + 0.03 and t < n.t + max(n.dur, 0.05) - 0.03]


def _bass(notes, t0: float, t1: float):
    """The bass note of a stretch: the lowest note sounding at its start, else the lowest starting in it."""
    now = _sounding(notes, t0) or [n for n in notes if t0 <= n.t < t1]
    return min(now, key=lambda n: n.pitch) if now else None


def _segments(score) -> list[tuple[float, float]]:
    """Bars, split at the half bar where a bass line changes there (a note
    starting at the half bar that is the lowest sounding note, at least an
    octave under the top, on a new pitch class)."""
    bars = score.bars if len(score.bars) > 1 else [0.0, score.length]
    out = []
    for b0, b1 in zip(bars[:-1], bars[1:]):
        mid = (b0 + b1) / 2
        low0 = _bass(score.notes, b0, b1)
        now = _sounding(score.notes, mid)
        split = False
        if low0 is not None and now:
            low = min(now, key=lambda n: n.pitch)
            top = max(n.pitch for n in now)
            split = abs(low.t - mid) < 0.03 and top - low.pitch >= 12 and low.pitch % 12 != low0.pitch % 12
        out += [(b0, mid), (mid, b1)] if split else [(b0, b1)]
    return out


def from_score(score, offset: float = 0.0) -> Harmony:
    """Chords estimated from what the score plays, bar by bar (or half bar
    where the bass changes): the triad that best covers the notes, weighted
    by duration (downbeat notes count double), strongly favouring a chord
    built on the bass note; ties go to chords of the key. The cycle is the score."""
    tonic, mode = detect_key(score.notes)
    scale = {(tonic + s) % 12 for s in ((0, 2, 4, 5, 7, 9, 11) if mode == "major" else (0, 2, 3, 5, 7, 8, 10))}
    chords: list[Chord] = []
    bars = score.bars if len(score.bars) > 1 else [0.0, score.length]
    for b0, b1 in _segments(score):
        w = [0.0] * 12
        for n in score.notes:
            overlap = min(b1, n.t + max(n.dur, 0.05)) - max(b0, n.t)
            if overlap > 0:
                w[n.pitch % 12] += overlap * (2.0 if abs(n.t - b0) < 0.03 else 1.0)
        total = sum(w)
        if total <= 0:
            if chords:
                continue  # an empty bar keeps the previous chord
            w[tonic] = 1.0
            total = 1.0
        bass = _bass(score.notes, b0, b1)
        top = max((n.pitch for n in score.notes if n.t < b1 and n.t + max(n.dur, 0.05) > b0), default=0)
        # a bass only counts when it is a separate, lower voice (not just the melody's lowest note)
        bass_pc = bass.pitch % 12 if bass is not None and top - bass.pitch >= 12 else None
        best = None
        for root in range(12):
            for q in ("maj", "min"):
                tones = {(root + i) % 12 for i in QUALITIES[q]}
                score_ = sum(w[p] for p in tones) - 0.6 * sum(w[p] for p in range(12) if p not in tones)
                score_ += 0.15 * total * (tones <= scale) + 0.02 * total * (root == tonic)
                if q == ("min" if mode == "minor" else "maj") or (root - tonic) % 12 == 7:
                    score_ += 0.06 * total * ((root - tonic) % 12 in (0, 5, 7))  # I, IV, V: the usual suspects
                if bass_pc is not None:
                    score_ += 0.4 * total * (root == bass_pc) + 0.1 * total * (bass_pc in tones)
                if best is None or score_ > best[0] + 1e-9:
                    best = (score_, root, q)
        _, root, q = best
        name = PC_NAMES[root] + ("m" if q == "min" else "")
        if chords and chords[-1].root == root and chords[-1].quality == q:
            continue  # same chord: extend
        chords.append(Chord(b0 - bars[0], root, q, name))
    return Harmony(tonic=tonic, tempo=score.tempo, chords=chords, cycle=score.length - bars[0],
                   beats_per_bar=score.beats_per_bar, mode=mode, progression="-".join(c.name for c in chords),
                   source="midi", offset=offset + bars[0])
