import assert from "node:assert/strict";
import { test } from "node:test";
import { integratedLufs, limit } from "../scripts/lib/loudness";
import { fitLength, mixTracks, voiceActivity } from "../scripts/lib/mix";
import { makeBeat } from "../scripts/make-beat";

const SR = 48000;

test("loudness meter: the BS.1770 reference tone", () => {
  const n = SR * 5;
  const s = Float32Array.from({ length: n }, (_, i) => 0.1 * Math.sin((2 * Math.PI * 997 * i) / SR));
  assert.ok(Math.abs(integratedLufs({ sampleRate: SR, channels: [s, s] }) + 20) < 0.05);
});

test("limiter keeps every sample under the ceiling", () => {
  const x = Float32Array.from({ length: SR }, (_, i) => (i === 1000 ? 3 : 0.5 * Math.sin(i / 7)));
  const y = limit({ sampleRate: SR, channels: [x] }, -1.5);
  assert.ok(Math.max(...y.channels[0].map(Math.abs)) <= 10 ** (-1.5 / 20) + 1e-6);
});

/** Speech-like test signal: buzzy syllables in phrases, silent between phrases. */
const fakeVoice = (seconds: number) => {
  const x = new Float32Array(seconds * SR);
  for (let i = 0; i < x.length; i++) {
    const t = i / SR;
    const phrase = t % 4 < 2.6; // 2.6 s of talking, 1.4 s pause
    const syl = Math.sin(Math.PI * ((t * 4) % 1)) ** 2;
    x[i] = phrase ? 0.3 * syl * Math.sin(2 * Math.PI * 140 * t) * (1 + 0.5 * Math.sin(2 * Math.PI * 420 * t)) : 0;
  }
  return { sampleRate: SR, channels: [x] };
};

test("mix: -14 LUFS, peaks under -1.5 dBFS, music ducked under the voice", () => {
  const voice = fakeVoice(12);
  const music = makeBeat({ bpm: 96, seconds: 6 }).pcm; // shorter than the video: it loops
  const r = mixTracks(voice, music, { duration: 12, musicDb: -4, duckDb: -12, loudness: -14 });
  assert.ok(Math.abs(r.lufs + 14) < 0.1, `lufs ${r.lufs}`);
  const peak = Math.max(...r.pcm.channels.map((c) => c.reduce((m, v) => Math.max(m, Math.abs(v)), 0)));
  assert.ok(peak <= 10 ** (-1.5 / 20) + 1e-6);
  assert.ok(r.duckedDb !== null && Math.abs(r.duckedDb + 12) < 0.5, `ducked ${r.duckedDb}`);
  const act = voiceActivity(voice, 12 * SR);
  assert.ok(act[Math.round(1.0 * SR)] > 0.95, "ducked while talking");
  // talking stops at 2.6 s; after a 250 ms hold the music comes back with a 350 ms release
  assert.ok(act[Math.round(3.1 * SR)] < 0.6 && act[Math.round(3.7 * SR)] < 0.1, "back up in the pause");
  assert.ok(act[Math.round(3.95 * SR)] > 0.3, "ducks again just before the next phrase (look-ahead)");
});

test("a short music track loops with a crossfade", () => {
  const x = Float32Array.from({ length: 1000 }, () => 1);
  const y = fitLength(x, 2500, 1000, 0.1); // 100-sample crossfades
  assert.equal(y.length, 2500);
  assert.ok(y.every((v) => Math.abs(v - 1) < 1e-6), "constant level through the loop points");
});
