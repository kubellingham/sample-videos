import assert from "node:assert/strict";
import { test } from "node:test";
import { alignToScript, captionsToRawWords, refineOnsets, type RawWord } from "../scripts/lib/words";

test("tokens without a leading space continue the previous word", () => {
  const words = captionsToRawWords([
    { text: "Did", startMs: 0, endMs: 100, timestampMs: 50, confidence: 0.9 },
    { text: " oct", startMs: 100, endMs: 200, timestampMs: 150, confidence: 0.8 },
    { text: "opus", startMs: 200, endMs: 300, timestampMs: 250, confidence: 0.8 },
  ]);
  assert.deepEqual(words.map((w) => [w.text, w.startMs, w.endMs, w.dtwMs]), [["Did", 0, 100, 50], ["octopus", 100, 300, 150]]);
});

test("the script gives spelling and punctuation; missed words are filled in", () => {
  const w = (text: string, s: number): RawWord => ({ text, startMs: s, endMs: s + 200, dtwMs: s, confidence: 1 });
  const heard = [w("did", 0), w("you", 250), w("no", 500), w("an", 750), w("octopus", 1000), w("um", 1300), w("hearts", 1800)];
  const r = alignToScript(heard, "Did you know an octopus has three hearts?");
  assert.deepEqual(r.words.map((x) => x.text), ["Did", "you", "know", "an", "octopus", "has", "three", "hearts?"]);
  assert.equal(r.matched, 5);
  const filled = r.words.slice(5, 7);
  assert.ok(filled[0].startMs >= 1200 && filled[1].endMs <= 1800, "missing words placed in the gap");
});

test("onsets snap to the audio: pause anchors exact, the rest within 25 ms", () => {
  // synthetic speech at 16 kHz: syllable bursts, short dips inside phrases, silences between them
  const sr = 16000;
  const truth: number[] = [];
  const x = new Float32Array(sr * 8);
  let t = 0.3;
  for (let phrase = 0; phrase < 4; phrase++) {
    for (let k = 0; k < 4; k++) {
      truth.push(t * 1000);
      const len = 0.22 + 0.04 * ((phrase + k) % 3);
      for (let i = 0; i < len * sr; i++) {
        const env = Math.min(1, i / (0.01 * sr)) * Math.min(1, (len * sr - i) / (0.04 * sr));
        x[Math.round(t * sr) + i] = 0.4 * env * Math.sin((2 * Math.PI * 160 * i) / sr) * (1 + 0.6 * Math.sin((2 * Math.PI * 480 * i) / sr));
      }
      t += len + 0.07; // a short dip between words
    }
    t += 0.45; // a pause between phrases
  }
  // Whisper-like estimates: DTW times ~150 ms late with +-30 ms jitter
  const jitter = [12, -25, 30, -8, 21, -30, 4, 17, -15, 28, -3, -22, 9, 25, -18, 6];
  const raw: RawWord[] = truth.map((ms, i) => ({ text: `w${i}`, startMs: ms - 120, endMs: ms + 200, dtwMs: ms + 150 + jitter[i], confidence: 1 }));
  const r = refineOnsets(raw, { sampleRate: sr, channels: [x] });
  r.words.forEach((w, i) => {
    const limit = i % 4 === 0 ? 10 : 25; // phrase starts are anchored to the end of the pause
    assert.ok(Math.abs(w.startMs - truth[i]) <= limit, `word ${i}: ${w.startMs} vs ${truth[i].toFixed(0)}`);
  });
  assert.ok(Math.abs(r.bias + 150) < 35, `estimated lateness ${r.bias}`);
});
