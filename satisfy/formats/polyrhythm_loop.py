"""polyrhythm_loop: concentric rings, one dot each, all realigning at the end of the cycle.

Ring ``i`` (0 = outermost) carries one dot that completes ``base + i`` laps per
cycle, so every dot is back at the top at exactly the end of the cycle. Each
time a dot crosses the top line it plays a note (ring index -> pentatonic
degree, outer rings lowest) and its ring flashes. Along the way the dots line up
into spokes at simple fractions of the cycle: at 1/2 they split into a top and a
bottom line, at 1/3 three spokes, and so on.

The video is exactly one cycle (``cycle`` defaults to the video duration) and
loops with no crossfade:

* Motion is closed-form, not simulated: positions, flashes and ripples are pure
  functions of the time modulo the cycle, so the frame after the last one is
  exactly frame 0 (and the pre-roll before frame 0 is exactly the end of the
  cycle, which is where frame 0's trails come from).
* Notes are placed at the exact crossing instants (sample-accurate, not snapped
  to frames), so the rhythm stays exact at any frame rate.
* The timeline is marked periodic: the audio is rendered over two cycles and
  the second copy is kept, so the start carries the reverb tails of the end.
"""

from __future__ import annotations

import math

from ..palettes import mix
from ..scene import Arc, Circle, Line, Ripple, Scene
from ..timeline import Event
from .base import Simulation

WHITE = (255, 255, 255)


def _ease_out(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return 1.0 - (1.0 - k) ** 3


class PolyrhythmLoop(Simulation):
    name = "polyrhythm_loop"
    description = "Concentric rings, one dot each lapping base+i times per cycle; every top crossing is a note. Loops perfectly."
    default_duration = 45.0
    preroll = 0.4  # longer than the trail: frame 0's trails come from the end of the cycle
    defaults = {
        "rings": 12,
        "base": 10,  # laps per cycle of ring 0 (outermost); ring i does base + i
        "cycle": None,  # seconds per cycle; None = the video duration (the video is exactly one cycle)
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
        self.n = int(p.rings)
        if self.n < 1:
            raise ValueError("polyrhythm_loop: rings must be at least 1")
        if p.direction not in ("cw", "ccw"):
            raise ValueError("polyrhythm_loop: direction must be 'cw' or 'ccw'")
        self.cycle = float(p.cycle) if p.cycle else float(self.cfg.duration)
        self.cx, self.cy = float(p.center[0]), float(p.center[1])
        span = (float(p.outer_radius) - float(p.inner_radius)) / max(1, self.n - 1)
        self.radii = [float(p.outer_radius) - i * span for i in range(self.n)]
        self.laps = [int(p.base) + i for i in range(self.n)]
        if min(self.laps) < 1:
            raise ValueError("polyrhythm_loop: base must be at least 1")
        self.sign = 1.0 if p.direction == "cw" else -1.0
        accents = self.palette.accents
        self.colors = []
        for i in range(self.n):  # a gradient through the palette, outer to inner
            x = i / max(1, self.n - 1) * (len(accents) - 1)
            j = min(int(x), len(accents) - 2) if len(accents) > 1 else 0
            self.colors.append(mix(accents[j], accents[min(j + 1, len(accents) - 1)], x - j))

        tl = self.timeline
        tl.periodic = abs(self.cycle - float(self.cfg.duration)) < 1e-9
        tl.audio_timing = "exact"
        notes = []
        for i, (r, laps) in enumerate(zip(self.radii, self.laps)):
            pan = (-1) ** i * (0.1 + 0.3 * i / max(1, self.n - 1))  # alternate sides, wider for higher notes
            for k in range(laps):  # crossings of the top line within one cycle: t = k * cycle / laps
                notes.append(Event(t=k * self.cycle / laps, kind="note", x=self.cx, y=self.cy - r, strength=0.75,
                                   data={"ring": i, "degree": i, "pan": round(pan, 3)}))
        tl.events.extend(sorted(notes, key=lambda e: (e.t, e.data["ring"])))

    def step(self, dt: float) -> None:
        pass  # closed-form motion: everything is a function of time

    def on_timeout(self) -> None:
        self.finish("loop")

    def _phase(self, i: int, t: float) -> float:
        """Laps completed by ring ``i``'s dot since its last top crossing, in [0, 1)."""
        f = (self.laps[i] * (t % self.cycle) / self.cycle) % 1.0
        return 0.0 if f > 1.0 - 1e-9 else f

    def snapshot(self) -> Scene:
        """A pure function of time modulo the cycle (frame N == frame 0)."""
        p, t = self.p, self.t
        items: list = []
        top = self.cy - self.radii[0] - 46
        flashes = []
        for i in range(self.n):
            age = self._phase(i, t) * self.cycle / self.laps[i]  # seconds since its last crossing
            flashes.append((age, math.exp(-age / float(p.flash))))
        # the top line, brightening a little whenever anything crosses it
        line_glow = max(f for _, f in flashes)
        items.append(Line(self.cx, top, self.cx, self.cy - self.radii[-1] + 30, 2.5, WHITE,
                          alpha=0.28 + 0.5 * line_glow, glow=0.6 * line_glow))
        for i, (r, color) in enumerate(zip(self.radii, self.colors)):
            age, flash = flashes[i]
            ring_color = mix(color, WHITE, 0.45 * flash)
            items.append(Arc(self.cx, self.cy, r, float(p.ring_width) * (1 + 1.4 * flash), 0, 360, ring_color,
                             alpha=0.26 + 0.74 * flash, glow=0.9 * flash))
            if age < 0.5:  # ripple from the crossing point
                k = age / 0.5
                items.append(Ripple(self.cx, self.cy - r, 6 + 46 * _ease_out(k), 3.5 * (1 - 0.7 * k), color,
                                    alpha=0.8 * (1 - k) ** 2))
        for i, (r, color) in enumerate(zip(self.radii, self.colors)):
            age, flash = flashes[i]
            ang = -math.pi / 2 + self.sign * 2 * math.pi * self._phase(i, t)
            items.append(Circle(self.cx + r * math.cos(ang), self.cy + r * math.sin(ang),
                                float(p.dot_radius) * (1 + 0.45 * flash), mix(color, WHITE, 0.3 * flash),
                                glow=0.55 + 0.45 * flash, id=i, trail=float(p.trail)))
        return Scene(items=items)
