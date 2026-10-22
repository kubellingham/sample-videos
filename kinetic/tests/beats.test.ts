import assert from "node:assert/strict";
import { test } from "node:test";
import { analyzeBeats } from "../scripts/lib/beats";
import { makeBeat } from "../scripts/make-beat";

/** The generated beat, resampled to the analyser's 22.05 kHz (mono). */
const at22k = (bpm: number) => {
  const { pcm, beats, downbeats } = makeBeat({ bpm, seconds: 24 });
  const x = pcm.channels[0];
  const ratio = 48000 / 22050;
  const y = Float32Array.from({ length: Math.floor(x.length / ratio) }, (_, i) => {
    const p = i * ratio;
    const a = Math.floor(p);
    return x[a] + (x[Math.min(x.length - 1, a + 1)] - x[a]) * (p - a);
  });
  return { pcm: { sampleRate: 22050, channels: [y] }, beats, downbeats };
};

for (const bpm of [72, 96, 120, 128]) {
  test(`finds the tempo, every beat and the bar lines at ${bpm} BPM`, () => {
    const { pcm, beats, downbeats } = at22k(bpm);
    const a = analyzeBeats(pcm);
    assert.ok(Math.abs(a.tempo - bpm) < bpm * 0.01, `tempo ${a.tempo}`);
    const err = a.beats.map((t) => Math.min(...beats.map((b) => Math.abs(b - t))));
    assert.ok(err.filter((e) => e < 0.02).length >= beats.length - 1, `beats within 20 ms: ${err.filter((e) => e < 0.02).length}/${beats.length}`);
    assert.ok(a.beats.length <= beats.length);
    assert.ok(a.downbeats.every((t) => downbeats.some((d) => Math.abs(d - t) < 0.03)), "downbeats on bar lines");
  });
}

test("fast music is tracked at half time (one punch per half note at 150 BPM)", () => {
  const { pcm } = at22k(150);
  assert.ok(Math.abs(analyzeBeats(pcm).tempo - 75) < 1);
});
