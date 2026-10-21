"""Base class for format plugins.

A format is one module in ``satisfy/formats/`` containing a subclass of
:class:`Simulation` with a unique ``name``. It is discovered automatically.

Clocks. Physics always advances in fixed steps of ``1 / PHYSICS_HZ`` seconds,
whatever the output frame rate. Video time normally advances by the same
amount, but a format can return more than one *video tick* per step from
:meth:`Simulation.video_ticks` to play a stretch in slow motion (4 ticks =
quarter speed). Frames are sampled on the video clock, so a 15 fps preview
and a 60 fps final of a seed are the same video at different sample rates.

Subclasses implement:

* ``setup()`` - build the world (``self.p`` holds merged params, ``self.rng``
  is a seeded ``random.Random``, ``self.plan`` is the rehearsal timeline or
  ``None`` while rehearsing).
* ``step(dt)`` - advance the world by ``dt``. Call ``self.emit(...)`` for
  anything audible and ``self.finish(kind)`` once the video should end.
* ``snapshot()`` - return a :class:`~satisfy.scene.Scene` for the current
  state, without changing it.
* optionally ``on_timeout()`` (default ``finish("timeout")``), ``video_ticks()``
  (slow motion) and the ``preroll`` class attribute (seconds simulated before
  frame 0, so the video opens mid-motion).

Rehearsal and pacing: :meth:`Simulation.simulate` first dry-runs the seed
(no frames, well under a second). With ``tries`` > 1 it tries up to that many
physics variants and keeps the first whose ending lands inside
``pace_window`` (fractions of the duration). Motion is never scripted; pacing
only chooses among honest runs, deterministically. The chosen rehearsal is
handed to the real run as ``self.plan`` so a format can anticipate what is
coming (for example, slow motion just before an escape). Rehearsal and real
run share the exact same physics.

Loop. The ending holds for ``outro`` seconds, then the renderer crossfades
for ``loop_xfade`` seconds into frame 0, so the video loops seamlessly.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from types import SimpleNamespace

from ..config import VideoConfig
from ..palettes import Palette
from ..scene import Scene
from ..timeline import Event, Timeline

PHYSICS_HZ = 240
# Video ticks per frame at the lowest supported frame rate (15 fps). Slow motion
# may only begin on a multiple of this, so every frame boundary of every
# supported rate (15/30/60 fps) is hit exactly and an event is never drawn a
# frame before its sound.
FRAME_GRID = PHYSICS_HZ // 15


@dataclass
class SimResult:
    frames: list[Scene]
    timeline: Timeline
    fps: int
    prelude: list[Scene] = field(default_factory=list)  # pre-roll frames before frame 0 (for trails)
    loop_start: int | None = None  # first frame of the loop crossfade into frame 0

    @property
    def n_frames(self) -> int:
        return len(self.frames)

    @property
    def length(self) -> float:
        return self.n_frames / self.fps


BASE_DEFAULTS = {
    "outro": 1.5,  # seconds the ending (and its label) holds
    "loop_xfade": 0.5,  # then crossfade into frame 0 for a seamless loop (0 = off)
    "tries": 8,  # pacing: physics variants to try per seed (1 = off)
    "pace_window": [0.55, 1.0],  # pacing: preferred video length, as fractions of the duration
}


class Simulation:
    name: str = ""
    description: str = ""
    defaults: dict = {}
    preroll: float = 0.0  # seconds simulated before frame 0
    default_duration: float | None = None  # used when a config leaves out `duration`

    @classmethod
    def all_defaults(cls) -> dict:
        return {**BASE_DEFAULTS, **cls.defaults}

    def __init__(self, cfg: VideoConfig, palette: Palette, variant: int = 0):
        known = self.all_defaults()
        unknown = set(cfg.params) - set(known)
        if unknown:
            raise ValueError(
                f"Unknown params for format '{self.name}': {', '.join(sorted(unknown))}. "
                f"Known: {', '.join(sorted(known))}"
            )
        merged = {**known, **cfg.params}
        self.p = SimpleNamespace(**merged)
        self.cfg = cfg
        self.palette = palette
        self.variant = variant
        self.rng = random.Random(f"{self.name}:{cfg.seed}:{variant}")
        self.timeline = Timeline()
        self.plan: Timeline | None = None  # rehearsal of this exact run (None while rehearsing)
        self.outro = float(self.p.outro)
        self.loop_xfade = max(0.0, float(self.p.loop_xfade))
        self.tail = self.outro + self.loop_xfade
        self.hold_timeout = False  # set while something must finish first (e.g. slow motion)
        pre = round(self.preroll * PHYSICS_HZ)
        self._step = -pre  # physics steps since t=0
        self._ticks = -pre  # video ticks since t=0
        self.t = self.t_next = -pre / PHYSICS_HZ  # physics time (now / end of current step)
        self.vt = self.vt_next = self.t  # video time (now / end of current step)
        self.end_t: float | None = None  # physics time of the ending
        self.end_vt: float | None = None  # video time of the ending
        self._slow_prev = 1

    # -- entry point -------------------------------------------------------
    @classmethod
    def simulate(cls, cfg: VideoConfig, palette: Palette, fps: int) -> SimResult:
        """Rehearse (and pace) the seed, then simulate the chosen run at ``fps``."""
        variant, rehearsal = cls.pick_variant(cfg, palette)
        sim = cls(cfg, palette, variant=variant)
        sim.plan = rehearsal
        result = sim.run(fps)
        result.timeline.variant = variant
        return result

    @classmethod
    def pick_variant(cls, cfg: VideoConfig, palette: Palette) -> tuple[int, Timeline]:
        """Return ``(variant, rehearsal timeline)`` for the best-paced variant."""
        p = {**cls.all_defaults(), **cfg.params}
        tries = max(1, int(p["tries"]))
        lo, hi = (float(v) * cfg.duration for v in p["pace_window"])
        best = None
        best_err = float("inf")
        for variant in range(tries):
            tl = cls(cfg, palette, variant=variant).run(fps=None)
            if tries == 1:
                return variant, tl
            if tl.ending == "timeout":
                err = 1e6 + variant  # acceptable, but any natural ending is better
            else:
                err = max(0.0, lo - tl.length, tl.length - hi)
            if err == 0.0:
                return variant, tl
            if err < best_err:
                best, best_err = (variant, tl), err
        return best

    # -- API for subclasses ------------------------------------------------
    def setup(self) -> None:
        raise NotImplementedError

    def step(self, dt: float) -> None:
        raise NotImplementedError

    def snapshot(self) -> Scene:
        raise NotImplementedError

    def on_timeout(self) -> None:
        self.finish("timeout")

    def video_ticks(self) -> int:
        """Video ticks for the step about to run: 1 = real time, 4 = quarter
        speed. Use a divisor of FRAME_GRID; above PHYSICS_HZ / fps (4 at 60 fps)
        frames repeat. Slow motion starts on the frame grid: a request made off
        the grid runs at real time until the next grid tick (see
        ``slow_start_step``)."""
        return 1

    @staticmethod
    def slow_start_step(step: int) -> int:
        """First physics step at or after ``step`` where slow motion may begin
        (valid while video and physics time still agree, i.e. before any slow motion)."""
        return -(-step // FRAME_GRID) * FRAME_GRID

    def emit(self, kind: str, x: float = 0.0, y: float = 0.0, strength: float = 1.0, **data) -> Event:
        """Record an audible event at the end of the current step (video time)."""
        return self.timeline.add(Event(t=self.vt_next, kind=kind, x=x, y=y, strength=strength, data=data))

    def finish(self, kind: str) -> None:
        """End the video: the ending holds for ``outro`` seconds, then loops back."""
        if self.end_t is None:
            self.end_t = self.t_next
            self.end_vt = self.vt_next
            self.timeline.ending = kind

    @property
    def ended(self) -> bool:
        return self.end_t is not None

    # -- driver ------------------------------------------------------------
    def run(self, fps: int | None):
        """Simulate to the end. With ``fps`` set, record one scene per frame and
        return a :class:`SimResult`; with ``fps=None`` (rehearsal) record
        nothing and return just the :class:`Timeline`."""
        hz = PHYSICS_HZ
        dt = 1.0 / hz
        max_t = float(self.cfg.duration)
        self.setup()
        frames: list[Scene] = []
        prelude: list[Scene] = []
        n = math.ceil(self._ticks * fps / hz) if fps else 0  # next frame index (< 0: pre-roll)

        while True:
            if self.end_t is None and not self.hold_timeout and self.vt >= max_t - self.tail - 1e-9:
                self.t_next, self.vt_next = self.t, self.vt
                self.on_timeout()
                if self.end_t is None:
                    self.finish("timeout")
            stop_at = (self.end_vt + self.tail) if self.end_vt is not None else max_t
            # The frame at exactly the stop time is not part of the video.
            if self._ticks > 0 and self.vt >= min(stop_at, max_t) - 1e-9:
                break
            if fps is None and self.end_t is not None:
                break  # rehearsal: the ending is all we need
            if fps is not None:
                while n * hz <= self._ticks * fps:  # frame n shows video time n / fps
                    (frames if n >= 0 else prelude).append(self.snapshot())
                    n += 1
            ticks = max(1, int(self.video_ticks()))
            if ticks > 1 and FRAME_GRID % ticks:
                raise ValueError(f"video_ticks() must divide {FRAME_GRID}, got {ticks}")
            if ticks > 1 and self._ticks % FRAME_GRID and self._slow_prev == 1:
                ticks = 1  # wait for the frame grid before slowing down
            self._slow_prev = ticks
            self.t_next = (self._step + 1) / hz
            self.vt_next = (self._ticks + ticks) / hz
            self.step(dt)
            self._step += 1
            self._ticks += ticks
            self.t, self.vt = self.t_next, self.vt_next

        tl = self.timeline
        tl.end_t = self.end_vt if self.end_vt is not None else self.vt
        if fps is None:
            tl.length = min(max_t, tl.end_t + self.tail)
            return tl
        n_frames = len(frames)
        xfade = round(self.loop_xfade * fps)
        loop_start = n_frames
        if xfade > 0:
            loop_start = min(math.ceil((tl.end_t + self.outro) * fps - 1e-9), n_frames - xfade)
            loop_start = max(1, loop_start)
        tl.length = n_frames / fps
        tl.loop_t = loop_start / fps
        return SimResult(frames=frames, timeline=tl, fps=fps, prelude=prelude, loop_start=loop_start)
