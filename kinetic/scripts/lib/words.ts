// From Whisper output to accurate word timings.
//
// Whisper's word times are rough: segment starts wander by +-200 ms and the
// DTW token times run ~150 ms late. Kinetic type slams on the first word of
// each chunk, so the onsets are refined against the audio:
//   1. pauses: where speech resumes after a silence is known exactly; the
//      word Whisper puts nearest becomes an anchor at that time;
//   2. the anchors give this file's DTW lateness (median), which corrects
//      every other word;
//   3. each word then snaps to the strongest rise in loudness near its
//      corrected time (syllable onsets), keeping order.
import type { Caption } from "@remotion/captions";
import { normalize, type Word } from "../../src/lib/chunks";
import type { Pcm } from "./audio";

export type RawWord = { text: string; startMs: number; endMs: number; dtwMs: number | null; confidence: number | null };

/** Whisper captions (tokens) -> words: a token without a leading space continues the previous word. */
export const captionsToRawWords = (captions: Caption[]): RawWord[] => {
  const out: RawWord[] = [];
  for (const c of captions) {
    const glued = out.length > 0 && !/^\s/.test(c.text) && !/^[\s]*$/.test(c.text);
    const text = c.text.trim();
    if (!text) continue;
    if (glued) {
      const w = out[out.length - 1];
      w.text += text;
      w.endMs = c.endMs;
    } else {
      out.push({ text, startMs: c.startMs, endMs: c.endMs, dtwMs: c.timestampMs, confidence: c.confidence });
    }
  }
  return out;
};

/**
 * Give the transcript the script's spelling and punctuation (when the
 * script is known, e.g. TTS): align the two word lists by edit distance;
 * matched and substituted words take the script's text, script words Whisper
 * missed get times between their neighbours, extra Whisper words are dropped.
 */
export const alignToScript = (words: RawWord[], script: string): { words: RawWord[]; matched: number; total: number } => {
  const target = script.split(/\s+/).filter((w) => normalize(w).length > 0);
  const a = words.map((w) => normalize(w.text));
  const b = target.map(normalize);
  const n = a.length;
  const m = b.length;
  const d = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = 0; i <= n; i++) d[i][0] = i;
  for (let j = 0; j <= m; j++) d[0][j] = j;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
  }
  const pairs: [number | null, number | null][] = [];
  for (let i = n, j = m; i > 0 || j > 0; ) {
    if (i > 0 && j > 0 && d[i][j] === d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)) pairs.unshift([--i, --j]);
    else if (j > 0 && d[i][j] === d[i][j - 1] + 1) pairs.unshift([null, --j]);
    else pairs.unshift([--i, null]);
  }
  const out: (RawWord | null)[] = [];
  let matched = 0;
  for (const [i, j] of pairs) {
    if (j === null) continue; // Whisper heard something that isn't in the script
    if (i === null) {
      out.push(null); // fill in below
      continue;
    }
    if (a[i] === b[j]) matched++;
    out.push({ ...words[i], text: target[j] });
  }
  // script words Whisper missed: spread them over the gap between their neighbours
  for (let k = 0; k < out.length; k++) {
    if (out[k] !== null) continue;
    let e = k;
    while (e < out.length && out[e] === null) e++;
    const prev = out[k - 1];
    const next = out[e];
    const from = prev ? prev.endMs : next ? next.startMs - 300 : 0;
    const to = next ? next.startMs : from + 300 * (e - k);
    for (let q = k; q < e; q++) {
      const s = from + ((to - from) * (q - k)) / (e - k);
      const t = from + ((to - from) * (q - k + 1)) / (e - k);
      out[q] = { text: target[q], startMs: Math.round(s), endMs: Math.round(t), dtwMs: null, confidence: null };
    }
    k = e;
  }
  return { words: out as RawWord[], matched, total: m };
};

/** Speech envelope: log energy (dB) every 5 ms, and its rise (onset strength). */
export const speechEnvelope = (pcm: Pcm) => {
  const x = pcm.channels[0];
  const sr = pcm.sampleRate;
  const hop = Math.round(0.005 * sr);
  const win = Math.round(0.02 * sr);
  const db: number[] = [];
  for (let i = 0; i + win <= x.length; i += hop) {
    let s = 0;
    for (let k = 0; k < win; k++) s += x[i + k] * x[i + k];
    db.push(10 * Math.log10(s / win + 1e-12));
  }
  // onset strength: rise over 20 ms, centred so a peak marks where the rise happens
  const rise = db.map((_, i) => Math.max(0, (db[i + 2] ?? db[i]) - (db[i - 2] ?? db[i])));
  return { db, rise, msPerFrame: (hop / sr) * 1000, winMs: (win / sr) * 1000 };
};

/** Silences of at least `minMs`: [startMs, endMs] where the level stays `belowDb` under the speech peak. */
export const findPauses = (env: ReturnType<typeof speechEnvelope>, minMs = 120, belowDb = 38) => {
  const peak = [...env.db].sort((p, q) => q - p)[Math.floor(env.db.length * 0.05)];
  const floor = peak - belowDb;
  const pauses: [number, number][] = [];
  let start = -1;
  for (let i = 0; i <= env.db.length; i++) {
    const quiet = i < env.db.length && env.db[i] < floor;
    if (quiet && start < 0) start = i;
    if (!quiet && start >= 0) {
      // frame i is the first loud frame; its window started at i * ms, the energy rises inside it
      const s = start * env.msPerFrame + env.winMs / 2;
      const e = i * env.msPerFrame + env.winMs / 2;
      if (e - s >= minMs) pauses.push([s, e]);
      start = -1;
    }
  }
  return pauses;
};

/**
 * Refine word onsets (see the top of this file). Returns words whose
 * startMs is the refined onset and whose endMs reaches the next word (or
 * the start of the pause after it).
 */
export const refineOnsets = (words: RawWord[], pcm: Pcm, opts: { anchors?: boolean } = {}) => {
  const env = speechEnvelope(pcm);
  const pauses = findPauses(env);
  const est = words.map((w) => w.dtwMs ?? w.startMs);
  const onset: (number | null)[] = words.map(() => null);
  // 1. anchors: speech resuming after a pause
  const anchorErr: number[] = [];
  const totalMs = env.db.length * env.msPerFrame;
  if (opts.anchors !== false) {
    for (const [, end] of pauses) {
      if (end >= totalMs - 1) continue; // trailing silence: nothing resumes
      let best = -1;
      for (let i = 0; i < words.length; i++) if (best < 0 || Math.abs(est[i] - end) < Math.abs(est[best] - end)) best = i;
      if (best >= 0 && Math.abs(est[best] - end) < 400 && onset[best] === null) {
        onset[best] = end;
        anchorErr.push(end - est[best]);
      }
    }
  }
  // 2. this file's lateness of the estimates
  anchorErr.sort((p, q) => p - q);
  const words0 = words.some((w) => w.dtwMs !== null);
  const bias = anchorErr.length >= 2 ? anchorErr[anchorErr.length >> 1] : words0 ? -140 : 0;
  // 3. snap to the strongest rise near the corrected time, in order
  const reach = 90; // ms
  for (let i = 0; i < words.length; i++) {
    if (onset[i] !== null) continue;
    const prev = i > 0 ? (onset[i - 1] ?? est[i - 1] + bias) : 0;
    let nextAnchor = Infinity;
    for (let k = i + 1; k < words.length; k++) if (onset[k] !== null) { nextAnchor = onset[k]!; break; }
    const target = est[i] + bias;
    const lo = Math.max(target - reach, prev + 60);
    const hi = Math.min(target + reach, nextAnchor - 60);
    let bestT = Math.min(Math.max(target, lo), Math.max(lo, hi));
    let bestV = 0;
    for (let f = Math.ceil(lo / env.msPerFrame); f * env.msPerFrame <= hi; f++) {
      const v = env.rise[f] ?? 0;
      // prefer rises near the target: a rise 90 ms away must be 1.5x as strong
      const weighted = v / (1 + (0.5 * Math.abs(f * env.msPerFrame - target)) / reach);
      if (weighted > bestV) {
        bestV = weighted;
        bestT = f * env.msPerFrame + env.winMs / 2;
      }
    }
    // inside a pause? then it starts where the pause ends
    for (const [s, e] of pauses) if (bestT > s && bestT < e) bestT = e;
    onset[i] = Math.max(bestT, prev + 50);
  }
  // ends: the next word's onset, or where the speech stops before a pause
  const speechEnd = (from: number, to: number) => {
    for (const [s] of pauses) if (s > from && s < to) return s;
    return to;
  };
  const lastEnd = pauses.length && pauses[pauses.length - 1][1] >= totalMs - 1 ? pauses[pauses.length - 1][0] : totalMs;
  const refined: Word[] = words.map((w, i) => ({
    text: w.text,
    startMs: Math.round(onset[i]!),
    endMs: Math.round(i + 1 < words.length ? speechEnd(onset[i]!, onset[i + 1]!) : Math.max(onset[i]! + 150, lastEnd)),
  }));
  return { words: refined, pauses, bias, anchors: anchorErr.length };
};

/** Word list -> Remotion Caption[] (a leading space on every word but the first). */
export const toCaptionJson = (words: Word[], confidence: (number | null)[] = []): Caption[] =>
  words.map((w, i) => ({
    text: (i === 0 ? "" : " ") + w.text,
    startMs: w.startMs,
    endMs: w.endMs,
    timestampMs: w.startMs,
    confidence: confidence[i] ?? null,
  }));
