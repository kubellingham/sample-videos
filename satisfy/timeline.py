"""Event timeline produced by a simulation and consumed by the audio synth."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Event:
    """Something audible happened.

    ``t`` is the simulation time (seconds) at which the event's result is first
    visible. Use :func:`frame_of` to map it onto a render frame.
    """

    t: float
    kind: str  # bounce | spawn | hit | sealed | escape | freeze | ...
    x: float = 0.0
    y: float = 0.0
    strength: float = 1.0  # 0..1, loudness hint
    data: dict = field(default_factory=dict)


def frame_of(t: float, fps: int) -> int:
    """First frame whose snapshot already shows an event that happened at ``t``."""
    return max(0, math.ceil(t * fps - 1e-9))


@dataclass
class Timeline:
    events: list[Event] = field(default_factory=list)
    ending: str = "none"  # escape | trapped | timeout
    end_t: float = 0.0  # time of the ending event
    length: float = 0.0  # total video length in seconds
    variant: int = 0  # physics variant chosen by pacing

    def add(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def of_kind(self, *kinds: str) -> list[Event]:
        return [e for e in self.events if e.kind in kinds]

    def counts(self) -> dict[str, int]:
        return dict(Counter(e.kind for e in self.events))

    def to_dict(self) -> dict:
        return {
            "ending": self.ending,
            "end_t": round(self.end_t, 4),
            "length": round(self.length, 4),
            "variant": self.variant,
            "counts": self.counts(),
            "events": [
                {**asdict(e), "t": round(e.t, 6), "x": round(e.x, 2), "y": round(e.y, 2),
                 "strength": round(e.strength, 4)}
                for e in self.events
            ],
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        d = json.loads(Path(path).read_text())
        tl = cls(ending=d["ending"], end_t=d["end_t"], length=d["length"], variant=d.get("variant", 0))
        tl.events = [Event(**e) for e in d["events"]]
        return tl
