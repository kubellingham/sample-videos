// Kokoro (local, 82M) text-to-speech: a plain script becomes the voiceover.
import { env } from "@huggingface/transformers";
import { KokoroTTS } from "kokoro-js";
import path from "node:path";
import type { Pcm } from "./audio";

export const KOKORO_MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";

/** Split into sentences (keeps the punctuation). */
export const sentences = (text: string) =>
  text
    .replace(/\s+/g, " ")
    .trim()
    .match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g)
    ?.map((s) => s.trim())
    .filter(Boolean) ?? [];

/**
 * Speak `text` with Kokoro, sentence by sentence (so long scripts work and
 * the pause between sentences is ours to set), and join the pieces.
 */
export const synthesize = async (
  text: string,
  { voice = "af_heart", speed = 1, sentencePauseMs = 220, cacheDir = ".cache/hf" } = {},
): Promise<Pcm> => {
  env.cacheDir = path.resolve(cacheDir);
  const tts = await KokoroTTS.from_pretrained(KOKORO_MODEL, { dtype: "fp32", device: "cpu" });
  const parts: Float32Array[] = [];
  let sr = 24000;
  for (const s of sentences(text)) {
    const audio = await tts.generate(s, { voice: voice as "af_heart", speed });
    sr = audio.sampling_rate;
    parts.push(trimSilence(audio.audio, sr));
    parts.push(new Float32Array(Math.round((sentencePauseMs / 1000) * sr)));
  }
  parts.pop();
  const n = parts.reduce((s, p) => s + p.length, 0);
  const out = new Float32Array(n + Math.round(0.15 * sr)); // a short lead-in so the first word isn't clipped
  let o = Math.round(0.15 * sr);
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return { sampleRate: sr, channels: [out] };
};

/** Trim leading/trailing near-silence, keeping 40 ms of air on each side. */
export const trimSilence = (x: Float32Array, sr: number) => {
  const thr = 0.01;
  let a = 0;
  while (a < x.length && Math.abs(x[a]) < thr) a++;
  let b = x.length - 1;
  while (b > a && Math.abs(x[b]) < thr) b--;
  const pad = Math.round(0.04 * sr);
  return x.slice(Math.max(0, a - pad), Math.min(x.length, b + pad));
};
