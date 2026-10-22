# satisfy — oddly satisfying Shorts generator

Generates vertical "oddly satisfying" videos (1080×1920, 60 fps, H.264 + AAC,
sound synced to the frame) from deterministic physics simulations.

```
YAML config ──► simulation (pymunk, fixed 240 Hz clock)
                   ├─► event timeline ──► audio synth (numpy, 48 kHz) ──┐
                   └─► scene per frame ──► renderer (skia) ─────────────┴─► ffmpeg ──► MP4
```

Samples (`configs/shrinking_ring.yaml`): [`samples/shrinking_ring_seed7.mp4`](samples/shrinking_ring_seed7.mp4)
(trapped ending) and [`samples/shrinking_ring_seed10.mp4`](samples/shrinking_ring_seed10.mp4)
(escape ending), each with a contact sheet.
`configs/polyrhythm_loop.yaml`: [`samples/polyrhythm_loop_seed1.mp4`](samples/polyrhythm_loop_seed1.mp4)
(one 45 s cycle that loops with no seam) and its contact sheet.
`configs/polyrhythm_harmony.yaml`: [`samples/polyrhythm_harmony_seed1.mp4`](samples/polyrhythm_harmony_seed1.mp4)
(the harmony engine: every hit on a 16th note, I-V-vi-IV once per cycle) and its contact sheet.

**Kinetic typography** lives in its own Remotion project, [`kinetic/`](kinetic/README.md):
voiceover (or a script, voiced locally by Kokoro) → Whisper word timings →
bold type slamming in on the words, punching on the beat, with the music
ducked under the voice at -14 LUFS. Sample:
[`samples/kinetic_did-you-know.mp4`](samples/kinetic_did-you-know.mp4) and its contact sheet.

## Setup

```bash
pip install -r requirements.txt      # numpy, pymunk, PyYAML, skia-python
# plus ffmpeg on PATH (brew install ffmpeg / apt install ffmpeg)
# headless Linux also needs: apt install libegl1   (skia-python links against it)
```

## Usage

```bash
# one video at full quality -> out/final/shrinking_ring_seed7.mp4 (+ .json timeline)
python -m satisfy render configs/shrinking_ring.yaml
python -m satisfy render configs/shrinking_ring.yaml --seed 12 --preview   # fast 360x640 15fps

# curate: N seeds as 15 fps previews + an HTML gallery
python -m satisfy batch configs/shrinking_ring.yaml -n 24
python -m satisfy serve out/batch/shrinking_ring     # open the gallery, click ★ to keep
python -m satisfy final out/batch/shrinking_ring     # re-render keepers at 1080x1920 60fps -> out/final/

python -m satisfy formats    # formats and their params
python -m satisfy sounds     # sound sets, options, melodies
```

The gallery also works opened straight from disk (`out/batch/<name>/gallery.html`):
marks are kept in the browser and the footer shows a ready-to-copy
`final ... --seeds 3 7 12` command. Through `serve`, marks are written to
`keepers.json` in the batch folder and `final` picks them up. Re-running
`batch` into the same folder adds seeds to the same gallery.

## Config

```yaml
format: shrinking_ring
seed: 7
palette: neon            # neon | sunset | ocean | forest | candy | mono | aurora | ember | random
sound_set: pentatonic    # harmony | pentatonic | melody | tactile | ambient
duration: 28             # maximum length in seconds
hook_text: "Will it escape before it fills the ring?"
sound:                   # optional sound-set options
  instrument: marimba
params:                  # optional format params
  mode: grow
```

`palette` can also be an inline mapping (`base`, `bg_top`, `bg_bottom`, `glow`,
`ring`, `accents`, `text`, `text_stroke`).

`sound: {midi: path/to/file.mid}` drives the pitches from a MIDI file, in any
sound set; formats that can choreograph (`polyrhythm_loop`, `shrinking_ring`)
also put their hits on the score's note times. See [MIDI](#midi).

## How it stays deterministic and in sync

* **Seeded everything.** Each format gets a `random.Random` derived from the
  seed; pymunk is stepped with a fixed `dt`. Same config → same video.
* **Frame rate never touches physics.** The simulation always steps at
  240 Hz; frames are samples of that clock. A 15 fps preview and the 60 fps
  final of a seed are the same video, so what you keep is what you get.
* **Sample-accurate audio.** Each event is stamped with the physics time its
  result first becomes visible, mapped to that frame, and placed at
  `frame × 48000 / fps` (a whole number of samples at 15/30/60 fps). Voices
  have no pre-delay, so the transient lands on the first sample of the frame
  that shows the bounce.
* **Pacing and rehearsal.** A seed can produce an ending at any time. Before
  rendering, the format dry-runs up to `tries` physics variants of the seed
  (no frames, under a second each) and keeps the first whose ending lands in
  `pace_window` (default 55–100% of `duration`). Motion is never scripted;
  pacing only chooses among honest runs, deterministically. If no variant
  ends in time, the video ends with a freeze at `duration`. The chosen dry
  run is handed to the real run as a *rehearsal*, so a format can anticipate
  an event, such as starting slow motion exactly 0.4 s before an escape.
* **Slow motion without breaking sync.** Physics always steps at 240 Hz; a
  format can ask for more *video ticks* per step (4 = quarter speed). Events
  are stamped in video time, so sound still lands on the right frame.
* **Seamless loop.** The ending holds for `outro` seconds, then the last
  `loop_xfade` seconds crossfade into frame 0 (picture) and fade to silence
  (audio), so Shorts' auto-replay has no visible or audible seam. Formats
  whose motion is periodic (`polyrhythm_loop`) loop *exactly* instead, with
  no crossfade at all; see below.
* **Shorts safe zone.** All text stays out of the bottom 20% and the right
  12% of the frame, where the Shorts UI sits. The hook is laid out inside
  the safe area, and the renderer moves any label that would cross into it.
* **Music the picture was built for.** A format that lines its motion up with
  the music (a tempo grid, a MIDI score) records the harmony it used in the
  timeline (`music`: key, tempo, chords with their times), and the sound set
  plays exactly that, so a re-render of the audio can never drift from the picture.

## Formats

A format is one module in `satisfy/formats/` with a `Simulation` subclass;
it is discovered automatically. It only describes the world. Drawing, audio,
encoding, pacing and the CLI are shared.

```python
# satisfy/formats/my_format.py
from ..scene import Circle, Scene
from .base import Simulation

class MyFormat(Simulation):
    name = "my_format"
    description = "One line for `satisfy formats`"
    defaults = {"speed": 900.0}          # becomes `params:` in the config

    def setup(self):                     # build the world; self.p = params, self.rng = seeded RNG
        ...
    def step(self, dt):                  # advance; self.emit("bounce", x, y, strength) for sounds,
        ...                              # self.finish("escape" | "trapped") to end (hold + loop follow)
    def snapshot(self) -> Scene:         # draw the current state with scene primitives (read-only!)
        return Scene(items=[Circle(540, 960, 40, (255, 80, 120), glow=0.6)])
```

Primitives (`satisfy/scene.py`): `Circle` (glow, highlight, outline, trails by
`id` or along an explicit `path`), `Arc` (rings, gaps, flying shards), `Line`,
`Ripple`, `Particle`, `Label`, plus frame `Effects` (flash, desaturate/tint,
zoom, shake, vignette). Event kinds the sound sets understand: `bounce`,
`spawn`, `hit`, `tap` (a faint touch that is not a note), `sealed`, `escape`,
`freeze`, and `note`. A hit can name its pitch: `data["notes"]` = [[midi,
gain], ...] plays exactly those (MIDI choreography does this); `data["degree"]`
picks a scale degree; `data["register"]` (0 low … 1 high) steers the harmony
set's choice; `data["pan"]` sets the stereo position. A format can set
`default_duration` (used when a config leaves `duration` out), set
`self.duration` in `setup()` to fix the video length (e.g. to a musical
cycle), and read the config's MIDI score with `self.load_score()`.

### shrinking_ring

A ball bounces inside a slowly rotating, shrinking ring with a gap (the
ring spans ~85% of the frame width). Every bounce plays the next note and
grows the ball (`mode: grow`) or adds a ball (`mode: spawn`).

* **Hook**: the video opens with the ball already in flight (a 0.25 s pre-roll
  feeds its trail on frame 0) and the first bounce lands within
  `first_bounce` (0.16–0.28 s).
* **Build**: the ring shrinks steadily, then the squeeze accelerates over the
  final third (`ring_squeeze`). The ring tints red as the ball's diameter
  approaches the gap's width, and is fully red once the ball can't fit.
* **Escape burst**: a ball slips through the gap; `slowmo` seconds (0.4) of
  quarter-speed slow motion with a rising sweep, zooming in on the gap, then
  the ring shatters: "ESCAPED!"
* **Trapped freeze**: the ball fills the ring (or time runs out); the frame
  freezes and desaturates: "TRAPPED!"

Balls keep a constant energy, so `energy` (reach height above the center)
decides how high they can climb. The gap starts near the top, out of reach.
The ring's spin and shrink bring it into reach while the ball grows, so it
is a race. Main params: `mode`, `ring_radius`, `ring_shrink`,
`ring_squeeze`, `gap`, `spin`, `gravity`, `energy`, `ball_radius`,
`first_bounce`, `grow` (`auto` scales with duration), `slowmo`,
`max_balls`, `spawn_from`, `trail`, `counter`, `escape_text`,
`trapped_text`, `outro`, `loop_xfade`, `tries`, `pace_window`, `sync`,
`midi_ending`, `min_flight`.

**Choreographed to MIDI** (`sync: midi`, automatic when the config names a
MIDI file; `configs/shrinking_ring_midi.yaml`): the ball bounces exactly on
the score's notes and plays them. At every bounce the next flight is
*solved for*: pymunk moves first and applies gravity after, so N physics
steps later a ball launched at velocity v0 is at p0 + N·dt·v0 + g·dt²·N(N−1)/2.
Every 2° of wall is tried as the landing point; a flight must stay inside
the shrinking ring (allowing for the ball's growth on the way), land on
solid wall, leave the wall inwards and arrive clearly into it, and the one
closest to the natural rebound (direction and energy) wins. Contacts land
within a step or two of the note (≤ 8 ms) and the sound is placed on the
note's exact time.

* A gap between notes too long for one flight gets quiet ghost bounces (a
  faint `tap`, no growth); notes closer than `min_flight` play without a
  bounce (the ball glows). Gravity is lowered (to no less than 40%) when the
  score's usual note spacing would not fit one flight in the final room.
* Growth per note is calibrated to the score, and the video ends on its last
  note (`midi_ending`): **trapped**: the ball pops to fill the ring and
  freezes (the thump and final chord land on the last note); **escape**: it
  is aimed out through the gap so the burst lands a beat after the last note
  (with the usual slow motion), taking a ghost bounce first if the gap is out
  of reach. `auto` picks one from the seed.
* Other balls (spawn mode) tap softly; the score's notes belong to the hero.

### polyrhythm_loop

Concentric rings (`rings`, default 12), one dot each. Each time a dot
crosses the top line it plays a note and its ring flashes; ring 0 is the
outermost, slowest and lowest. Every dot is back at the top at exactly the
end of the cycle. How the laps are chosen (`sync`, `auto` by default):

* **free** (the default with the classic sound sets;
  `configs/polyrhythm_loop.yaml`): ring `i` laps `base + i` times per cycle
  (10…21) and the cycle is the duration. Ring index → pentatonic degree.
  Neighbouring rings drift apart and line up into spokes at simple fractions
  of the cycle (two at 1/2, three at 1/3, …).
* **grid** (the default with the `harmony` set;
  `configs/polyrhythm_harmony.yaml`): every hit lands on a 16th note of the
  harmony's tempo and the chord progression plays exactly once per cycle.
  The cycle is a whole number of bars, the same whole number per chord, and
  a whole number of frames at 15/30/60 fps. A ring with `laps` laps hits every
  16ths/laps 16th notes, so its lap count must divide the cycle's 16th count
  (and leave at least `min_interval` 16ths between hits). Of the cycles that
  fit, the one nearest the duration wins, at a tempo in 70–90 BPM (or the
  config's `tempo`, nudged by under 1% to land on whole frames). With the
  defaults (I-V-vi-IV, 45 s, 12 rings) that is 12 bars at 72 BPM = 40 s,
  laps 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64: from one lap per cycle to a
  dotted-eighth pulse, with 3-against-4 cross-rhythms. A three-chord
  progression (ii-V-I) gets 15 bars at 80 BPM = exactly 45 s. Explicit
  `laps: [...]` are checked against the grid.
* **midi** (the default when the config names a MIDI file;
  `configs/polyrhythm_midi.yaml`): one ring per pitch of the score (lowest
  outermost; pitches share rings beyond `rings`), and each dot crosses the
  top exactly on its pitch's notes. Between crossings the dot follows a
  monotone spline through its crossing times (Fritsch–Carlson: speed varies
  smoothly and never reverses). The cycle is the score, looped or cut to the
  duration at bar lines and stretched by under 0.1% to a whole number of frames.

Dot trails are drawn along each ring from the closed-form motion, so fast
laps stay curved and previews match finals. The video is exactly one cycle
(the format's default duration is 45 s) and loops with no crossfade:

* **Picture.** Motion is closed-form, not simulated: every position, flash
  and ripple is a function of the time modulo the cycle. The frame after the
  last one is frame 0 exactly, and frame 0's trails come from the end of the
  cycle.
* **Note timing.** Notes start at the exact crossing instants
  (`audio_timing: exact`, sample-accurate) rather than snapping to frames,
  so the polyrhythm stays exact at any frame rate. A note can lead its flash
  by up to one frame.
* **Sound tails wrap around.** The timeline is marked `periodic`: the audio
  is rendered over two cycles and the second copy is kept, so the opening
  carries the reverb tails of the closing notes, as on a replay.
* **Clean start in the MP4.** The AAC encoder smears a loud attack on the
  very first sample of a file, and here a 12-note chord sits on it. The
  pipeline encodes one second of the cycle's end as a lead-in and trims it
  with the MP4 edit list (`-itsoffset`), so the track still starts at 0 and
  lasts exactly one cycle.

In free and grid mode the seed changes only the palette and, where they
are random, key, progression and pitch choices (the motion is fixed by the
params). Params: `rings`, `sync`, `base`, `laps`, `min_interval`, `cycle`,
`center`, `outer_radius`, `inner_radius`, `ring_width`, `dot_radius`,
`direction` (cw/ccw), `flash` (flash decay, seconds), `trail`.

## Sound sets

| set | each bounce | options |
| --- | --- | --- |
| `harmony` | a tone of the current chord, over a soft pad and bass; -14 LUFS | `key` (C), `progression` (I-V-vi-IV, vi-IV-I-V, ii-V-I, any roman numerals, random), `tempo` (auto: 70–90), `chord_bars`, `instrument`, `low`/`high` (C4/C6), `dyads`, `humanize`, `pad`, `bass` |
| `pentatonic` | next note of a pentatonic pattern | `key`, `scale` (major/minor), `pattern` (updown/up/down/walk/random), `instrument`, `octave`, `octave_up` (auto/true/false), `octaves` |
| `melody` | next note of a public-domain melody | `melody` (ode_to_joy, fur_elise, twinkle, greensleeves, canon_in_d, frere_jacques, brahms_lullaby, mountain_king, amazing_grace, mary_lamb, random), `instrument`, `transpose`, `double` |
| `tactile` | pitched bubble pop + click (dry, ASMR) | `key`, `pitch` (scale/size), `octave`, `octave_up` |
| `ambient` | soft chime on the current chord over slow pad chords | `key`, `chord_seconds`, `instrument` |

Instruments (any set with an `instrument` option): `felt_piano` (muted
upright: soft attack, two slightly detuned strings, two-stage decay),
`music_box` (steel tine and pin), `celesta` (hammered bar over a resonator),
`kalimba`, `marimba`, `glock`, `pluck`, `chime`. All are additive/modal
syntheses in numpy; each voice fades out at its end so it never ticks.

`octave_up: auto` (the default) moves the pattern up an octave when its
lowest note would sit below ~200 Hz, which phone speakers barely reproduce;
`true`/`false` force it.

All sets also take `min_gap` (minimum seconds between notes when bounces
pile up), `volume`, `loudness` (target LUFS) and `midi`, and have their own
slow-motion riser, escape (boom + whoosh + run + chord) and freeze (thump +
glassy shimmer + chord) finales. The classic sets are RMS-normalized and
soft limited below -0.6 dBFS; the mix fades to silence under the loop crossfade.

### The harmony engine

`satisfy/audio/harmony.py` decides one key, progression and tempo per video
(by default C major, I-V-vi-IV, a seeded 70–90 BPM, one bar per chord; the
random draws always happen in the same order, so fixing one option never
changes the others). The `harmony` set then:

* gives every hit a tone of the chord sounding at that moment, between `low`
  and `high` (C4–C6): never the pitch just played (nor one already sounding
  in the same instant), mostly stepwise, towards the event's `register` hint
  when it has one (polyrhythm rings: outer low, inner high); a lone hit adds a
  second chord tone 3–9 semitones away 15% of the time (`dyads`); velocities
  vary by ±15% (`humanize`); many simultaneous hits are scaled down (an
  accent, not a wall);
* plays a soft detuned pad holding each chord (crossfading into the next)
  and a simple bass on the chord's root, beats 1 and 3 of every bar;
* masters to **-14 LUFS** integrated (ITU-R BS.1770: K-weighting, 400 ms
  blocks, absolute and relative gates; checked against ffmpeg's `ebur128`)
  with a look-ahead limiter at -1 dBFS whose gain is a moving minimum then a
  moving average, so no sample can exceed the ceiling. A loop is mastered as
  a loop: the limiter looks across the seam;
* in a loop, picks pitches twice round the cycle and keeps the second round,
  so the start follows on from the end, and every repeat plays the same notes.

## MIDI

`sound: {midi: assets/midi/ode_to_joy.mid}` (relative to the working folder or
the project) reads a Standard MIDI File with a small built-in parser (format
0/1, running status, tempo changes, time signatures; the drum channel is
skipped). Notes are grouped by onset into chords.

* **Pitches**, in any sound set: a hit plays the score's next note group
  (formats that do not choreograph), or exactly the notes the format attached.
* **Timing**, in formats that can choreograph: `polyrhythm_loop` and
  `shrinking_ring` put their hits on the note times (see above).
* **Harmony**: the `harmony` set's pad and bass follow chords estimated from
  the score, bar by bar (or half bar where the bass line moves): the triad
  that best covers the notes, strongly favouring one built on a separate bass
  voice, with ties going to the key's primary chords (key by
  Krumhansl–Kessler profiles).

`assets/midi/ode_to_joy.mid` (Beethoven, public domain, with a simple bass
line) is written by `assets/midi/make_examples.py` using `satisfy.audio.midi.write`.

## Output

* `<name>_seed<N>.mp4`: H.264 High, yuv420p, BT.709 tagged, +faststart; AAC 48 kHz stereo
* `<name>_seed<N>.json`: config, summary (ending, length, counts) and the full event timeline
  (with `music`: the key, tempo and chords the picture was built for, when a format used one)
* batch folder: `previews/`, `posters/`, `manifest.json`, `gallery.html`, `keepers.json`

## Tests

```bash
pip install pytest && python -m pytest -q
```

Covers determinism, preview/final parity (including slow motion), the
mid-flight opening, the loop seam, safe-zone text, frame-exact note onsets,
every sound set, config validation, and an end-to-end
batch → keepers → final run (needs ffmpeg). For `polyrhythm_loop`: one note
per top crossing at the exact instant, frames past the end equal frames 0,
1, 2, the looped audio equals a continuous three-cycle performance across
the seam, and the encoded MP4 starts with the opening chord intact; on the
grid, every hit sits on a 16th note and the progression fills the cycle once.
Harmony: the plan and its options, chord-tone hits without repeats, dyad
rate and velocity range, pad and bass on each chord's root, the loudness
meter against the BS.1770 reference tone, -14 LUFS output, the limiter's
ceiling and wrap-around, the new instruments. MIDI: a hand-built file with
running status, a tempo change and drums; round trips; chord estimation;
polyrhythm dots at the top on every note; ring bounces on every note within
2.5 physics steps, ending on the last note (freeze on it, or a burst on a
beat after it); free physics playing the score in order.
