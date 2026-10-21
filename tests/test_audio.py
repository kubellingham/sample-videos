import numpy as np
import pytest

from satisfy.audio import SOUND_SETS, SR, render_audio
from satisfy.audio.melodies import MELODIES, melody_midi
from satisfy.timeline import Event, Timeline, frame_of


def _timeline(*events, length=4.0):
    tl = Timeline(events=list(events), ending="trapped", end_t=length - 1.0, length=length)
    return tl


def test_audio_length_is_exactly_the_video_length():
    for fps, n_frames in ((60, 241), (15, 61)):
        audio = render_audio(_timeline(Event(0.5, "bounce")), "pentatonic", {}, 1, fps, n_frames)
        assert audio.shape == (2, n_frames * SR // fps)


@pytest.mark.parametrize("fps", [15, 60])
def test_note_starts_on_its_frame(fps):
    t = 1.2345
    audio = render_audio(_timeline(Event(t, "bounce", strength=1.0)), "pentatonic", {}, 1, fps, 4 * fps)
    start = frame_of(t, fps) * SR // fps
    mono = np.abs(audio).max(axis=0)
    assert mono[: start - 1].max() < 1e-3  # silence before the frame
    assert mono[start : start + 96].max() > 0.05  # sound within 2 ms of it


@pytest.mark.parametrize("name", sorted(SOUND_SETS))
def test_every_sound_set_handles_every_event(name):
    events = [Event(0.2 + 0.11 * i, "bounce", x=300 + 40 * i, strength=0.7, data={"r": 20 + i}) for i in range(12)]
    events += [Event(0.5, "spawn", data={"count": 3}), Event(0.6, "hit", strength=0.5), Event(1.2, "sealed"),
               Event(2.0, "escape", x=700), Event(2.5, "freeze")]
    audio = render_audio(_timeline(*events), name, {}, 3, 60, 240)
    assert np.isfinite(audio).all()
    assert 0.05 < np.abs(audio).max() <= 1.0


def test_unknown_sound_option_is_rejected():
    with pytest.raises(ValueError, match="Unknown options"):
        render_audio(_timeline(), "melody", {"tempo": 3}, 1, 60, 60)


def test_melodies_parse():
    for name in MELODIES:
        notes = melody_midi(name)
        assert len(notes) >= 8 and all(40 <= n <= 90 for n in notes)
