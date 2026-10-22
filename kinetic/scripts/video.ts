// One video, end to end:
//
//   npm run video -- <id> [--force tts,transcribe,beats,mix] [--no-render] [--sheet]
//
// Reads public/videos/<id>/config.json and runs only the steps whose inputs
// changed (hashes in .pipeline.json):
//   tts        script.txt -> voice.wav (Kokoro, local)
//   transcribe voice.wav  -> transcript.json (Whisper word timings, refined; edit by hand freely)
//   beats      music      -> beats.json (tempo, beats, downbeats)
//   mix        voice + ducked music -> mix.wav at the target loudness, meta.json
//   render     -> out/<id>.mp4 (Remotion)
// A hand-edited transcript.json is never overwritten unless the voice itself
// changes (or --force transcribe), and then it is backed up first.
import path from "node:path";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { downloadWhisperModel, installWhisperCpp, toCaptions, transcribe } from "@remotion/install-whisper-cpp";
import { ConfigSchema, parseScript, type VideoConfig } from "../src/lib/config";
import { decode, duration, fileSha, measureLoudness, sha, writeWav } from "./lib/audio";
import { analyzeBeats } from "./lib/beats";
import { mixTracks } from "./lib/mix";
import { synthesize } from "./lib/tts";
import { alignToScript, captionsToRawWords, refineOnsets, toCaptionJson } from "./lib/words";

const ROOT = path.resolve(__dirname, "..");
const WHISPER_DIR = path.join(ROOT, ".cache", "whisper.cpp");
const WHISPER_VERSION = "1.7.6";

type State = Record<string, Record<string, string | number | null>>;

const log = (step: string, msg: string) => console.log(`  ${step.padEnd(10)} ${msg}`);

const readState = (dir: string): State => {
  const p = path.join(dir, ".pipeline.json");
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : {};
};

export const run = async (id: string, flags: { force: Set<string>; render: boolean; sheet: boolean }) => {
  const dir = path.join(ROOT, "public", "videos", id);
  const cfgPath = path.join(dir, "config.json");
  if (!existsSync(cfgPath)) throw new Error(`No ${path.relative(ROOT, cfgPath)}`);
  const config: VideoConfig = ConfigSchema.parse(JSON.parse(readFileSync(cfgPath, "utf8")));
  const state = readState(dir);
  const save = () => writeFileSync(path.join(dir, ".pipeline.json"), JSON.stringify(state, null, 2) + "\n");
  const voicePath = path.join(dir, config.voice);
  const script = config.script ? parseScript(readFileSync(path.join(dir, config.script), "utf8")) : null;
  console.log(`== ${id}`);

  // 1. TTS ------------------------------------------------------------------
  if (script) {
    const key = sha(script.spoken, JSON.stringify(config.tts));
    if (flags.force.has("tts") || !existsSync(voicePath) || state.tts?.key !== key) {
      const t0 = Date.now();
      const pcm = await synthesize(script.spoken, { ...config.tts, cacheDir: path.join(ROOT, ".cache", "hf") });
      writeWav(voicePath, pcm);
      state.tts = { key, voice: config.tts.voice, seconds: Number((pcm.channels[0].length / pcm.sampleRate).toFixed(3)) };
      save();
      log("tts", `Kokoro ${config.tts.voice} -> ${config.voice} (${state.tts.seconds} s) in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
    } else log("tts", `${config.voice} is up to date`);
  }
  if (!existsSync(voicePath)) throw new Error(`No voice audio: ${path.relative(ROOT, voicePath)} (add one, or a "script" for TTS)`);
  const voiceHash = fileSha(voicePath);

  // 2. Transcribe -----------------------------------------------------------------
  const transcriptPath = path.join(dir, "transcript.json");
  const transcriptKey = sha(voiceHash, config.whisperModel, script?.spoken ?? "");
  const edited = existsSync(transcriptPath) && state.transcribe?.output !== fileSha(transcriptPath);
  if (flags.force.has("transcribe") || !existsSync(transcriptPath) || state.transcribe?.key !== transcriptKey) {
    if (edited) {
      const backup = path.join(dir, `transcript.edited-${Date.now()}.json`);
      copyFileSync(transcriptPath, backup);
      log("transcribe", `the voice changed: your edited transcript is kept as ${path.basename(backup)}`);
    }
    const t0 = Date.now();
    await installWhisperCpp({ to: WHISPER_DIR, version: WHISPER_VERSION, printOutput: false });
    await downloadWhisperModel({ model: config.whisperModel, folder: WHISPER_DIR, printOutput: false });
    mkdirSync(path.join(ROOT, ".cache"), { recursive: true });
    const wav16 = path.join(ROOT, ".cache", `${id}-16k.wav`);
    spawnSync("ffmpeg", ["-v", "error", "-y", "-i", voicePath, "-ar", "16000", "-ac", "1", wav16], { stdio: "inherit" });
    const whisperCppOutput = await transcribe({
      inputPath: wav16,
      whisperPath: WHISPER_DIR,
      whisperCppVersion: WHISPER_VERSION,
      model: config.whisperModel,
      tokenLevelTimestamps: true,
      splitOnWord: true,
      language: config.language as "en",
      printOutput: false,
    });
    const { captions } = toCaptions({ whisperCppOutput });
    writeFileSync(path.join(dir, "transcript.raw.json"), JSON.stringify(captions, null, 1) + "\n");
    let words = captionsToRawWords(captions);
    let note = "";
    if (script) {
      const aligned = alignToScript(words, script.spoken);
      words = aligned.words;
      note = `, ${aligned.matched}/${aligned.total} words as in the script`;
    }
    const refined = refineOnsets(words, decode(wav16, 16000, 1));
    const out = toCaptionJson(refined.words, words.map((w) => w.confidence));
    writeFileSync(transcriptPath, JSON.stringify(out, null, 1) + "\n");
    state.transcribe = { key: transcriptKey, output: fileSha(transcriptPath), model: config.whisperModel, anchors: refined.anchors, biasMs: refined.bias };
    save();
    log("transcribe", `whisper.cpp ${config.whisperModel}: ${out.length} words${note}; onsets refined (${refined.anchors} pause anchors, DTW offset ${refined.bias} ms) in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  } else log("transcribe", `transcript.json is up to date${edited ? " (hand-edited: kept)" : ""}`);

  // 3. Beats -------------------------------------------------------------------
  const beatsFrom = config.beatsFrom === "auto" ? (config.music ? "music" : "voice") : config.beatsFrom;
  const beatSource = beatsFrom === "music" && config.music ? path.join(dir, config.music) : voicePath;
  const beatsPath = path.join(dir, "beats.json");
  const beatsKey = fileSha(beatSource);
  if (flags.force.has("beats") || !existsSync(beatsPath) || state.beats?.key !== beatsKey) {
    const a = analyzeBeats(decode(beatSource, 22050, 1));
    writeFileSync(beatsPath, JSON.stringify(a) + "\n");
    state.beats = { key: beatsKey, source: path.basename(beatSource), tempo: a.tempo, beats: a.beats.length };
    save();
    log("beats", `${path.basename(beatSource)}: ${a.tempo} BPM, ${a.beats.length} beats, ${a.downbeats.length} downbeats, ${a.onsets.length} onsets`);
  } else log("beats", "beats.json is up to date");

  // 4. Mix ---------------------------------------------------------------------
  const musicPath = config.music ? path.join(dir, config.music) : null;
  const voiceSeconds = duration(voicePath);
  const total = Number((voiceSeconds + config.tailSeconds).toFixed(3));
  const mixKey = sha(voiceHash, musicPath ? fileSha(musicPath) : "-", String(total), String(config.musicDb), String(config.duckDb), String(config.loudness));
  const mixPath = path.join(dir, "mix.wav");
  if (flags.force.has("mix") || !existsSync(mixPath) || state.mix?.key !== mixKey) {
    const r = mixTracks(decode(voicePath, 48000, 1), musicPath ? decode(musicPath, 48000, 2) : null, {
      duration: total,
      musicDb: config.musicDb,
      duckDb: config.duckDb,
      loudness: config.loudness,
    });
    writeWav(mixPath, r.pcm);
    const ff = measureLoudness(mixPath);
    state.mix = { key: mixKey, seconds: total, lufs: ff.lufs, truePeak: ff.truePeak, duckDb: r.duckedDb === null ? null : Number(r.duckedDb.toFixed(1)) };
    save();
    log("mix", `voice${musicPath ? ` + ${path.basename(musicPath)} (ducked ${state.mix.duckDb} dB under speech)` : ""} -> mix.wav: ${total} s, ${ff.lufs} LUFS, true peak ${ff.truePeak} dBTP`);
  } else log("mix", "mix.wav is up to date");
  writeFileSync(path.join(dir, "meta.json"), JSON.stringify({ duration: total, audio: "mix.wav", emphasis: script?.emphasis ?? [] }, null, 1) + "\n");

  // 5. Render ------------------------------------------------------------------
  if (flags.render) {
    const out = path.join(ROOT, "out", `${id}.mp4`);
    const t0 = Date.now();
    const r = spawnSync("npx", ["remotion", "render", "KineticShort", out, `--props=${JSON.stringify({ id })}`], { cwd: ROOT, stdio: "inherit" });
    if (r.status !== 0) throw new Error("remotion render failed");
    log("render", `${path.relative(ROOT, out)} in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
    if (flags.sheet) spawnSync("npx", ["tsx", "scripts/contact-sheet.ts", id], { cwd: ROOT, stdio: "inherit" });
  }
};

if (require.main === module) {
  const args = process.argv.slice(2);
  const id = args.find((a) => !a.startsWith("--"));
  if (!id) {
    console.error("usage: npm run video -- <id> [--force tts,transcribe,beats,mix] [--no-render] [--sheet]");
    process.exit(1);
  }
  const fi = args.indexOf("--force");
  const force = new Set(fi >= 0 ? (args[fi + 1] ?? "").split(",") : []);
  run(id, { force, render: !args.includes("--no-render"), sheet: args.includes("--sheet") }).catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
