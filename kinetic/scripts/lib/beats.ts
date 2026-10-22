// Beat and onset detection for the music track.
//
// 1. Onset strength: spectral flux of the log-compressed STFT magnitude
//    (positive changes only), one value per hop, for the full band and the
//    low band (kick).
// 2. Onsets: peaks of the onset strength above an adaptive threshold.
// 3. Tempo: autocorrelation of the onset strength over 66-130 BPM (exactly
//    one octave, so a pulse can't be taken for its double or half), weighted
//    towards ~100 BPM.
// 4. Beats: dynamic programming (Ellis 2007): the best chain of strong
//    onsets spaced about one beat apart; times refined between hops.
// 5. Downbeats (4/4): the beat phase where kicks and chord changes land.
import { fft, type Pcm } from "./audio";

export type BeatAnalysis = {
  tempo: number; // BPM
  beats: number[]; // seconds
  downbeats: number[]; // seconds (a subset of beats)
  onsets: { t: number; strength: number }[];
};

const SR = 22050;
const MIN_BPM = 66;
const MAX_BPM = 130; // < 2 x MIN_BPM: exactly one octave, no tempo has its double in range
const PRIOR_BPM = 100; // tempo preference inside that range (log-normal, 0.8 octave wide)
const N_FFT = 1024;
const HOP = 256;

/** Frames of log-magnitude spectra (bins up to ~11 kHz). */
const spectrogram = (x: Float32Array) => {
  const win = Float64Array.from({ length: N_FFT }, (_, i) => 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / N_FFT));
  const frames: Float64Array[] = [];
  const re = new Float64Array(N_FFT);
  const im = new Float64Array(N_FFT);
  // frame t is centred on sample t * HOP (pad half a window at the start)
  for (let t = 0; t * HOP < x.length; t++) {
    const start = t * HOP - N_FFT / 2;
    for (let i = 0; i < N_FFT; i++) {
      const j = start + i;
      re[i] = j >= 0 && j < x.length ? x[j] * win[i] : 0;
      im[i] = 0;
    }
    fft(re, im);
    const mag = new Float64Array(N_FFT / 2);
    for (let k = 0; k < N_FFT / 2; k++) mag[k] = Math.log1p(1000 * Math.hypot(re[k], im[k]));
    frames.push(mag);
  }
  return frames;
};

const flux = (frames: Float64Array[], k0: number, k1: number) => {
  const out = new Float64Array(frames.length);
  for (let t = 0; t < frames.length; t++) {
    let s = 0;
    for (let k = k0; k < k1; k++) s += Math.max(0, frames[t][k] - (t > 0 ? frames[t - 1][k] : 0));
    out[t] = s;
  }
  return out;
};

const movingAverage = (x: Float64Array, half: number) => {
  const c = new Float64Array(x.length + 1);
  for (let i = 0; i < x.length; i++) c[i + 1] = c[i] + x[i];
  return Float64Array.from(x, (_, i) => {
    const a = Math.max(0, i - half);
    const b = Math.min(x.length, i + half + 1);
    return (c[b] - c[a]) / (b - a);
  });
};

/** Detrended, normalised onset strength. */
const normalise = (x: Float64Array) => {
  const avg = movingAverage(x, Math.round((0.4 * SR) / HOP));
  const y = Float64Array.from(x, (v, i) => Math.max(0, v - avg[i]));
  const sd = Math.sqrt(y.reduce((s, v) => s + v * v, 0) / y.length) || 1;
  return y.map((v) => v / sd);
};

/** Sub-hop position of a peak at frame i (parabolic interpolation). */
const refine = (x: Float64Array, i: number) => {
  if (i <= 0 || i >= x.length - 1) return i;
  const a = x[i - 1];
  const b = x[i];
  const c = x[i + 1];
  const d = a - 2 * b + c;
  return d < 0 ? i + Math.max(-0.5, Math.min(0.5, (0.5 * (a - c)) / d)) : i;
};

/**
 * Time of frame `f`. Frames are centred on f * HOP; with a Hann window the
 * spectral flux of a transient peaks a quarter window before the transient
 * reaches the centre, so that latency is added back.
 */
export const frameTime = (f: number) => (f * HOP + N_FFT / 4) / SR;

export const analyzeBeats = (pcm: Pcm, meter = 4): BeatAnalysis => {
  if (pcm.sampleRate !== SR) throw new Error(`analyzeBeats needs ${SR} Hz audio`);
  const x = pcm.channels[0];
  const frames = spectrogram(x);
  const binHz = SR / N_FFT;
  const full = normalise(flux(frames, 1, Math.floor(11000 / binHz)));
  const low = normalise(flux(frames, 1, Math.ceil(160 / binHz)));
  const n = full.length;
  const fr = SR / HOP;

  // onsets
  const thr = movingAverage(full, Math.round(0.25 * fr));
  const onsets: { t: number; strength: number }[] = [];
  const gap = Math.round(0.05 * fr);
  for (let i = 1; i < n - 1; i++) {
    let isMax = true;
    for (let k = Math.max(0, i - 3); k <= Math.min(n - 1, i + 3); k++) if (full[k] > full[i]) isMax = false;
    if (isMax && full[i] > thr[i] + 0.5 && (onsets.length === 0 || frameTime(i) - onsets[onsets.length - 1].t > gap / fr)) {
      onsets.push({ t: frameTime(refine(full, i)), strength: Math.round(full[i] * 100) / 100 });
    }
  }

  // tempo by autocorrelation, with a log-normal preference around PRIOR_BPM
  const env = Float64Array.from(full, (v, i) => v + 0.7 * low[i]);
  // One octave of tempos, 66-130 BPM: slow grooves keep their tempo, 120-128 BPM pop stays
  // as it is, and faster music (140+ trap, drum & bass) is punched at half time, as it is felt.
  const minLag = Math.ceil((60 / MAX_BPM) * fr);
  const maxLag = Math.floor((60 / MIN_BPM) * fr);
  const ac = new Float64Array(maxLag + 2);
  for (let lag = minLag - 1; lag <= maxLag + 1; lag++) {
    let s = 0;
    for (let i = 0; i + lag < n; i++) s += env[i] * env[i + lag];
    ac[lag] = s / (n - lag);
  }
  let bestLag = minLag;
  let bestScore = -Infinity;
  for (let lag = minLag; lag <= maxLag; lag++) {
    const bpm = (60 * fr) / lag;
    const prior = Math.exp(-0.5 * (Math.log2(bpm / PRIOR_BPM) / 0.8) ** 2);
    const score = ac[lag] * prior;
    if (ac[lag] >= ac[lag - 1] && ac[lag] >= ac[lag + 1] && score > bestScore) {
      bestScore = score;
      bestLag = lag;
    }
  }
  // beat tracking by dynamic programming
  const track = (period: number) => {
    const tightness = 100;
    const score = new Float64Array(n);
    const back = new Int32Array(n).fill(-1);
    for (let t = 0; t < n; t++) {
      let best = 0;
      let arg = -1;
      for (let tau = Math.max(0, Math.round(t - 2 * period)); tau <= t - Math.round(period / 2); tau++) {
        const v = score[tau] - tightness * Math.log((t - tau) / period) ** 2;
        if (v > best) {
          best = v;
          arg = tau;
        }
      }
      score[t] = env[t] + best;
      back[t] = arg;
    }
    // end on the best-scoring frame within the last beat
    let end = n - 1;
    for (let t = Math.max(0, n - Math.round(period)); t < n; t++) if (score[t] > score[end]) end = t;
    const frames: number[] = [];
    for (let t = end; t >= 0; t = back[t]) frames.unshift(t);
    return frames;
  };
  const period = refine(ac, bestLag); // frames per beat
  const tempo = (60 * fr) / period;
  const beatFrames = track(period);

  // (the chain can run past the end of the music: drop beats in the last 0.1 s)
  const audioEnd = x.length / SR;
  while (beatFrames.length && frameTime(beatFrames[beatFrames.length - 1]) > audioEnd - 0.1) beatFrames.pop();
  const beats = beatFrames.map((f) => frameTime(refine(env, f)));

  // downbeats: the phase whose beats carry the most kick and harmonic change
  const harmonicChange = beatFrames.map((f, k) => {
    const prev = beatFrames[k - 1] ?? Math.max(0, f - Math.round(period));
    const next = beatFrames[k + 1] ?? Math.min(n, f + Math.round(period));
    const avg = (a: number, b: number) => {
      const m = new Float64Array(frames[0].length);
      for (let i = a; i < b; i++) for (let j = 0; j < m.length; j++) m[j] += frames[i][j];
      return m;
    };
    const A = avg(prev, f);
    const B = avg(f, Math.max(f + 1, next));
    let dot = 0;
    let na = 0;
    let nb = 0;
    for (let j = Math.ceil(100 / binHz); j < Math.floor(2000 / binHz); j++) {
      dot += A[j] * B[j];
      na += A[j] * A[j];
      nb += B[j] * B[j];
    }
    return 1 - dot / (Math.sqrt(na * nb) || 1);
  });
  const hcMax = Math.max(...harmonicChange, 1e-9);
  let phase = 0;
  let phaseScore = -Infinity;
  for (let p = 0; p < meter; p++) {
    let s = 0;
    let count = 0;
    beatFrames.forEach((f, k) => {
      if (k % meter === p) {
        s += low[f] / 4 + harmonicChange[k] / hcMax;
        count++;
      }
    });
    if (count && s / count > phaseScore) {
      phaseScore = s / count;
      phase = p;
    }
  }
  const downbeats = beats.filter((_, k) => k % meter === phase);
  const r = (v: number) => Math.round(v * 10000) / 10000;
  return { tempo: Math.round(tempo * 100) / 100, beats: beats.map(r), downbeats: downbeats.map(r), onsets: onsets.map((o) => ({ ...o, t: r(o.t) })) };
};
