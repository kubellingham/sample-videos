"""polyrhythm_loop: concentric rings, one dot each, all realigning at the end of the cycle.

Each ring carries one dot; every time a dot crosses the top line it plays a
note and its ring flashes. Ring 0 is the outermost and slowest (lowest notes).
The video is exactly one cycle and loops with no crossfade.

How the laps are chosen (``sync``):

* ``free``: ring ``i`` does ``base + i`` laps per cycle and the cycle is the
  video duration. Neighbouring rings drift apart and line up into spokes at
  simple fractions of the cycle.
* ``grid`` (the default with the ``harmony`` sound set): the hits land on a
  16th-note grid of the harmony's tempo, and the chord progression plays
  exactly once per cycle. The cycle is a whole number of bars (an equal
  number per chord); its length and tempo (70-90 BPM unless fixed) are picked
  so it comes closest to the requested duration, and each ring's lap count
  divides the cycle's number of 16ths, so every crossing is on the grid.
* ``midi`` (the default when the config names a MIDI file): each ring is one
  pitch of the score (lowest pitch outermost) and its dot crosses the top
  exactly on that pitch's notes. Between notes the dot moves at a smoothly
  varying speed (a monotone spline through its crossing times). The cycle is
  the score looped or cut to the duration at bar lines.

Loop: motion is closed-form in time modulo the cycle, so the frame after the
last one is exactly frame 0; the cycle is a whole number of frames at 15, 30
and 60 fps. Notes start at the exact crossing instants, and the timeline is
marked periodic so the audio wraps around (tails of the end play under the
start).
"""

from __future__ import annotations

import bisect
import math

from ..audio import harmony as harmony_mod
from ..palettes import mix
from ..scene import Arc, Circle, Line, Ripple, Scene
from ..timeline import Event
from .base import Simulation

WHITE = (255, 255, 255)
FRAME_RATE_LCM = 15  # cycle lengths are whole multiples of 1/15 s: exact frames at 15/30/60 fps


def _ease_out(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return 1.0 - (1.0 - k) ** 3


def _divisors(n: int) -> list[int]:
    return [d for d in range(1, n + 1) if n % d == 0]


def fit_grid(target: float, rings: int, n_chords: int, tempo: float | None = None, seed_tempo: float = 80.0,
             min_interval: int = 3, beats_per_bar: int = 4, laps: list[int] | None = None,
             ) -> tuple[float, int, list[int]]:
    """Tempo, bars and lap counts for a loop on a 16th-note grid.

    The cycle is ``bars`` bars (a multiple of the progression's length, so
    every chord gets the same whole number of bars) and a whole number of
    frames. A ring with ``laps`` laps hits every ``16ths / laps`` 16th notes,
    so ``laps`` must divide the cycle's 16th count, with at least
    ``min_interval`` 16ths between hits. Picks the cycle nearest ``target``
    seconds (tempo within 70-90 BPM, near ``seed_tempo``, whole BPM preferred;
    or ``tempo`` itself, nudged by under 1% to land on whole frames). With
    ``laps`` given, only cycles that all of them fit are considered."""
    lo, hi = harmony_mod.TEMPO_RANGE
    best = None
    for chord_bars in range(1, 33):
        bars = n_chords * chord_bars
        n16 = 4 * beats_per_bar * bars
        if laps:
            if any(n16 % v or n16 // v < min_interval for v in laps):
                continue
            fits = sorted(laps)
        else:
            fits = [d for d in _divisors(n16) if n16 // d >= min_interval]
        if len(fits) < rings:
            continue
        beats = beats_per_bar * bars
        # cycle = m / 15 s  <=>  tempo = 60 * beats * 15 / m
        if tempo is None:
            ms = range(math.ceil(900 * beats / hi), math.floor(900 * beats / lo) + 1)
        else:
            ms = [round(beats * 60 / tempo * FRAME_RATE_LCM)]
        for m in ms:
            if m <= 0:
                continue
            t = 900 * beats / m
            if tempo is not None and abs(t - tempo) > 0.01 * tempo:
                continue
            cycle = m / FRAME_RATE_LCM
            score = abs(cycle - target) / target + 0.004 * abs(t - (tempo or seed_tempo))
            score += 0.0 if abs(t - round(t)) < 1e-9 else 0.02
            if best is None or score < best[0] - 1e-12:
                pick = [fits[round(j * (len(fits) - 1) / max(1, rings - 1))] for j in range(rings)]
                if len(set(pick)) < rings:  # spread by rank collided: take the first ones
                    pick = fits[:rings]
                best = (score, t, bars, pick)
    what = f"laps {sorted(laps)}" if laps else f"{rings} rings"
    if best is None:
        raise ValueError(f"polyrhythm_loop: no 16th-note grid fits {what}; use fewer rings or a lower min_interval")
    cycle = 60.0 * beats_per_bar * best[2] / best[1]
    if cycle > 1.5 * target:
        raise ValueError(f"polyrhythm_loop: the shortest 16th-note cycle for {what} is {cycle:.1f}s "
                         f"(target {target:.0f}s); use fewer rings, other laps or a lower min_interval")
    return best[1], best[2], best[3]


class _Spline:
    """A ring's phase (laps since t=0) through its crossing times: monotone
    cubic Hermite, periodic with the cycle. Integer phase = at the top."""

    def __init__(self, times: list[float], cycle: float):
        self.c = cycle
        self.t = sorted(times)
        m = len(self.t)
        ext = [self.t[-1] - cycle] + self.t + [self.t[0] + cycle]
        secant = [1.0 / max(1e-6, b - a) for a, b in zip(ext[:-1], ext[1:])]  # laps per second
        # Fritsch-Carlson: mean of the neighbouring secants, limited so the dot never runs backwards.
        # A lap squeezed between two long ones keeps some speed at its ends instead of peaking in the middle.
        slope = [(secant[k] + secant[k + 1]) / 2 for k in range(m)]
        for k in range(m):  # interval k runs from crossing k to k + 1 (secant[k + 1])
            d = secant[k + 1]
            a, b = slope[k] / d, slope[(k + 1) % m] / d
            if a * a + b * b > 9.0:
                tau = 3.0 / (a * a + b * b) ** 0.5
                slope[k], slope[(k + 1) % m] = tau * a * d, tau * b * d
        self.slope = slope

    def laps(self, t: float) -> float:
        """Continuous phase: laps completed since t = 0 (negative before it)."""
        n = math.floor(t / self.c)
        frac, age = self.at(t)
        u = t - n * self.c
        k = bisect.bisect_right(self.t, u - age + 1e-6) - 1  # index of the last crossing
        return n * len(self.t) + k + frac

    def at(self, t: float) -> tuple[float, float]:
        """(fraction of the current lap in [0, 1), seconds since the last crossing)."""
        u = t % self.c
        m = len(self.t)
        k = bisect.bisect_right(self.t, u + 1e-9) - 1
        if k < 0:  # before the first crossing: the stretch from the cycle's last crossing
            k, t0, t1 = m - 1, self.t[-1] - self.c, self.t[0]
        else:
            t0 = self.t[k]
            t1 = self.t[k + 1] if k + 1 < m else self.t[0] + self.c
        h = t1 - t0
        x = (u - t0) / h
        s0, s1 = self.slope[k] * h, self.slope[(k + 1) % m] * h
        f = (x ** 3 - 2 * x ** 2 + x) * s0 + (-2 * x ** 3 + 3 * x ** 2) + (x ** 3 - x ** 2) * s1
        f = min(max(f, 0.0), 1.0)
        return (0.0 if f > 1.0 - 1e-9 else f), u - t0


class PolyrhythmLoop(Simulation):
    name = "polyrhythm_loop"
    description = "Concentric rings, one dot each; every top crossing is a note. Free, on a 16th grid, or from MIDI."
    default_duration = 45.0
    preroll = 0.4  # longer than the trail: frame 0's trails come from the end of the cycle
    defaults = {
        "rings": 12,  # midi: the most rings (pitches share rings beyond this)
        "sync": "auto",  # auto | free | grid | midi (auto: midi with a MIDI file, grid with harmony, else free)
        "base": 10,  # free: laps per cycle of ring 0 (outermost); ring i does base + i
        "laps": None,  # grid: explicit lap counts, outer to inner (each must divide the cycle's 16ths)
        "min_interval": 3,  # grid: fewest 16ths between two hits of one ring
        "cycle": None,  # seconds per cycle; None = the duration (grid/midi: the target, fitted to the music)
        "center": [540, 1000],
        "outer_radius": 405.0,  # keeps the rings clear of the Shorts UI on the right
        "inner_radius": 90.0,
        "ring_width": 3.0,
        "dot_radius": 12.0,
        "direction": "cw",  # cw | ccw
        "flash": 0.35,  # seconds: decay time of a ring's flash after its dot crosses the top
        "trail": 0.3,  # seconds
        # a loop has no ending to hold, no crossfade and nothing to pace
        "outro": 0.0,
        "loop_xfade": 0.0,
        "tries": 1,
    }

    def setup(self) -> None:
        p = self.p
        if p.direction not in ("cw", "ccw"):
            raise ValueError("polyrhythm_loop: direction must be 'cw' or 'ccw'")
        sync = str(p.sync)
        if sync == "auto":
            sync = "midi" if (self.cfg.sound or {}).get("midi") else (
                "grid" if self.cfg.sound_set == "harmony" else "free")
        if sync not in ("free", "grid", "midi"):
            raise ValueError("polyrhythm_loop: sync must be auto, free, grid or midi")
        self.sync = sync
        target = float(p.cycle) if p.cycle else float(self.cfg.duration)
        tl = self.timeline
        self.splines: list[_Spline] | None = None
        notes: list[Event] = []

        if sync == "free":
            self.n = int(p.rings)
            if self.n < 1:
                raise ValueError("polyrhythm_loop: rings must be at least 1")
            self.cycle = target
            self.laps = [int(p.base) + i for i in range(self.n)]
            if min(self.laps) < 1:
                raise ValueError("polyrhythm_loop: base must be at least 1")
            tl.periodic = abs(self.cycle - float(self.cfg.duration)) < 1e-9
        elif sync == "grid":
            h = harmony_mod.plan(self.cfg.sound or {}, self.cfg.seed)
            fixed = (self.cfg.sound or {}).get("tempo", "auto")
            fixed = None if str(fixed) == "auto" else float(fixed)
            n_chords = len(h.chords)
            given = sorted({int(v) for v in p.laps}) if p.laps else None
            self.n = len(given) if given else int(p.rings)
            tempo, bars, self.laps = fit_grid(target, self.n, n_chords, fixed, h.tempo, int(p.min_interval),
                                              laps=given)
            self.cycle = 240.0 * bars / tempo
            h = harmony_mod.progression_harmony(h.tonic, h.progression, tempo, bars / n_chords)
            h.cycle = self.cycle
            tl.music = {**h.to_dict(), "bars": bars, "sixteenths": 16 * bars}
            self.duration = self.cycle
            tl.periodic = True
        else:  # midi
            score = self.load_score()
            if score is None:
                raise ValueError("polyrhythm_loop: sync 'midi' needs `sound: {midi: path/to/file.mid}`")
            score = score.segment(target)
            frames = round(score.length * 15)
            score = score.stretched(max(1, frames) / 15 / score.length)  # whole frames (a tiny tempo change)
            self.cycle = score.length
            self.duration = self.cycle
            pitches = sorted({n.pitch for n in score.notes})
            self.n = max(1, min(int(p.rings), len(pitches)))
            ring_of = {m: k * self.n // len(pitches) for k, m in enumerate(pitches)}
            crossings: list[list[float]] = [[] for _ in range(self.n)]
            for t, group in score.onsets():
                by_ring: dict[int, list] = {}
                for nt in group:
                    by_ring.setdefault(ring_of[nt.pitch], []).append(nt)
                for i, ns in by_ring.items():
                    t = t % self.cycle
                    if not crossings[i] or t - crossings[i][-1] > 1e-3:
                        crossings[i].append(t)
                    notes.append(Event(t=t, kind="note", strength=min(1.0, max(n.vel for n in ns) / 110),
                                       data={"ring": i, "register": round(i / max(1, self.n - 1), 4),
                                             "notes": [[n.pitch, round(0.3 + 0.7 * n.vel / 127, 4)] for n in ns]}))
            self.splines = [_Spline(ts, self.cycle) for ts in crossings]
            self.laps = [len(ts) for ts in crossings]
            h = harmony_mod.from_score(score)
            tl.music = h.to_dict()
            tl.periodic = True

        self.cx, self.cy = float(p.center[0]), float(p.center[1])
        span = (float(p.outer_radius) - float(p.inner_radius)) / max(1, self.n - 1)
        self.radii = [float(p.outer_radius) - i * span for i in range(self.n)]
        self.sign = 1.0 if p.direction == "cw" else -1.0
        accents = self.palette.accents
        self.colors = []
        for i in range(self.n):  # a gradient through the palette, outer to inner
            x = i / max(1, self.n - 1) * (len(accents) - 1)
            j = min(int(x), len(accents) - 2) if len(accents) > 1 else 0
            self.colors.append(mix(accents[j], accents[min(j + 1, len(accents) - 1)], x - j))

        tl.audio_timing = "exact"
        if sync != "midi":
            for i, (r, laps) in enumerate(zip(self.radii, self.laps)):
                for k in range(laps):  # crossings of the top line within one cycle: t = k * cycle / laps
                    notes.append(Event(t=k * self.cycle / laps, kind="note", strength=0.75,
                                       data={"ring": i, "degree": i, "register": round(i / max(1, self.n - 1), 4)}))
        for e in notes:
            i = e.data["ring"]
            e.x, e.y = self.cx, self.cy - self.radii[i]
            e.data["pan"] = round((-1) ** i * (0.1 + 0.3 * i / max(1, self.n - 1)), 3)  # alternate sides
        tl.events.extend(sorted(notes, key=lambda e: (e.t, e.data["ring"])))

    def step(self, dt: float) -> None:
        pass  # closed-form motion: everything is a function of time

    def on_timeout(self) -> None:
        self.finish("loop")

    def _laps(self, i: int, t: float) -> float:
        """Continuous phase of ring ``i`` (laps since t = 0)."""
        if self.splines is not None:
            return self.splines[i].laps(t)
        return self.laps[i] * t / self.cycle

    def _path(self, i: int, t: float) -> list[tuple[float, float]]:
        """Trail behind ring ``i``'s dot along its circle (oldest first), at most 300 degrees long."""
        r, steps, span = self.radii[i], 24, float(self.p.trail)
        now = self._laps(i, t)
        pts = []
        for j in range(steps, 0, -1):
            back = now - self._laps(i, t - span * j / steps)
            if back > 0.83:
                continue
            ang = -math.pi / 2 + self.sign * 2 * math.pi * (now - back)
            pts.append((self.cx + r * math.cos(ang), self.cy + r * math.sin(ang)))
        return pts

    def _phase(self, i: int, t: float) -> tuple[float, float]:
        """(fraction of ring ``i``'s current lap in [0, 1), seconds since its last top crossing)."""
        if self.splines is not None:
            return self.splines[i].at(t)
        f = (self.laps[i] * (t % self.cycle) / self.cycle) % 1.0
        f = 0.0 if f > 1.0 - 1e-9 else f
        return f, f * self.cycle / self.laps[i]

    def snapshot(self) -> Scene:
        """A pure function of time modulo the cycle (frame N == frame 0)."""
        p, t = self.p, self.t
        items: list = []
        top = self.cy - self.radii[0] - 46
        phases = [self._phase(i, t) for i in range(self.n)]
        flashes = [math.exp(-age / float(p.flash)) for _, age in phases]
        # the top line, brightening a little whenever anything crosses it
        line_glow = max(flashes)
        items.append(Line(self.cx, top, self.cx, self.cy - self.radii[-1] + 30, 2.5, WHITE,
                          alpha=0.28 + 0.5 * line_glow, glow=0.6 * line_glow))
        for i, (r, color) in enumerate(zip(self.radii, self.colors)):
            age, flash = phases[i][1], flashes[i]
            ring_color = mix(color, WHITE, 0.45 * flash)
            items.append(Arc(self.cx, self.cy, r, float(p.ring_width) * (1 + 1.4 * flash), 0, 360, ring_color,
                             alpha=0.26 + 0.74 * flash, glow=0.9 * flash))
            if age < 0.5:  # ripple from the crossing point
                k = age / 0.5
                items.append(Ripple(self.cx, self.cy - r, 6 + 46 * _ease_out(k), 3.5 * (1 - 0.7 * k), color,
                                    alpha=0.8 * (1 - k) ** 2))
        for i, (r, color) in enumerate(zip(self.radii, self.colors)):
            frac, flash = phases[i][0], flashes[i]
            ang = -math.pi / 2 + self.sign * 2 * math.pi * frac
            items.append(Circle(self.cx + r * math.cos(ang), self.cy + r * math.sin(ang),
                                float(p.dot_radius) * (1 + 0.45 * flash), mix(color, WHITE, 0.3 * flash),
                                glow=0.55 + 0.45 * flash, id=i, trail=float(p.trail), path=self._path(i, t)))
        return Scene(items=items)
