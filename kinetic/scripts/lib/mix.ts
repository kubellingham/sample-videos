// Voice + music: the music is loudness-matched under the voice, ducked
// while the voice talks, and the sum is mastered to the target loudness.
import type { Pcm } from "./audio";
import { gainDb, integratedLufs, normalize } from "./loudness";

export type MixOptions = {
  duration: number; // seconds of output
  musicDb: number; // music level relative to the voice's loudness (when not ducked)
  duckDb: number; // extra music gain while the voice is active (negative)
  loudness: number; // target integrated LUFS
  fadeOutS?: number;
};

/**
 * Voice activity as a 0..1 envelope (10 ms steps): speech above the voice's
 * own floor, with a 60 ms look-ahead (the music dips just before a word),
 * a 250 ms hold between words and a 350 ms release.
 */
export const voiceActivity = (voice: Pcm, n: number) => {
  const x = voice.channels[0];
  const sr = voice.sampleRate;
  const step = Math.round(0.01 * sr);
  const frames = Math.ceil(n / step);
  const db = Float64Array.from({ length: frames }, (_, f) => {
    let s = 0;
    let c = 0;
    for (let i = f * step; i < Math.min(x.length, (f + 2) * step); i++) {
      s += x[i] * x[i];
      c++;
    }
    return c ? 10 * Math.log10(s / c + 1e-12) : -120;
  });
  const peak = [...db].sort((a, b) => b - a)[Math.floor(frames * 0.05)] ?? -20;
  const active = db.map((v) => (v > peak - 35 ? 1 : 0));
  const target = Float64Array.from(active, (_, f) => {
    for (let k = f - 25; k <= f + 6; k++) if (active[k]) return 1; // 60 ms look-ahead, 250 ms hold
    return 0;
  });
  const env = new Float64Array(frames);
  let v = 0;
  for (let f = 0; f < frames; f++) {
    const k = target[f] > v ? 1 - Math.exp(-1 / 3) : 1 - Math.exp(-1 / 35); // attack ~30 ms, release ~350 ms
    v += (target[f] - v) * k;
    env[f] = v;
  }
  // per sample (linear between 10 ms steps)
  return Float32Array.from({ length: n }, (_, i) => {
    const p = i / step;
    const a = Math.floor(p);
    const b = Math.min(frames - 1, a + 1);
    return env[a] + (env[b] - env[a]) * (p - a);
  });
};

/** Trim or pad `x` to `n` samples; with `loopFadeS` > 0 a short track loops instead, crossfading each repeat in. */
export const fitLength = (x: Float32Array, n: number, sr: number, loopFadeS = 0) => {
  if (x.length >= n) return x.slice(0, n);
  const out = new Float32Array(n);
  if (loopFadeS <= 0) {
    out.set(x);
    return out;
  }
  const fade = Math.max(1, Math.min(Math.round(loopFadeS * sr), Math.floor(x.length / 4)));
  const period = x.length - fade;
  for (let s = 0; s < n; s += period) {
    const last = s + period >= n;
    for (let i = 0; i < x.length && s + i < n; i++) {
      const fadeIn = s > 0 && i < fade ? i / fade : 1;
      const fadeOut = !last && i >= period ? (x.length - i) / fade : 1;
      out[s + i] += x[i] * fadeIn * fadeOut;
    }
  }
  return out;
};

export const mixTracks = (voice: Pcm, music: Pcm | null, o: MixOptions): { pcm: Pcm; lufs: number; voiceLufs: number; duckedDb: number | null } => {
  const sr = 48000;
  if (voice.sampleRate !== sr || (music && music.sampleRate !== sr)) throw new Error("mixTracks expects 48 kHz");
  const n = Math.round(o.duration * sr);
  const v = fitLength(voice.channels[0], n, sr); // the voice is padded or trimmed, never looped
  const voiceStereo: Pcm = { sampleRate: sr, channels: [v, Float32Array.from(v)] };
  const voiceLufs = integratedLufs(voiceStereo);
  const L = Float32Array.from(v);
  const R = Float32Array.from(v);
  let duckedDb: number | null = null;
  if (music) {
    const mL = fitLength(music.channels[0], n, sr, 0.5);
    const mR = fitLength(music.channels[1] ?? music.channels[0], n, sr, 0.5);
    const musicPcm: Pcm = { sampleRate: sr, channels: [mL, mR] };
    const level = gainDb(musicPcm, voiceLufs + o.musicDb - integratedLufs(musicPcm));
    const act = voiceActivity({ sampleRate: sr, channels: [v] }, n);
    const fadeN = Math.round((o.fadeOutS ?? 1.5) * sr);
    let duckSum = 0;
    let duckCount = 0;
    for (let i = 0; i < n; i++) {
      const g = 10 ** ((o.duckDb * act[i]) / 20) * (i > n - fadeN ? (n - i) / fadeN : 1);
      L[i] += level.channels[0][i] * g;
      R[i] += level.channels[1][i] * g;
      if (act[i] > 0.99) {
        duckSum += 20 * Math.log10(g);
        duckCount++;
      }
    }
    duckedDb = duckCount ? duckSum / duckCount : null;
  }
  const { pcm, lufs } = normalize({ sampleRate: sr, channels: [L, R] }, o.loudness, -1.5);
  return { pcm, lufs, voiceLufs, duckedDb };
};
