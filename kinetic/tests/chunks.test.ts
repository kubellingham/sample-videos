import assert from "node:assert/strict";
import { test } from "node:test";
import { displayText, groupWords, markEmphasis, type Word } from "../src/lib/chunks";

/** Words spoken back to back, 300 ms each; `gaps` adds silence (ms) before word i. */
const speak = (text: string, gaps: Record<number, number> = {}): Word[] => {
  let t = 0;
  return text.split(" ").map((w, i) => {
    t += gaps[i] ?? 0;
    const word = { text: w, startMs: t, endMs: t + 280 };
    t += 300;
    return word;
  });
};
const texts = (chunks: ReturnType<typeof groupWords>) => chunks.map((c) => c.words.map((w) => w.display).join(" "));

test("chunks hold 1-3 words and break after punctuation", () => {
  const c = groupWords(speak("Did you know an octopus has three hearts? Two pump blood, one does not."));
  assert.deepEqual(texts(c), ["DID YOU KNOW", "AN OCTOPUS HAS", "THREE HEARTS?", "TWO PUMP BLOOD", "ONE DOES NOT"]);
  assert.ok(c.every((x) => x.words.length >= 1 && x.words.length <= 3));
});

test("a pause starts a new chunk", () => {
  const c = groupWords(speak("one two three four", { 2: 400 }), { pauseMs: 260 });
  assert.deepEqual(texts(c), ["ONE TWO", "THREE FOUR"]);
});

test("a full chunk never ends on a lone function word", () => {
  assert.deepEqual(texts(groupWords(speak("pump it to the rest of the body"))), ["PUMP IT", "TO THE REST", "OF THE BODY"]);
});

test("emphasised phrases get their own chunk and are marked", () => {
  const words = speak("it has three hearts and blue blood");
  const c = groupWords(words, { emphasis: ["Three hearts", "BLUE"] });
  assert.deepEqual(texts(c), ["IT HAS", "THREE HEARTS", "AND", "BLUE", "BLOOD"]);
  assert.deepEqual(c.map((x) => x.emphasis), [false, true, false, true, false]);
  assert.deepEqual(markEmphasis(words, ["hearts"]), [false, false, false, true, false, false, false]);
});

test("letters per chunk are limited", () => {
  const c = groupWords(speak("extraordinary cephalopod intelligence"), { maxChars: 18 });
  assert.equal(c.length, 3);
});

test("timing: slam on the first word; stay until the next chunk or leave after a hold", () => {
  const c = groupWords(speak("one two. three four", { 2: 1500 }), { holdMs: 550 });
  assert.equal(c[0].startMs, 0);
  assert.equal(c[0].endMs, c[0].lastWordEndMs + 550); // a long pause follows: leave after the hold
  assert.equal(c[1].startMs, 2100);
  const d = groupWords(speak("one two. three four", { 2: 200 }));
  assert.equal(d[0].endMs, d[1].startMs); // the next chunk comes soon: cut straight to it
});

test("display text drops trailing commas and periods but keeps ? and !", () => {
  assert.equal(displayText("blood,"), "BLOOD");
  assert.equal(displayText("hearts?"), "HEARTS?");
  assert.equal(displayText("aren't."), "AREN'T");
  assert.equal(displayText('"wow!"'), "WOW!");
});
