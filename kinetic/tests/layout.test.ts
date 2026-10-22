import assert from "node:assert/strict";
import { test } from "node:test";
import { groupWords } from "../src/lib/chunks";
import { layoutChunk, worstCaseBounds } from "../src/lib/layout";
import { HEIGHT, SAFE, slam, springAt, WIDTH } from "../src/lib/motion";

// a condensed display face is ~0.45 em per capital; a space ~0.2 em
const measure = (text: string, size: number) => size * (text === " " ? 0.2 : 0.45 * text.length);

const sample = "Did you know an octopus has three hearts? Extraordinary cephalopods. Supercalifragilistic. A I O. Blood is blue, thanks to copper-based hemocyanin!";

test("every chunk, at its largest (slam, punch, shake, rotation), stays out of the Shorts UI", () => {
  let t = 0;
  const words = sample.split(" ").map((w) => ({ text: w, startMs: (t += 300), endMs: t + 280 }));
  for (const emphasis of [[], sample.split(" ")]) {
    for (const chunk of groupWords(words, { emphasis })) {
      const layout = layoutChunk(chunk, measure);
      const b = worstCaseBounds(layout, chunk.emphasis);
      assert.ok(b.right <= SAFE.right * WIDTH + 1e-6, `right ${b.right.toFixed(0)} for ${chunk.words.map((w) => w.display).join(" ")}`);
      assert.ok(b.bottom <= SAFE.bottom * HEIGHT + 1e-6, `bottom ${b.bottom.toFixed(0)}`);
      assert.ok(b.left >= SAFE.left * WIDTH - 1e-6 && b.top >= SAFE.top * HEIGHT - 1e-6);
      assert.ok(layout.lines.length >= 1 && layout.lines.length <= 3);
    }
  }
});

test("short words are set huge, long ones fitted to the width", () => {
  const one = groupWords([{ text: "ok", startMs: 0, endMs: 300 }])[0];
  const long = groupWords([{ text: "Supercalifragilistic", startMs: 0, endMs: 300 }])[0];
  assert.equal(layoutChunk(one, measure).lines[0].fontSize, 360);
  assert.ok(layoutChunk(long, measure).lines[0].fontSize < 120);
});

test("the slam settles: spring reaches 1, scale returns to 1, rotation to the resting tilt", () => {
  assert.ok(Math.abs(springAt(2) - 1) < 1e-4);
  const end = slam(2, 0, false);
  assert.ok(Math.abs(end.scale - 1) < 1e-4 && Math.abs(Math.abs(end.rotate) - 1.5) < 1e-3 && end.opacity === 1);
  assert.ok(slam(0.001, 0, false).scale > 1.2, "starts big");
  assert.ok(Math.sign(slam(0.1, 0, false).rotate) !== Math.sign(slam(0.1, 1, false).rotate), "alternates sides");
});

test("a long hyphenated word may break after its hyphen, and a small leading word becomes a lead-in", () => {
  const words = [
    { text: "a", startMs: 0, endMs: 200 },
    { text: "copper-based", startMs: 200, endMs: 700 },
  ];
  const layout = layoutChunk(groupWords(words)[0], measure);
  assert.deepEqual(layout.lines.map((l) => l.items.map((p) => p.text).join("")), ["A", "COPPER-", "BASED"]);
  const main = Math.max(layout.lines[1].fontSize, layout.lines[2].fontSize);
  assert.ok(layout.lines[0].fontSize <= 0.5 * main + 1e-9, "A is a small lead-in (half the biggest main line)");
  assert.ok(layout.lines[1].items[0].word === 1 && layout.lines[2].items[0].word === 1, "both pieces belong to the same word");
});
