# kinetic: bold kinetic-typography Shorts

Remotion project that turns a voiceover (or a plain-text script, via local
TTS) into a 1080x1920, 60 fps YouTube Short: huge centred type slamming in
word-chunk by word-chunk, emphasis in an accent colour, a zoom punch and
shake on every beat, over a slowly drifting gradient. The voice and an
optional beat are mixed with the music ducked under the voice, at -14 LUFS.

Sample (`public/videos/did-you-know`): a 30 s "did you know" script voiced by
Kokoro over a synthesized 96 BPM beat, rendered to `out/did-you-know.mp4`
(see `samples/` at the repo root for the committed copy and contact sheet).

```
script.txt ──Kokoro──► voice.wav ──whisper.cpp──► transcript.json (word timings, editable)
                           │                            │
beat.wav ──beat tracker──► beats.json                   ▼
   │                                          chunks of 1-3 words ──► layout (fits the safe area)
   └──► mix: music ducked under voice, -14 LUFS ──► mix.wav ──► Remotion <KineticShort> ──► MP4
```

## Setup

```bash
npm install
# plus ffmpeg on PATH; whisper.cpp is cloned and built on first use (needs git, make, cmake, a C++ compiler)
```

Remotion downloads its own headless Chrome on first render. Where that isn't
possible, point it at a local one:

```bash
export REMOTION_BROWSER_EXECUTABLE=/path/to/chrome-headless-shell   # read by remotion.config.ts
```

## Usage

```bash
npm run video -- did-you-know            # every step that needs it, then render out/did-you-know.mp4
npm run video -- did-you-know --no-render
npm run video -- did-you-know --force transcribe,beats   # redo steps even if nothing changed
npm run sheet -- did-you-know            # out/did-you-know_contact.png
npx tsx scripts/check-safe-zones.ts did-you-know   # proves no text enters the Shorts UI zones
npm run dev                              # Remotion Studio (props: id, showSafeZones, mask)
npm run beat -- public/videos/x/beat.wav --bpm 96 --seconds 32   # a royalty-free test beat
npm test
```

A new video is a folder `public/videos/<id>/` with a `config.json` and either
a `voice.wav` (voiceover or song) or a `script.txt`.

## Config (`public/videos/<id>/config.json`)

```json
{
  "script": "script.txt",
  "voice": "voice.wav",
  "tts": { "voice": "af_heart", "speed": 0.92, "sentencePauseMs": 300 },
  "music": "beat.wav",
  "palette": "sunset",
  "emphasis": ["three hearts", "blue"],
  "tailSeconds": 1.3
}
```

| key | default | |
| --- | --- | --- |
| `voice` | `voice.wav` | the vocal audio (voiceover or song); written by TTS when `script` is set |
| `script` | — | plain text for Kokoro; `*word*` or `*a phrase*` marks emphasis (asterisks are not spoken) |
| `tts` | `af_heart`, 1.0, 220 ms | Kokoro voice, speed, pause between sentences |
| `music` | — | optional background beat (any format ffmpeg reads); loops with a crossfade if short |
| `beatsFrom` | `auto` | `music`, or `voice` for a song (auto: music if given) |
| `palette` | `sunset` | `sunset`, `electric`, `lime`, `candy`, `ember`, `mono`, or `{ background: [...], text, accent, stroke }` |
| `emphasis` | `[]` | words or phrases: accent colour, bigger, their own chunk |
| `font` | `Anton` | `Anton` or `Archivo Black` (bundled, OFL) |
| `musicDb` / `duckDb` | -4 / -12 | music level vs the voice's loudness, and the extra dip while the voice talks |
| `loudness` | -14 | integrated LUFS of the mix |
| `tailSeconds` | 1.2 | after the last word |
| `whisperModel` | `medium.en` | `tiny.en` … `large-v3-turbo` |
| `maxWords` / `pauseMs` | 3 / 260 | chunk size; a silence this long starts a new chunk |

## How it works

**TTS** (`scripts/lib/tts.ts`). Kokoro-82M runs locally through `kokoro-js`
(ONNX, CPU; the model downloads from Hugging Face once). The script is spoken
sentence by sentence and joined with a fixed pause, so long scripts work and
the rhythm is predictable.

**Transcription** (`scripts/video.ts`, `scripts/lib/words.ts`). Remotion's
`@remotion/install-whisper-cpp` builds whisper.cpp 1.7.6 and runs it with
word splitting and DTW token timestamps; `toCaptions()` gives Remotion
`Caption`s. Whisper's word times are rough (segment starts wander ±200 ms,
DTW times run ~150 ms late), and the type slams on the first word of each
chunk, so the onsets are refined against the audio: where speech resumes
after a pause is known exactly and anchors the nearest word; the anchors
measure this file's DTW lateness, which corrects every other word; each word
then snaps to the strongest rise in loudness near its corrected time. When
the text is known (TTS), the transcript takes the script's spelling and
punctuation (edit-distance alignment). On the sample: 88/88 words matched,
15 pause anchors, DTW lateness 155 ms.

`transcript.json` is the `Caption[]` the video reads (word level; `startMs`
is the slam time). **Edit it by hand** to fix a word or nudge a time; the
render picks it up. The pipeline never overwrites an edited transcript unless
the voice audio itself changes (or `--force transcribe`), and then it keeps a
backup. `transcript.raw.json` is Whisper's untouched output.

**Beats** (`scripts/lib/beats.ts`). Spectral flux of the log STFT (with the
quarter-window latency of a Hann window added back), tempo by
autocorrelation over one octave, 66-130 BPM (so a pulse can't be mistaken
for its double or half; faster music is punched at half time, as it is
felt), beat tracking by dynamic programming (Ellis 2007), and downbeats from
the beat phase that carries the kicks and chord changes. On generated beats
from 66 to 174 BPM: the right tempo, every beat within 50 ms (mean error
2-6 ms), downbeats on the bar lines.

**Chunks** (`src/lib/chunks.ts`). 1-3 words; a new chunk after punctuation,
at a pause, when the chunk is full, and around emphasised phrases (they get
their own). A soft break never leaves a dangling "the"/"of" at the end of a
chunk. A chunk stays until the next one slams in, or leaves after a hold when
a long pause follows.

**Layout and motion** (`src/lib/layout.ts`, `src/lib/motion.ts`). Each line
of a chunk is fitted to the width on its own (short words get huge), up to
3 lines, and the whole block is then scaled down until, at the motion's
largest scale (slam start x beat punch), rotation and shake, it still fits
the safe area: never in the bottom 20% or the right 12%. The block is centred
between the safe edges (a touch left of the frame's centre). Text widths come
from the real font (`measureText` after the bundled font loads).

* **Slam**: spring scale from 1.22x (emphasis 1.25x) with a slight rotation
  that settles at 1.5°, alternating sides; 3-frame fade-in.
* **Words** not yet spoken wait at 30% opacity and pop when spoken.
* **Emphasis**: accent colour, 14% bigger next to plain words, glow.
* **Beats**: zoom punch around the text centre (5% on downbeats, 2.5% on
  beats) and a short decaying shake.
* **Background**: a swaying linear gradient plus three soft colour blobs on
  slow Lissajous paths; beats lift its brightness a little.

`scripts/check-safe-zones.ts` renders the video in mask mode (white type,
outline and shadow on black) and checks every frame for text pixels in the
Shorts UI zones.

**Mix** (`scripts/lib/mix.ts`, `scripts/lib/loudness.ts`). The music is
matched to the voice's loudness plus `musicDb`, and a voice-activity envelope
(60 ms look-ahead, 250 ms hold, 30 ms attack, 350 ms release) dips it by
`duckDb` while the voice talks. The sum is normalised to the target with a
BS.1770 meter (K-weighting, gating) and a look-ahead limiter at -1.5 dBFS;
ffmpeg's `ebur128` confirms the result (-14.0 LUFS, -1.4 dBTP on the sample).

## Layout

* `src/` — the composition: `KineticShort`, `ChunkView`, `Background`,
  `data.ts` (loads a video's files in `calculateMetadata`), `lib/` (pure
  logic shared with the scripts and tests)
* `scripts/` — the pipeline (`video.ts`), beat generator, contact sheet,
  safe-zone check; `scripts/lib/` audio, TTS, transcription helpers, beats,
  loudness, mix
* `public/videos/<id>/` — one video's inputs and generated files
* `tests/` — chunking, beats, mix/loudness, word timing, layout (`npm test`)
