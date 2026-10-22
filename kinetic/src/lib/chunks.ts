// Words -> on-screen chunks of 1-3 words. Pure functions (used by the
// composition and the tests).

export type Word = { text: string; startMs: number; endMs: number };

export type ChunkWord = Word & {
  /** What is drawn: upper case, without trailing commas/periods (keeps ? and !). */
  display: string;
  emphasis: boolean;
};

export type Chunk = {
  index: number;
  words: ChunkWord[];
  startMs: number; // slam-in time: the first word's onset
  endMs: number; // leaves the screen (next chunk's start, or after a hold in a pause)
  lastWordEndMs: number;
  emphasis: boolean; // contains an emphasised word
};

export type ChunkOptions = {
  maxWords: number;
  pauseMs: number; // a silence at least this long starts a new chunk
  maxChars: number; // letters per chunk
  emphasis: string[]; // words or phrases
  holdMs: number; // how long a chunk stays after its last word when a pause follows
};

export const DEFAULT_CHUNK_OPTIONS: ChunkOptions = {
  maxWords: 3,
  pauseMs: 260,
  maxChars: 18,
  emphasis: [],
  holdMs: 550,
};

export const FUNCTION_WORDS = new Set(
  "a an the and or but of to in on at by for with from as is it its that this so than then if into".split(" "),
);

export const normalize = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}']+/gu, "").replace(/^'+|'+$/g, "");

const endsSentence = (w: string) => /[.,!?;:—–…]["')\]]*$/.test(w.trim());

export const displayText = (w: string) =>
  w
    .trim()
    .replace(/[.,;:…]+(["')\]]*)$/u, "$1")
    .replace(/^["'([]+|["')\]]+$/g, "")
    .toUpperCase();

/** Indices of words covered by the emphasis phrases (matched word by word, ignoring case and punctuation). */
export const markEmphasis = (words: Word[], phrases: string[]): boolean[] => {
  const marks = words.map(() => false);
  const norm = words.map((w) => normalize(w.text));
  for (const phrase of phrases) {
    const parts = phrase.split(/\s+/).map(normalize).filter(Boolean);
    if (!parts.length) continue;
    for (let i = 0; i + parts.length <= norm.length; i++) {
      if (parts.every((p, k) => norm[i + k] === p)) {
        for (let k = 0; k < parts.length; k++) marks[i + k] = true;
      }
    }
  }
  return marks;
};

/**
 * Group words into chunks of at most `maxWords` words. A new chunk starts
 * after punctuation, at a pause of `pauseMs` or more, when the chunk is
 * full (words or letters), and around emphasised phrases (they get a chunk
 * of their own when they fit). A soft break (a full chunk) never leaves a
 * lone function word ("the", "of", ...) at the end of a chunk: it moves to
 * the next one.
 */
export const groupWords = (words: Word[], opts: Partial<ChunkOptions> = {}): Chunk[] => {
  const o = { ...DEFAULT_CHUNK_OPTIONS, ...opts };
  const emph = markEmphasis(words, o.emphasis);
  const groups: number[][] = [];
  let cur: number[] = [];
  const letters = (idx: number[]) => idx.reduce((n, i) => n + normalize(words[i].text).length, 0);
  for (let i = 0; i < words.length; i++) {
    if (cur.length) {
      const prev = words[i - 1];
      const hard = endsSentence(prev.text) || words[i].startMs - prev.endMs >= o.pauseMs;
      const emphasisEdge = emph[i] !== emph[i - 1];
      const full = cur.length >= o.maxWords || letters([...cur, i]) > o.maxChars;
      if (hard || emphasisEdge || full) {
        if (!hard && !emphasisEdge && cur.length > 1 && FUNCTION_WORDS.has(normalize(prev.text))) {
          // soft break: carry a dangling "the"/"of" over to the next chunk
          const carried = cur.pop()!;
          groups.push(cur);
          cur = [carried];
        } else {
          groups.push(cur);
          cur = [];
        }
      }
    }
    cur.push(i);
  }
  if (cur.length) groups.push(cur);

  const chunks: Chunk[] = groups.map((g, index) => {
    const ws = g.map((i) => ({ ...words[i], display: displayText(words[i].text), emphasis: emph[i] }));
    return {
      index,
      words: ws,
      startMs: ws[0].startMs,
      endMs: 0,
      lastWordEndMs: ws[ws.length - 1].endMs,
      emphasis: ws.some((w) => w.emphasis),
    };
  });
  for (let k = 0; k < chunks.length; k++) {
    const c = chunks[k];
    const next = chunks[k + 1];
    const held = c.lastWordEndMs + o.holdMs;
    // stay until the next chunk slams in, or leave after a hold if a long pause follows
    c.endMs = next ? (next.startMs - held < 150 ? next.startMs : held) : held;
  }
  return chunks;
};
