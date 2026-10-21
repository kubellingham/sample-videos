"""Public-domain melodies as pitch sequences.

Only pitches are stored: in these videos each bounce plays the next note, so
the physics supplies the rhythm. ``key`` and ``mode`` are used for the final
chord. All tunes are traditional or by composers who died well over a
century ago.
"""

from __future__ import annotations

import re

MELODIES: dict[str, dict] = {
    "ode_to_joy": {  # Beethoven, Symphony No. 9 (1824)
        "key": "C", "mode": "major",
        "notes": """
            E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4
            E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 D4 C4 C4
            D4 D4 E4 C4 D4 E4 F4 E4 C4 D4 E4 F4 E4 D4 C4 D4 G3
            E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 D4 C4 C4
        """,
    },
    "fur_elise": {  # Beethoven, Bagatelle No. 25 (1810)
        "key": "A", "mode": "minor",
        "notes": """
            E5 D#5 E5 D#5 E5 B4 D5 C5 A4  C4 E4 A4 B4  E4 G#4 B4 C5
            E4 E5 D#5 E5 D#5 E5 B4 D5 C5 A4  C4 E4 A4 B4  E4 C5 B4 A4
        """,
    },
    "twinkle": {  # traditional
        "key": "C", "mode": "major",
        "notes": """
            C4 C4 G4 G4 A4 A4 G4  F4 F4 E4 E4 D4 D4 C4
            G4 G4 F4 F4 E4 E4 D4  G4 G4 F4 F4 E4 E4 D4
            C4 C4 G4 G4 A4 A4 G4  F4 F4 E4 E4 D4 D4 C4
        """,
    },
    "greensleeves": {  # traditional English, 16th century
        "key": "A", "mode": "minor",
        "notes": """
            A4 C5 D5 E5 F5 E5 D5 B4 G4 A4 B4 C5 A4 A4 G#4 A4 B4 G#4 E4
            A4 C5 D5 E5 F5 E5 D5 B4 G4 A4 B4 C5 B4 A4 G#4 F#4 G#4 A4 A4
            G5 G5 F#5 E5 D5 B4 G4 A4 B4 C5 A4 A4 G#4 A4 B4 G#4 E4
            G5 G5 F#5 E5 D5 B4 G4 A4 B4 C5 B4 A4 G#4 F#4 G#4 A4 A4
        """,
    },
    "canon_in_d": {  # Pachelbel (c. 1680), opening violin line
        "key": "D", "mode": "major",
        "notes": """
            F#5 E5 D5 C#5 B4 A4 B4 C#5
            D5 C#5 B4 A4 G4 F#4 G4 E4
        """,
    },
    "frere_jacques": {  # traditional French
        "key": "C", "mode": "major",
        "notes": """
            C4 D4 E4 C4 C4 D4 E4 C4  E4 F4 G4 E4 F4 G4
            G4 A4 G4 F4 E4 C4 G4 A4 G4 F4 E4 C4  C4 G3 C4 C4 G3 C4
        """,
    },
    "brahms_lullaby": {  # Brahms, Wiegenlied Op. 49 No. 4 (1868)
        "key": "C", "mode": "major",
        "notes": """
            E4 E4 G4 E4 E4 G4 E4 G4 C5 B4 A4 A4 G4
            D4 E4 F4 D4 D4 E4 F4 D4 F4 B4 A4 G4 B4 C5
            C4 C4 C5 A4 F4 G4 E4 C4 F4 G4 A4 G4
            C4 C4 C5 A4 F4 G4 E4 C4 F4 E4 D4 C4
        """,
    },
    "mountain_king": {  # Grieg, Peer Gynt (1875)
        "key": "B", "mode": "minor",
        "notes": """
            B3 C#4 D4 E4 F#4 D4 F#4  F4 C#4 F4  E4 C4 E4
            B3 C#4 D4 E4 F#4 D4 F#4 B4  A4 F#4 D4 F#4 A4
        """,
    },
    "amazing_grace": {  # "New Britain" tune (1829)
        "key": "G", "mode": "major",
        "notes": """
            D4 G4 B4 G4 B4 A4 G4 E4 D4
            D4 G4 B4 G4 B4 A4 D5
            B4 D5 B4 D5 B4 G4 D4 E4 G4 G4 E4 D4
            D4 G4 B4 G4 B4 A4 G4
        """,
    },
    "mary_lamb": {  # traditional
        "key": "C", "mode": "major",
        "notes": """
            E4 D4 C4 D4 E4 E4 E4  D4 D4 D4  E4 G4 G4
            E4 D4 C4 D4 E4 E4 E4 E4 D4 D4 E4 D4 C4
        """,
    },
}

_NOTE_RE = re.compile(r"^([A-Ga-g])([#b]?)(-?\d)$")
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_to_midi(name: str) -> int:
    m = _NOTE_RE.match(name.strip())
    if not m:
        raise ValueError(f"Bad note name '{name}'")
    letter, acc, octave = m.groups()
    pc = _PC[letter.upper()] + (1 if acc == "#" else -1 if acc == "b" else 0)
    return 12 * (int(octave) + 1) + pc


def key_to_pc(key: str) -> int:
    key = key.strip()
    pc = _PC[key[0].upper()]
    if len(key) > 1:
        pc += 1 if key[1] == "#" else -1 if key[1] == "b" else 0
    return pc % 12


def midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def melody_midi(name: str) -> list[int]:
    if name not in MELODIES:
        raise ValueError(f"Unknown melody '{name}'. Known: {', '.join(sorted(MELODIES))}")
    return [note_to_midi(n) for n in MELODIES[name]["notes"].split()]
