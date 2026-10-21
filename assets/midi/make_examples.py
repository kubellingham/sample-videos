"""Writes the example MIDI files in this folder (public-domain tunes).

    python assets/midi/make_examples.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # the project folder

from satisfy.audio.melodies import note_to_midi  # noqa: E402
from satisfy.audio.midi import write  # noqa: E402

# Beethoven, "Ode to Joy" (Symphony No. 9, 1824), in C, 4/4. Bars of (note, beats).
ODE_MELODY = """
E4 1  E4 1  F4 1  G4 1  | G4 1  F4 1  E4 1  D4 1  | C4 1  C4 1  D4 1  E4 1  | E4 1.5 D4 .5 D4 2 |
E4 1  E4 1  F4 1  G4 1  | G4 1  F4 1  E4 1  D4 1  | C4 1  C4 1  D4 1  E4 1  | D4 1.5 C4 .5 C4 2 |
D4 1  D4 1  E4 1  C4 1  | D4 1  E4 .5 F4 .5 E4 1 C4 1 | D4 1 E4 .5 F4 .5 E4 1 D4 1 | C4 1 D4 1 G3 2 |
E4 1  E4 1  F4 1  G4 1  | G4 1  F4 1  E4 1  D4 1  | C4 1  C4 1  D4 1  E4 1  | D4 1.5 C4 .5 C4 2 |
"""
# a simple bass: the harmony's root, a whole or half note at a time
ODE_BASS = """
C3 4 | G2 4 | C3 4 | G2 4 | C3 4 | G2 4 | C3 4 | G2 2 C3 2 |
G2 4 | G2 2 C3 2 | G2 4 | C3 2 G2 2 | C3 4 | G2 4 | C3 4 | G2 2 C3 2 |
"""


def _line(text: str, vel: int) -> list[tuple[float, float, int, int]]:
    notes, beat = [], 0.0
    toks = text.replace("|", " ").split()
    for name, length in zip(toks[::2], toks[1::2]):
        notes.append((beat, float(length) * 0.95, note_to_midi(name), vel))
        beat += float(length)
    return notes


def main() -> None:
    notes = _line(ODE_MELODY, 100) + _line(ODE_BASS, 72)
    write(HERE / "ode_to_joy.mid", notes, tempo=100, meter=(4, 4), name="Ode to Joy")
    print("wrote", HERE / "ode_to_joy.mid")


if __name__ == "__main__":
    main()
