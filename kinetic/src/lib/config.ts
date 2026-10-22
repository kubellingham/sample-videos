// Per-video config (public/videos/<id>/config.json), shared by the pipeline
// scripts and the composition.
import { z } from "zod";

export const PaletteSchema = z.object({
  background: z.array(z.string()).min(2), // gradient colours, slowly animated
  text: z.string(),
  accent: z.string(), // emphasis words
  stroke: z.string(), // outline / shadow behind the type
});
export type Palette = z.infer<typeof PaletteSchema>;

export const PALETTES: Record<string, Palette> = {
  sunset: { background: ["#ff4e50", "#f9a03f", "#7b2ff7", "#2b0f54"], text: "#ffffff", accent: "#ffe14d", stroke: "#1d0b33" },
  electric: { background: ["#0f0c29", "#302b63", "#00b4d8", "#24243e"], text: "#ffffff", accent: "#00f5d4", stroke: "#05031a" },
  lime: { background: ["#0b3d2e", "#1b998b", "#114b5f", "#04201a"], text: "#ffffff", accent: "#c6ff00", stroke: "#021a13" },
  candy: { background: ["#ff6fd8", "#3813c2", "#ff9a8b", "#2a0a5e"], text: "#ffffff", accent: "#fff200", stroke: "#25063f" },
  ember: { background: ["#1a0000", "#7f1d1d", "#f97316", "#2a0505"], text: "#fff7ed", accent: "#fde047", stroke: "#120000" },
  mono: { background: ["#111111", "#2b2b2b", "#444444", "#0a0a0a"], text: "#ffffff", accent: "#ff3b30", stroke: "#000000" },
};

export const ConfigSchema = z.object({
  /** Vocal audio in the video folder: a voiceover or a song. Written by TTS when `script` is set. */
  voice: z.string().default("voice.wav"),
  /** Plain-text script for Kokoro TTS. `*word*` marks emphasis (the asterisks are not spoken). */
  script: z.string().optional(),
  tts: z
    .object({
      voice: z.string().default("af_heart"),
      speed: z.number().min(0.5).max(2).default(1),
      sentencePauseMs: z.number().min(0).default(220),
    })
    .default({ voice: "af_heart", speed: 1, sentencePauseMs: 220 }),
  /** Optional background beat in the video folder. */
  music: z.string().optional(),
  /** Where beats come from: the music track, or the vocal audio itself (a song). */
  beatsFrom: z.enum(["auto", "music", "voice"]).default("auto"),
  palette: z.union([z.string(), PaletteSchema]).default("sunset"),
  /** Words or short phrases to emphasise (case and punctuation are ignored). */
  emphasis: z.array(z.string()).default([]),
  font: z.enum(["Anton", "Archivo Black"]).default("Anton"),
  /** Mix */
  musicDb: z.number().default(-4), // music level relative to its own loudness-matched level
  duckDb: z.number().default(-12), // extra music gain while the voice is talking
  loudness: z.number().default(-14), // integrated LUFS of the final mix
  tailSeconds: z.number().min(0).default(1.2), // after the last word
  /** Transcription */
  whisperModel: z.enum(["tiny.en", "base.en", "small.en", "medium.en", "large-v3-turbo"]).default("medium.en"),
  language: z.string().default("en"),
  /** Chunking */
  maxWords: z.number().int().min(1).max(4).default(3),
  pauseMs: z.number().min(0).default(260),
});
export type VideoConfig = z.infer<typeof ConfigSchema>;

export const resolvePalette = (p: VideoConfig["palette"]): Palette => {
  if (typeof p !== "string") return p;
  const found = PALETTES[p];
  if (!found) throw new Error(`Unknown palette "${p}". Known: ${Object.keys(PALETTES).join(", ")}`);
  return found;
};

/** Emphasis phrases marked in a TTS script with *asterisks*, and the script without them. */
export const parseScript = (text: string): { spoken: string; emphasis: string[] } => {
  const emphasis = [...text.matchAll(/\*([^*]+)\*/g)].map((m) => m[1].trim());
  return { spoken: text.replace(/\*([^*]+)\*/g, "$1").replace(/\s+/g, " ").trim(), emphasis };
};
