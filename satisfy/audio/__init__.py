"""Audio synthesis: event timeline -> stereo mix."""

from .sound_sets import SOUND_SETS, render_audio
from .synth import SR, write_wav

__all__ = ["SOUND_SETS", "SR", "render_audio", "write_wav"]
