// ITU-R BS.1770 loudness (K-weighting, 400 ms blocks, absolute and relative
// gates) and a look-ahead peak limiter, for 48 kHz audio.
import type { Pcm } from "./audio";

// K-weighting at 48 kHz: high shelf, then the RLB high pass
const SHELF = { b: [1.53512485958697, -2.69169618940638, 1.19839281085285], a: [1, -1.69065929318241, 0.73248077421585] };
const HIGHPASS = { b: [1, -2, 1], a: [1, -1.99004745483398, 0.99007225036621] };

const filter = (x: Float32Array, f: { b: number[]; a: number[] }) => {
  const y = new Float64Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const v = f.b[0] * x[i] + f.b[1] * x1 + f.b[2] * x2 - f.a[1] * y1 - f.a[2] * y2;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = v;
    y[i] = v;
  }
  return Float32Array.from(y);
};

const lufsOf = (power: number) => -0.691 + 10 * Math.log10(Math.max(power, 1e-20));

/** Integrated loudness in LUFS. */
export const integratedLufs = (pcm: Pcm): number => {
  if (pcm.sampleRate !== 48000) throw new Error("integratedLufs expects 48 kHz audio");
  const weighted = pcm.channels.map((c) => filter(filter(c, SHELF), HIGHPASS));
  const n = weighted[0].length;
  const block = 19200; // 400 ms
  const hop = 4800; // 75% overlap
  const cum = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (const w of weighted) s += w[i] * w[i];
    cum[i + 1] = cum[i] + s;
  }
  const blocks: number[] = [];
  for (let s = 0; s + block <= n; s += hop) blocks.push((cum[s + block] - cum[s]) / block);
  const gated = blocks.filter((z) => lufsOf(z) > -70);
  if (!gated.length) return -70;
  const rel = lufsOf(gated.reduce((p, z) => p + z, 0) / gated.length) - 10;
  const kept = gated.filter((z) => lufsOf(z) > rel);
  return lufsOf(kept.reduce((p, z) => p + z, 0) / kept.length);
};

/** out[i] = min(x[i .. i + w - 1]) (van Herk / Gil-Werman, O(n)); x is padded with +inf. */
const movingMin = (x: Float64Array, w: number) => {
  const n = x.length;
  const m = Math.ceil((n + w) / w) * w;
  const pre = new Float64Array(m);
  const suf = new Float64Array(m);
  for (let b = 0; b < m; b += w) {
    for (let i = b; i < b + w; i++) pre[i] = Math.min(i < n ? x[i] : Infinity, i > b ? pre[i - 1] : Infinity);
    for (let i = b + w - 1; i >= b; i--) suf[i] = Math.min(i < n ? x[i] : Infinity, i < b + w - 1 ? suf[i + 1] : Infinity);
  }
  return Float64Array.from({ length: n }, (_, i) => Math.min(suf[i], pre[Math.min(i + w - 1, m - 1)]));
};

/**
 * Look-ahead limiter (channels linked): the gain dips smoothly over
 * `windowS` either side of a peak. A moving minimum of the needed gain over
 * twice the window, then a moving average over the window, never exceeds
 * what any sample in reach needs, so nothing ends up above the ceiling.
 */
export const limit = (pcm: Pcm, ceilingDb = -1.5, windowS = 0.015): Pcm => {
  const ceiling = 10 ** (ceilingDb / 20);
  const n = pcm.channels[0].length;
  const need = new Float64Array(n);
  let any = false;
  for (let i = 0; i < n; i++) {
    let p = 0;
    for (const c of pcm.channels) p = Math.max(p, Math.abs(c[i]));
    need[i] = p > ceiling ? ceiling / p : 1;
    if (need[i] < 1) any = true;
  }
  if (!any) return pcm;
  const h = Math.max(1, Math.round(windowS * pcm.sampleRate));
  const ext = new Float64Array(n + 4 * h).fill(1);
  ext.set(need, 2 * h);
  const g1 = movingMin(ext, 2 * h + 1);
  const cum = new Float64Array(g1.length + 1);
  for (let i = 0; i < g1.length; i++) cum[i + 1] = cum[i] + g1[i];
  const w = 2 * h + 1;
  const gain = Float64Array.from({ length: n }, (_, i) => (cum[i + w] - cum[i]) / w);
  return { sampleRate: pcm.sampleRate, channels: pcm.channels.map((c) => Float32Array.from(c, (v, i) => v * gain[i])) };
};

export const gainDb = (pcm: Pcm, db: number): Pcm => {
  const g = 10 ** (db / 20);
  return { sampleRate: pcm.sampleRate, channels: pcm.channels.map((c) => Float32Array.from(c, (v) => v * g)) };
};

/** Bring `pcm` to `targetLufs` and keep peaks under `ceilingDb` (refines the gain to make up for limiting). */
export const normalize = (pcm: Pcm, targetLufs = -14, ceilingDb = -1.5): { pcm: Pcm; lufs: number } => {
  const level = integratedLufs(pcm);
  let gain = targetLufs - level;
  let out = limit(gainDb(pcm, gain), ceilingDb);
  for (let k = 0; k < 5; k++) {
    const err = targetLufs - integratedLufs(out);
    if (Math.abs(err) < 0.03) break;
    gain += err;
    out = limit(gainDb(pcm, gain), ceilingDb);
  }
  return { pcm: out, lufs: integratedLufs(out) };
};
