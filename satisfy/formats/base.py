"""Base class for format plugins.

A format is one module in ``satisfy/formats/`` containing a subclass of
:class:`Simulation` with a unique ``name``. It is discovered automatically.

The base class owns the clock: physics advances in fixed steps of
``1 / PHYSICS_HZ`` seconds regardless of the output frame rate, and frames are
sampled from that clock. A 15 fps preview and a 60 fps final of the same seed
are therefore the same video at different sample rates.

Subclasses implement:

* ``setup()`` - build the world (``self.p`` holds merged params, ``self.rng``
  is a seeded ``random.Random``).
* ``step(dt)`` - advance the world by ``dt``. Call ``self.emit(...)`` for
  anything audible and ``self.finish(kind)`` once the video should end.
* ``snapshot()`` - return a :class:`~satisfy.scene.Scene` for the current state.
* optionally ``on_timeout()`` - called when the duration is about to run out
  without an ending (default: ``finish("timeout")``).

Pacing: :meth:`Simulation.simulate` dry-runs up to ``tries`` physics variants
of the seed (cheap, no frames recorded) and keeps the first whose natural
ending lands inside ``pace_window`` (fractions of the duration). The motion is
never scripted; pacing only chooses among honest runs, deterministically.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from types import SimpleNamespace

from ..config import VideoConfig
from ..palettes import Palette
from ..scene import Scene
from ..timeline import Event, Timeline

PHYSICS_HZ = 240


@dataclass
class SimResult:
    frames: list[Scene]
    timeline: Timeline
    fps: int

    @property
    def n_frames(self) -> int:
        return len(self.frames)

    @property
    def length(self) -> float:
        return self.n_frames / self.fps


BASE_DEFAULTS = {
    "outro": 2.0,  # seconds kept after the ending event
    "tries": 8,  # pacing: physics variants to try per seed (1 = off)
    "pace_window": [0.55, 1.0],  # pacing: preferred ending time, as fractions of the duration
}


class Simulation:
    name: str = ""
    description: str = ""
    defaults: dict = {}

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
        self.t = 0.0  # current simulation time
        self.t_next = 0.0  # time at the end of the step being computed
        self.end_t: float | None = None
        self.outro = float(self.p.outro)

    # -- entry point -------------------------------------------------------
    @classmethod
    def simulate(cls, cfg: VideoConfig, palette: Palette, fps: int) -> SimResult:
        """Pick a well-paced variant of this seed, then simulate it at ``fps``."""
        chosen = cls.pick_variant(cfg, palette)
        result = cls(cfg, palette, variant=chosen).run(fps)
        result.timeline.variant = chosen
        return result

    @classmethod
    def pick_variant(cls, cfg: VideoConfig, palette: Palette) -> int:
        p = {**cls.all_defaults(), **cfg.params}
        tries = max(1, int(p["tries"]))
        if tries == 1:
            return 0
        lo, hi = (float(v) * cfg.duration for v in p["pace_window"])
        best, best_err = 0, float("inf")
        for variant in range(tries):
            tl = cls(cfg, palette, variant=variant).run(fps=None)
            if tl.ending == "timeout":
                err = 1e6 + variant  # acceptable, but any natural ending is better
            else:
                err = max(0.0, lo - tl.length, tl.length - hi)
            if err == 0.0:
                return variant
            if err < best_err:
                best, best_err = variant, err
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

    def emit(self, kind: str, x: float = 0.0, y: float = 0.0, strength: float = 1.0, **data) -> Event:
        """Record an audible event at the end of the current step."""
        return self.timeline.add(Event(t=self.t_next, kind=kind, x=x, y=y, strength=strength, data=data))

    def finish(self, kind: str) -> None:
        """End the video: keep simulating for ``self.outro`` seconds, then stop."""
        if self.end_t is None:
            self.end_t = self.t_next
            self.timeline.ending = kind

    @property
    def ended(self) -> bool:
        return self.end_t is not None

    # -- driver ------------------------------------------------------------
    def run(self, fps: int | None):
        """Simulate to the end. With ``fps`` set, record one scene per frame and
        return a :class:`SimResult`; with ``fps=None`` (dry run for pacing),
        record nothing and return just the :class:`Timeline`."""
        self.setup()
        hz = PHYSICS_HZ
        dt = 1.0 / hz
        max_t = float(self.cfg.duration)
        frames: list[Scene] = []
        step = 0
        n = 0  # next frame index

        while True:
            self.t = step / hz
            if self.end_t is None and self.t >= max_t - self.outro - 1e-9:
                self.t_next = self.t
                self.on_timeout()
                if self.end_t is None:
                    self.finish("timeout")
            stop_at = (self.end_t + self.outro) if self.end_t is not None else max_t
            # The frame at exactly the stop time is not part of the video.
            if step > 0 and self.t >= min(stop_at, max_t) - 1e-9:
                break
            if fps is None and self.end_t is not None:
                break  # dry run: the ending time is all we need
            if fps is not None:
                while round(n * hz / fps) <= step:
                    frames.append(self.snapshot())
                    n += 1
            self.t_next = (step + 1) / hz
            self.step(dt)
            step += 1

        tl = self.timeline
        tl.end_t = self.end_t if self.end_t is not None else self.t
        if fps is None:
            tl.length = min(max_t, tl.end_t + self.outro)
            return tl
        tl.length = len(frames) / fps
        return SimResult(frames=frames, timeline=tl, fps=fps)
