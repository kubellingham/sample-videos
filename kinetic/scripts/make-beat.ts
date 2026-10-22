// Synthesise a royalty-free background beat (for samples and tests).
//
//   npx tsx scripts/make-beat.ts <out.wav> [--bpm 96] [--seconds 32] [--seed 7]
//
// 4/4 at `bpm`: kick, snare, hats, a sidechained bass and soft Rhodes-like
// chords over vi-IV-I-V in C, and a quiet arpeggio. Beat k is exactly at
// k * 60 / bpm seconds (bar lines every 4 beats), which the beat tests use
// as ground truth.
import { writeWav } from "./lib/audio";

const SR = 48000;

const mulberry32 = (seed: number) => () => {
  seed |= 0;
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

/** RBJ biquad, applied in place. */
const biquad = (x: Float32Array, type: "lowpass" | "highpass" | "bandpass", f0: number, q: number) => {
  const w = (2 * Math.PI * f0) / SR;
  const alpha = Math.sin(w) / (2 * q);
  const cos = Math.cos(w);
  let b0, b1, b2;
  if (type === "lowpass") [b0, b1, b2] = [(1 - cos) / 2, 1 - cos, (1 - cos) / 2];
  else if (type === "highpass") [b0, b1, b2] = [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2];
  else [b0, b1, b2] = [alpha, 0, -alpha];
  const a0 = 1 + alpha;
  const a1 = -2 * cos;
  const a2 = 1 - alpha;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const y = (b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = y;
    x[i] = y;
  }
  return x;
};

const hz = (midi: number) => 440 * 2 ** ((midi - 69) / 12);

export const makeBeat = ({ bpm = 96, seconds = 32, seed = 7 } = {}) => {
  const rnd = mulberry32(seed);
  const beat = 60 / bpm;
  const step = beat / 4; // 16th
  const bars = Math.ceil(seconds / (4 * beat));
  const n = Math.round(bars * 4 * beat * SR);
  const L = new Float32Array(n);
  const R = new Float32Array(n);
  const duckEnv = new Float32Array(n).fill(1);
  const add = (sig: Float32Array, at: number, gain: number, pan = 0) => {
    const i0 = Math.round(at * SR);
    const gl = gain * Math.cos(((pan + 1) * Math.PI) / 4) * Math.SQRT2;
    const gr = gain * Math.sin(((pan + 1) * Math.PI) / 4) * Math.SQRT2;
    for (let i = 0; i < sig.length && i0 + i < n; i++) {
      L[i0 + i] += sig[i] * gl;
      R[i0 + i] += sig[i] * gr;
    }
  };
  const noise = (len: number) => Float32Array.from({ length: Math.round(len * SR) }, () => rnd() * 2 - 1);

  const kick = (() => {
    const len = Math.round(0.42 * SR);
    const out = new Float32Array(len);
    let ph = 0;
    for (let i = 0; i < len; i++) {
      const t = i / SR;
      const f = 46 + 110 * Math.exp(-t / 0.035);
      ph += (2 * Math.PI * f) / SR;
      out[i] = Math.sin(ph) * Math.exp(-t / 0.22) * Math.min(1, t / 0.0015);
    }
    const click = biquad(noise(0.004), "highpass", 2500, 0.7);
    for (let i = 0; i < click.length; i++) out[i] += 0.5 * click[i] * (1 - i / click.length);
    return out;
  })();
  const snare = (() => {
    const body = biquad(noise(0.25), "bandpass", 1900, 0.7);
    const out = new Float32Array(body.length);
    for (let i = 0; i < out.length; i++) {
      const t = i / SR;
      out[i] = 1.4 * body[i] * Math.exp(-t / 0.11) + 0.5 * Math.sin(2 * Math.PI * 185 * t) * Math.exp(-t / 0.06);
    }
    return out;
  })();
  const hat = (open: boolean) => {
    const s = biquad(noise(open ? 0.3 : 0.06), "highpass", 7500, 0.7);
    return s.map((v, i) => v * Math.exp(-i / SR / (open ? 0.12 : 0.018)));
  };
  const keys = (midi: number, len: number) => {
    const out = new Float32Array(Math.round(len * SR));
    const f = hz(midi);
    for (let i = 0; i < out.length; i++) {
      const t = i / SR;
      const env = Math.min(1, t / 0.006) * (0.65 * Math.exp(-t / 0.5) + 0.35 * Math.exp(-t / 2.2)) * Math.min(1, (len - t) / 0.08);
      const trem = 1 + 0.08 * Math.sin(2 * Math.PI * 4.5 * t);
      out[i] = env * trem * (Math.sin(2 * Math.PI * f * t) + 0.35 * Math.sin(4 * Math.PI * f * t) * Math.exp(-t / 0.3) + 0.12 * Math.sin(6 * Math.PI * f * t) * Math.exp(-t / 0.15));
    }
    return out;
  };
  const bassNote = (midi: number, len: number) => {
    const out = new Float32Array(Math.round(len * SR));
    const f = hz(midi);
    for (let i = 0; i < out.length; i++) {
      const t = i / SR;
      const env = Math.min(1, t / 0.008) * Math.exp(-t / 0.9) * Math.min(1, (len - t) / 0.03);
      out[i] = env * (Math.sin(2 * Math.PI * f * t) + 0.45 * Math.sin(4 * Math.PI * f * t) + 0.18 * Math.sin(6 * Math.PI * f * t));
    }
    return out;
  };
  const pluck = (midi: number) => {
    const out = new Float32Array(Math.round(0.5 * SR));
    const f = hz(midi);
    for (let i = 0; i < out.length; i++) {
      const t = i / SR;
      out[i] = Math.exp(-t / 0.16) * Math.min(1, t / 0.002) * (Math.sin(2 * Math.PI * f * t) + 0.3 * Math.sin(4 * Math.PI * f * t) * Math.exp(-t / 0.05));
    }
    return out;
  };

  // vi-IV-I-V in C (Am F C G): chord tones around C4, bass roots in octave 2
  const prog = [
    { root: 45, tones: [57, 60, 64, 67] },
    { root: 41, tones: [57, 60, 65, 69] },
    { root: 48, tones: [55, 60, 64, 67] },
    { root: 43, tones: [55, 59, 62, 67] },
  ];
  const music = { L: new Float32Array(n), R: new Float32Array(n) };
  const addMusic = (sig: Float32Array, at: number, gain: number, pan = 0) => {
    const i0 = Math.round(at * SR);
    const gl = gain * Math.cos(((pan + 1) * Math.PI) / 4) * Math.SQRT2;
    const gr = gain * Math.sin(((pan + 1) * Math.PI) / 4) * Math.SQRT2;
    for (let i = 0; i < sig.length && i0 + i < n; i++) {
      music.L[i0 + i] += sig[i] * gl;
      music.R[i0 + i] += sig[i] * gr;
    }
  };
  const arpPattern = [0, 2, 1, 3, 2, 1, 3, 0];
  for (let bar = 0; bar < bars; bar++) {
    const t0 = bar * 4 * beat;
    const c = prog[bar % prog.length];
    const fill = bar % 4 === 3;
    for (let s = 0; s < 16; s++) {
      const t = t0 + s * step;
      if (s === 0 || s === 8 || s === 10 || (fill && s === 15)) {
        add(kick, t, s === 0 ? 0.95 : 0.8);
        const i0 = Math.round(t * SR);
        for (let i = 0; i < 0.3 * SR && i0 + i < n; i++) duckEnv[i0 + i] = Math.min(duckEnv[i0 + i], 1 - 0.55 * Math.exp(-i / SR / 0.11));
      }
      if (s === 4 || s === 12) add(snare, t, 0.55, 0.05);
      if (fill && (s === 13 || s === 14)) add(snare, t, 0.22, -0.1);
      if (s % 2 === 0 && s !== 14) add(hat(false), t, s % 4 === 0 ? 0.22 : 0.15, 0.35);
      if (s % 2 === 1) add(hat(false), t, 0.06 + 0.04 * rnd(), 0.4);
      if (s === 14) add(hat(true), t, 0.16, 0.35);
    }
    // bass on the kick pattern
    addMusic(bassNote(c.root, 8 * step), t0, 0.5);
    addMusic(bassNote(c.root, 2 * step), t0 + 8 * step, 0.42);
    addMusic(bassNote(c.root + (bar % 2 ? 7 : 12), 6 * step), t0 + 10 * step, 0.38);
    // chords: a stab on 1 and a lighter one on the "and" of 3
    c.tones.forEach((m, k) => {
      addMusic(keys(m, 4 * beat), t0 + 0.004 * k, 0.11, k % 2 ? 0.25 : -0.25);
      addMusic(keys(m, 2 * beat), t0 + 10 * step + 0.004 * k, 0.05, k % 2 ? -0.2 : 0.2);
    });
    // a quiet arpeggio on 8ths, an octave up
    for (let e = 0; e < 8; e++) addMusic(pluck(c.tones[arpPattern[e]] + 12), t0 + e * 2 * step, 0.07, e % 2 ? 0.5 : -0.5);
  }
  for (let i = 0; i < n; i++) {
    L[i] += music.L[i] * duckEnv[i];
    R[i] += music.R[i] * duckEnv[i];
  }
  // gentle soft clip and peak normalise to -1 dBFS
  let peak = 0;
  for (let i = 0; i < n; i++) {
    L[i] = Math.tanh(L[i] * 1.2);
    R[i] = Math.tanh(R[i] * 1.2);
    peak = Math.max(peak, Math.abs(L[i]), Math.abs(R[i]));
  }
  const g = 10 ** (-1 / 20) / peak;
  for (let i = 0; i < n; i++) {
    L[i] *= g;
    R[i] *= g;
  }
  const beats = Array.from({ length: bars * 4 }, (_, k) => k * beat);
  return { pcm: { sampleRate: SR, channels: [L, R] }, beats, downbeats: beats.filter((_, k) => k % 4 === 0) };
};

if (process.argv[1]?.endsWith("make-beat.ts")) {
  const args = process.argv.slice(2);
  const out = args.find((a) => !a.startsWith("--") && !/^\d/.test(a)) ?? "beat.wav";
  const opt = (name: string, def: number) => {
    const i = args.indexOf(`--${name}`);
    return i >= 0 ? Number(args[i + 1]) : def;
  };
  const { pcm, beats } = makeBeat({ bpm: opt("bpm", 96), seconds: opt("seconds", 32), seed: opt("seed", 7) });
  writeWav(out, pcm);
  console.log(`wrote ${out}: ${(pcm.channels[0].length / SR).toFixed(2)} s, ${beats.length} beats at ${opt("bpm", 96)} BPM`);
}
