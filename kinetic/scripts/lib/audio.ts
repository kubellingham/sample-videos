// Audio helpers for the pipeline: decode anything with ffmpeg, write WAVs, hash files.
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

export type Pcm = { sampleRate: number; channels: Float32Array[] };

/** Decode any audio file to float PCM (resampled, `channels` channels). */
export const decode = (path: string, sampleRate = 48000, channels = 1): Pcm => {
  const out = spawnSync("ffmpeg", ["-v", "error", "-i", path, "-f", "f32le", "-ac", String(channels), "-ar", String(sampleRate), "-"], {
    maxBuffer: 1 << 30,
  });
  if (out.status !== 0) throw new Error(`ffmpeg could not decode ${path}: ${out.stderr.toString()}`);
  const buf = out.stdout;
  const all = new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
  const n = all.length / channels;
  const chans = Array.from({ length: channels }, (_, c) => {
    const ch = new Float32Array(n);
    for (let i = 0; i < n; i++) ch[i] = all[i * channels + c];
    return ch;
  });
  return { sampleRate, channels: chans };
};

/** Write 16-bit PCM WAV. */
export const writeWav = (path: string, pcm: Pcm) => {
  const nCh = pcm.channels.length;
  const n = pcm.channels[0].length;
  const data = Buffer.alloc(n * nCh * 2);
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < nCh; c++) {
      const v = Math.max(-1, Math.min(1, pcm.channels[c][i]));
      data.writeInt16LE(Math.round(v * 32767), (i * nCh + c) * 2);
    }
  }
  const h = Buffer.alloc(44);
  h.write("RIFF", 0);
  h.writeUInt32LE(36 + data.length, 4);
  h.write("WAVE", 8);
  h.write("fmt ", 12);
  h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20);
  h.writeUInt16LE(nCh, 22);
  h.writeUInt32LE(pcm.sampleRate, 24);
  h.writeUInt32LE(pcm.sampleRate * nCh * 2, 28);
  h.writeUInt16LE(nCh * 2, 32);
  h.writeUInt16LE(16, 34);
  h.write("data", 36);
  h.writeUInt32LE(data.length, 40);
  writeFileSync(path, Buffer.concat([h, data]));
};

export const duration = (path: string): number =>
  Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).toString().trim());

export const sha = (...parts: (string | Buffer)[]) => {
  const h = createHash("sha256");
  for (const p of parts) h.update(p);
  return h.digest("hex").slice(0, 16);
};
export const fileSha = (path: string) => sha(readFileSync(path));

/** Integrated loudness (LUFS) and true peak (dBTP) as measured by ffmpeg's ebur128 filter. */
export const measureLoudness = (path: string): { lufs: number; truePeak: number } => {
  const out = spawnSync("ffmpeg", ["-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]);
  const text = out.stderr.toString();
  const summary = text.slice(text.lastIndexOf("Summary:"));
  const lufs = Number(/I:\s+(-?[\d.]+) LUFS/.exec(summary)?.[1]);
  const truePeak = Number(/Peak:\s+(-?[\d.]+) dBFS/.exec(summary)?.[1]);
  return { lufs, truePeak };
};

/** In-place iterative radix-2 FFT (re, im of length 2^k). */
export const fft = (re: Float64Array, im: Float64Array) => {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wr = Math.cos(ang);
    const wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1;
      let ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k;
        const b = a + len / 2;
        const tr = re[b] * cr - im[b] * ci;
        const ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
        const ncr = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = ncr;
      }
    }
  }
};
