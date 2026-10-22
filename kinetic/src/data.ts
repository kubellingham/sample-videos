// Loads one video's files from public/videos/<id>/ and turns them into
// everything the composition draws: chunks, their layout, beats, palette.
import type { Caption } from "@remotion/captions";
import { measureText } from "@remotion/layout-utils";
import type { CalculateMetadataFunction } from "remotion";
import { staticFile } from "remotion";
import { z } from "zod";
import { groupWords, type Chunk } from "./lib/chunks";
import { ConfigSchema, resolvePalette, type Palette } from "./lib/config";
import { layoutChunk, type ChunkLayout } from "./lib/layout";
import { FPS } from "./lib/motion";
import { ensureFont, type FontName } from "./fonts";

export const PropsSchema = z.object({
  id: z.string(), // folder in public/videos/
  showSafeZones: z.boolean().default(false), // draw the Shorts UI zones (for checking)
  mask: z.boolean().default(false), // white type on black, no background (for the safe-zone check)
});

export type Beats = { tempo: number; beats: number[]; downbeats: number[] };
export type Meta = { duration: number; audio: string; emphasis?: string[] };

export type VideoData = {
  palette: Palette;
  font: FontName;
  chunks: (Chunk & { layout: ChunkLayout })[];
  beats: number[]; // seconds
  downbeats: number[]; // indices into beats
  audio: string; // path under public/
};

export type Props = z.infer<typeof PropsSchema> & { data?: VideoData };

const getJson = async <T,>(path: string, signal: AbortSignal | undefined, optional = false): Promise<T | null> => {
  const res = await fetch(staticFile(path), { signal });
  if (!res.ok) {
    if (optional) return null;
    throw new Error(`Could not load ${path} (${res.status}). Run: npm run video -- <id>`);
  }
  return (await res.json()) as T;
};

/** Caption[] (word level) -> words with their onset: a hand-edited startMs wins over the DTW timestamp. */
export const captionsToWords = (captions: Caption[]) =>
  captions
    .filter((c) => c.text.trim().length > 0)
    .map((c) => ({ text: c.text.trim(), startMs: c.startMs, endMs: c.endMs }));

export const calculateMetadata: CalculateMetadataFunction<Props> = async ({ props, abortSignal }) => {
  const base = `videos/${props.id}`;
  const [rawConfig, transcript, beats, meta] = await Promise.all([
    getJson<unknown>(`${base}/config.json`, abortSignal),
    getJson<Caption[]>(`${base}/transcript.json`, abortSignal),
    getJson<Beats>(`${base}/beats.json`, abortSignal, true),
    getJson<Meta>(`${base}/meta.json`, abortSignal),
  ]);
  const config = ConfigSchema.parse(rawConfig);
  await ensureFont(config.font);
  const measure = (text: string, fontSize: number) =>
    measureText({ text, fontFamily: config.font, fontSize, validateFontIsLoaded: true }).width;
  const chunks = groupWords(captionsToWords(transcript!), {
    maxWords: config.maxWords,
    pauseMs: config.pauseMs,
    emphasis: [...config.emphasis, ...(meta!.emphasis ?? [])],
  }).map((c) => ({ ...c, layout: layoutChunk(c, measure) }));
  const data: VideoData = {
    palette: resolvePalette(config.palette),
    font: config.font,
    chunks,
    beats: beats?.beats ?? [],
    downbeats: (beats?.downbeats ?? []).map((t) => (beats?.beats ?? []).findIndex((b) => Math.abs(b - t) < 1e-6)),
    audio: `${base}/${meta!.audio}`,
  };
  return {
    durationInFrames: Math.max(1, Math.ceil(meta!.duration * FPS)),
    props: { ...props, data },
    defaultOutName: props.id,
  };
};
