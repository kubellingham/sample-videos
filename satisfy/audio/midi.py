"""Standard MIDI files: a small dependency-free reader, and a writer (used by
the tests and to make the example files).

A :class:`Score` is the list of notes with onsets and durations in seconds
(tempo changes applied), plus the bar lines and the initial tempo and time
signature. The drum channel (MIDI channel 10) is skipped.
"""

from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Note:
    t: float  # onset (s)
    dur: float  # length (s)
    pitch: int  # MIDI note number (60 = C4)
    vel: int = 96  # 1..127
    channel: int = 0
    track: int = 0


@dataclass
class Score:
    notes: list[Note]
    tempo: float = 120.0  # initial tempo (quarter notes per minute)
    meter: tuple[int, int] = (4, 4)  # initial time signature
    bars: list[float] = field(default_factory=list)  # bar line times (s), from 0 to `length`
    name: str = ""

    @property
    def length(self) -> float:
        """End of the last bar that holds a note (s)."""
        return self.bars[-1] if self.bars else (max((n.t + n.dur for n in self.notes), default=0.0))

    @property
    def beats_per_bar(self) -> float:
        num, den = self.meter
        return num * 4.0 / den

    def onsets(self, tol: float = 0.02) -> list[tuple[float, list[Note]]]:
        """Notes grouped by onset: notes starting within ``tol`` seconds of the
        first note of a group sound together (a chord)."""
        groups: list[tuple[float, list[Note]]] = []
        for n in sorted(self.notes, key=lambda n: (n.t, n.pitch)):
            if groups and n.t - groups[-1][0] <= tol:
                groups[-1][1].append(n)
            else:
                groups.append((n.t, [n]))
        return groups

    def segment(self, length: float) -> "Score":
        """The score looped or cut to fill about ``length`` seconds, at bar lines:
        repeated a whole number of times when it is shorter (at least once),
        cut at the last bar line that fits when it is longer."""
        L = self.length
        if L <= 0:
            return self
        if L <= length + 1e-6:
            k = max(1, int((length + 1e-6) // L))
            notes = [replace(n, t=n.t + i * L) for i in range(k) for n in self.notes]
            bars = [b + i * L for i in range(k) for b in self.bars[:-1]] + [k * L]
            return replace(self, notes=notes, bars=bars)
        j = bisect.bisect_right(self.bars, length + 1e-6) - 1
        end = self.bars[max(1, j)]
        notes = [replace(n, dur=min(n.dur, end - n.t)) for n in self.notes if n.t < end - 1e-6]
        return replace(self, notes=notes, bars=self.bars[: max(1, j) + 1])

    def stretched(self, k: float) -> "Score":
        """Every time multiplied by ``k`` (k > 1 is slower)."""
        return replace(self, notes=[replace(n, t=n.t * k, dur=n.dur * k) for n in self.notes],
                       tempo=self.tempo / k, bars=[b * k for b in self.bars])

    def shifted(self, dt: float) -> "Score":
        return replace(self, notes=[replace(n, t=n.t + dt) for n in self.notes], bars=[b + dt for b in self.bars])


def resolve(path: str | Path) -> Path:
    """A MIDI path as given (relative to the working directory), else relative
    to the project folder (so ``assets/midi/x.mid`` works from anywhere)."""
    p = Path(path)
    if p.exists() or p.is_absolute():
        return p
    q = ROOT / p
    return q if q.exists() else p


# --------------------------------------------------------------------- reading
def _varlen(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if b < 0x80:
            return value, pos
    raise ValueError("bad variable-length number")


def load(path: str | Path, include_drums: bool = False) -> Score:
    """Read a Standard MIDI File (format 0 or 1)."""
    path = resolve(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError(f"MIDI file not found: {path}") from None
    if data[:4] != b"MThd":
        raise ValueError(f"{path}: not a Standard MIDI File")
    hlen = struct.unpack(">I", data[4:8])[0]
    _fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError(f"{path}: SMPTE time division is not supported")
    ppq = division
    pos = 8 + hlen

    tempos: list[tuple[int, int]] = []  # (tick, microseconds per quarter)
    meters: list[tuple[int, int, int]] = []  # (tick, numerator, denominator)
    raw: list[tuple[int, int, int, int, int]] = []  # (on tick, off tick, pitch, vel, channel), per track
    tracks: list[int] = []
    track = 0
    while pos + 8 <= len(data) and track < ntrks:
        cid, clen = data[pos: pos + 4], struct.unpack(">I", data[pos + 4: pos + 8])[0]
        pos += 8
        end = pos + clen
        if cid != b"MTrk":
            pos = end
            continue
        tick, status, p = 0, 0, pos
        held: dict[tuple[int, int], list[tuple[int, int]]] = {}
        while p < end:
            delta, p = _varlen(data, p)
            tick += delta
            b = data[p]
            if b >= 0x80:
                status = b
                p += 1
            elif status == 0 or status >= 0xF0:
                raise ValueError(f"{path}: running status without a status byte")
            if status == 0xFF:  # meta event
                kind = data[p]
                n, p = _varlen(data, p + 1)
                body = data[p: p + n]
                p += n
                if kind == 0x51 and n == 3:
                    tempos.append((tick, int.from_bytes(body, "big")))
                elif kind == 0x58 and n >= 2:
                    meters.append((tick, body[0], 2 ** body[1]))
                elif kind == 0x2F:
                    break
                status = 0
            elif status in (0xF0, 0xF7):  # sysex
                n, p = _varlen(data, p)
                p += n
                status = 0
            else:
                hi, ch = status & 0xF0, status & 0x0F
                size = 1 if hi in (0xC0, 0xD0) else 2
                d = data[p: p + size]
                p += size
                if hi == 0x90 and d[1] > 0:
                    held.setdefault((ch, d[0]), []).append((tick, d[1]))
                elif hi == 0x80 or hi == 0x90:
                    starts = held.get((ch, d[0]))
                    if starts:
                        on, vel = starts.pop(0)
                        raw.append((on, tick, d[0], vel, ch))
                        tracks.append(track)
        for (ch, pitch), starts in held.items():  # never released: end with the track
            for on, vel in starts:
                raw.append((on, tick, pitch, vel, ch))
                tracks.append(track)
        pos = end
        track += 1

    # tick -> seconds through the tempo map
    tempos = sorted({t: v for t, v in sorted(tempos)}.items()) or [(0, 500_000)]
    if tempos[0][0] != 0:
        tempos.insert(0, (0, 500_000))
    marks, secs = [], []
    s = 0.0
    for i, (tk, us) in enumerate(tempos):
        if i:
            s += (tk - tempos[i - 1][0]) * tempos[i - 1][1] / 1e6 / ppq
        marks.append(tk)
        secs.append(s)

    def sec(tick: int) -> float:
        i = bisect.bisect_right(marks, tick) - 1
        return secs[i] + (tick - marks[i]) * tempos[i][1] / 1e6 / ppq

    notes = []
    for (on, off, pitch, vel, ch), trk in zip(raw, tracks):
        if ch == 9 and not include_drums:
            continue
        t0 = sec(on)
        notes.append(Note(t=t0, dur=max(0.0, sec(off) - t0), pitch=pitch, vel=vel, channel=ch, track=trk))
    notes.sort(key=lambda n: (n.t, n.pitch))
    if not notes:
        raise ValueError(f"{path}: no notes")

    # bar lines from the time signatures, up to the bar that holds the last note's end
    meters = sorted({t: (a, b) for t, a, b in sorted(meters)}.items()) or [(0, (4, 4))]
    if meters[0][0] != 0:
        meters.insert(0, (0, (4, 4)))
    kept = [r for r in raw if include_drums or r[4] != 9]
    end_tick = max(off for _, off, *_ in kept)
    last_on = max(on for on, *_ in kept)
    bars_t = []
    tk, mi = 0, 0
    while True:
        bars_t.append(tk)
        if tk >= end_tick - ppq // 8 and tk > last_on:  # a release just past a bar line adds no bar
            break
        while mi + 1 < len(meters) and meters[mi + 1][0] <= tk:
            mi += 1
        num, den = meters[mi][1]
        tk += int(round(num * ppq * 4 / den))
    return Score(notes=notes, tempo=60e6 / tempos[0][1], meter=meters[0][1], bars=[sec(b) for b in bars_t],
                 name=path.stem)


# --------------------------------------------------------------------- writing
def _vl(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def write(path: str | Path, notes: list[tuple[float, float, int, int]], tempo: float = 120.0,
          meter: tuple[int, int] = (4, 4), ppq: int = 480, name: str = "") -> None:
    """Write a one-track MIDI file. ``notes``: (start beat, length in beats,
    pitch, velocity), beats being quarter notes."""
    events = []  # (tick, order, bytes): note-offs before note-ons on the same tick
    for start, length, pitch, vel in notes:
        on = int(round(start * ppq))
        off = int(round((start + length) * ppq))
        events.append((on, 1, bytes([0x90, pitch, vel])))
        events.append((off, 0, bytes([0x80, pitch, 0])))
    events.sort(key=lambda e: (e[0], e[1]))
    body = bytearray()
    if name:
        body += b"\x00\xff\x03" + _vl(len(name)) + name.encode()
    body += b"\x00\xff\x51\x03" + int(round(60e6 / tempo)).to_bytes(3, "big")
    den_pow = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}[meter[1]]
    body += b"\x00\xff\x58\x04" + bytes([meter[0], den_pow, 24, 8])
    tick = 0
    for t, _, msg in events:
        body += _vl(t - tick) + msg
        tick = t
    body += b"\x00\xff\x2f\x00"
    head = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq)
    Path(path).write_bytes(head + b"MTrk" + struct.pack(">I", len(body)) + bytes(body))
