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
sound_set: pentatonic    # pentatonic | melody | tactile | ambient
duration: 28             # maximum length in seconds
hook_text: "Will it escape before it fills the ring?"
sound:                   # optional sound-set options
  instrument: marimba
params:                  # optional format params
  mode: grow
```

`palette` can also be an inline mapping (`base`, `bg_top`, `bg_bottom`, `glow`,
`ring`, `accents`, `text`, `text_stroke`).

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
  (audio), so Shorts' auto-replay has no visible or audible seam.
* **Shorts safe zone.** All text stays out of the bottom 20% and the right
  12% of the frame, where the Shorts UI sits. The hook is laid out inside
  the safe area, and the renderer moves any label that would cross into it.

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
`id`), `Arc` (rings, gaps, flying shards), `Ripple`, `Particle`, `Label`, plus
frame `Effects` (flash, desaturate/tint, zoom, shake, vignette). Event kinds
the sound sets understand: `bounce`, `spawn`, `hit`, `sealed`, `escape`,
`freeze`.

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
`trapped_text`, `outro`, `loop_xfade`, `tries`, `pace_window`.

## Sound sets

| set | each bounce | options |
| --- | --- | --- |
| `pentatonic` | next note of a pentatonic pattern | `key`, `scale` (major/minor), `pattern` (updown/up/down/walk/random), `instrument` (marimba/glock/kalimba/pluck/chime), `octave`, `octave_up` (auto/true/false), `octaves` |
| `melody` | next note of a public-domain melody | `melody` (ode_to_joy, fur_elise, twinkle, greensleeves, canon_in_d, frere_jacques, brahms_lullaby, mountain_king, amazing_grace, mary_lamb, random), `instrument`, `transpose`, `double` |
| `tactile` | pitched bubble pop + click (dry, ASMR) | `key`, `pitch` (scale/size), `octave`, `octave_up` |
| `ambient` | soft chime on the current chord over slow pad chords | `key`, `chord_seconds`, `instrument` |

`octave_up: auto` (the default) moves the pattern up an octave when its
lowest note would sit below ~200 Hz, which phone speakers barely reproduce;
`true`/`false` force it.

All sets also take `min_gap` (minimum seconds between notes when bounces
pile up) and `volume`, and have their own slow-motion riser, escape (boom +
whoosh + run + chord) and freeze (thump + glassy shimmer + chord) finales.
The mix is loudness normalized, soft limited below -0.6 dBFS, and fades to
silence under the loop crossfade.

## Output

* `<name>_seed<N>.mp4`: H.264 High, yuv420p, BT.709 tagged, +faststart; AAC 48 kHz stereo
* `<name>_seed<N>.json`: config, summary (ending, length, counts) and the full event timeline
* batch folder: `previews/`, `posters/`, `manifest.json`, `gallery.html`, `keepers.json`

## Tests

```bash
pip install pytest && python -m pytest -q
```

Covers determinism, preview/final parity (including slow motion), the
mid-flight opening, the loop seam, safe-zone text, frame-exact note onsets,
every sound set, config validation, and an end-to-end
batch → keepers → final run (needs ffmpeg).
