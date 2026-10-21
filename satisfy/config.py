"""Video config (YAML) and render quality presets."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

KNOWN_KEYS = {"format", "seed", "palette", "sound_set", "duration", "hook_text", "sound", "params", "name"}


@dataclass
class VideoConfig:
    format: str
    seed: int = 1
    palette: str | dict = "neon"
    sound_set: str = "pentatonic"
    duration: float = 28.0  # maximum length; the video may end earlier
    hook_text: str = ""
    sound: dict = field(default_factory=dict)  # options for the sound set
    params: dict = field(default_factory=dict)  # options for the format
    name: str = ""  # used for output file names; defaults to the YAML stem

    def with_seed(self, seed: int) -> "VideoConfig":
        return replace(copy.deepcopy(self), seed=int(seed))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "format": self.format,
            "seed": self.seed,
            "palette": self.palette,
            "sound_set": self.sound_set,
            "duration": self.duration,
            "hook_text": self.hook_text,
            "sound": self.sound,
            "params": self.params,
        }

    @property
    def slug(self) -> str:
        return f"{self.name or self.format}_seed{self.seed}"


def from_dict(d: dict, name: str = "") -> VideoConfig:
    unknown = set(d) - KNOWN_KEYS
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
    if "format" not in d:
        raise ValueError("Config needs a 'format'")
    cfg = VideoConfig(
        format=str(d["format"]),
        seed=int(d.get("seed", 1)),
        palette=d.get("palette", "neon"),
        sound_set=str(d.get("sound_set", "pentatonic")),
        duration=float(d.get("duration", 28.0)),
        hook_text=str(d.get("hook_text") or ""),
        sound=dict(d.get("sound") or {}),
        params=dict(d.get("params") or {}),
        name=str(d.get("name") or name),
    )
    if not 3.0 <= cfg.duration <= 180.0:
        raise ValueError("duration must be between 3 and 180 seconds")
    return cfg


def load(path: str | Path) -> VideoConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    return from_dict(data, name=path.stem)


@dataclass(frozen=True)
class Quality:
    name: str
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str


# 48 kHz audio divides evenly by every fps used here, so each frame maps to
# an exact whole number of audio samples.
FINAL = Quality("final", 1080, 1920, 60, 17, "slow", "256k")
PREVIEW = Quality("preview", 360, 640, 15, 30, "veryfast", "96k")
QUALITIES = {q.name: q for q in (FINAL, PREVIEW)}
