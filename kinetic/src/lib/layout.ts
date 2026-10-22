// Fit each chunk's words into huge stacked lines that stay inside the safe
// area even at the peak of the slam, the beat punch and the shake.
import { FUNCTION_WORDS, normalize, type Chunk } from "./chunks";
import { CENTER, HEIGHT, motionEnvelope, SAFE, WIDTH } from "./motion";

export type Measure = (text: string, fontSize: number) => number; // width in px

/** A piece of a word on a line: hyphenated words may break after a hyphen ("COPPER-" / "BASED"). */
export type Piece = { word: number; text: string; space: boolean }; // space: a space comes before it on its line
export type Line = { items: Piece[]; fontSize: number; width: number };
export type ChunkLayout = { lines: Line[]; width: number; height: number };

export const LAYOUT = {
  maxFont: 360,
  maxFontEmphasis: 400,
  emphasisScale: 1.14, // an emphasised word next to plain ones is this much bigger
  lineHeight: 0.98, // em
  pad: 0.1, // em: outline and hard shadow around the glyphs
  maxContrast: 1.7, // the biggest main line is at most this many times the smallest
  leadIn: 0.5, // a line of small words ("a", "to", "it") is set at this fraction of the main lines
};

/** Half the room around the block's centre, in px. */
export const room = () => ({
  hx: Math.min(CENTER.x - SAFE.left, SAFE.right - CENTER.x) * WIDTH,
  hy: Math.min(CENTER.y - SAFE.top, SAFE.bottom - CENTER.y) * HEIGHT,
});

/** The chunk's words cut into pieces after each hyphen. */
const piecesOf = (chunk: Chunk) =>
  chunk.words.flatMap((w, word) => w.display.split(/(?<=-)(?=.)/).map((text, k) => ({ word, text, first: k === 0 })));

/** All ways to split `n` items into consecutive lines. */
const partitions = (n: number): number[][][] => {
  if (n === 0) return [[]];
  const out: number[][][] = [];
  for (let k = 1; k <= n; k++) {
    for (const rest of partitions(n - k)) {
      out.push([Array.from({ length: k }, (_, i) => i), ...rest.map((l) => l.map((i) => i + k))]);
    }
  }
  return out;
};

/** Size of a w x h box rotated by `deg`. */
export const rotatedBox = (w: number, h: number, deg: number) => {
  const r = (Math.abs(deg) * Math.PI) / 180;
  return { w: w * Math.cos(r) + h * Math.sin(r), h: w * Math.sin(r) + h * Math.cos(r) };
};

/** Size multiplier of a piece: emphasised words are bigger next to plain ones. */
export const pieceScale = (chunk: Chunk, items: Piece[], p: Piece) =>
  chunk.words[p.word].emphasis && items.some((q) => !chunk.words[q.word].emphasis) ? LAYOUT.emphasisScale : 1;

const lineWidth = (chunk: Chunk, items: Piece[], fontSize: number, measure: Measure) =>
  items.reduce((w, p) => w + measure(p.text, fontSize * pieceScale(chunk, items, p)) + (p.space ? measure(" ", fontSize) : 0), 0);

/**
 * Choose the split into lines and the font size of each line. Every line is
 * fitted to the usable width on its own (short words get bigger), the sizes
 * are kept within `maxContrast` of each other, and the whole block is then
 * scaled down until, at the motion's largest scale and rotation, it still
 * fits the safe area. A line holding only small words ("a", "an", "to",
 * "it") before the last line is a lead-in: set at half the size of the main
 * lines, so the word it leads into can be huge. Among the splits, the one whose smallest main
 * line is biggest wins (with a little bonus for fewer lines).
 */
export const layoutChunk = (chunk: Chunk, measure: Measure): ChunkLayout => {
  const env = motionEnvelope(chunk.emphasis);
  const { hx, hy } = room();
  const usableW = (2 * (hx - env.shakePx)) / env.scale;
  const usableH = (2 * (hy - env.shakePx)) / env.scale;
  const pieces = piecesOf(chunk);
  let best: { score: number; layout: ChunkLayout } | null = null;
  for (const split of partitions(pieces.length)) {
    if (split.length > 3) continue;
    let lines: Line[] = split.map((idx) => {
      const items = idx.map((i, k) => ({ word: pieces[i].word, text: pieces[i].text, space: k > 0 && pieces[i].first }));
      const emph = items.every((p) => chunk.words[p.word].emphasis);
      const unit = lineWidth(chunk, items, 100, measure) / 100 + LAYOUT.pad * 2;
      const fontSize = Math.min(emph ? LAYOUT.maxFontEmphasis : LAYOUT.maxFont, usableW / unit);
      return { items, fontSize, width: 0 };
    });
    // lead-ins lead: a small-word line before the last line (a trailing "it" would just look lost)
    const minor = lines.map(
      (l, li) => li < lines.length - 1 && l.items.every((p) => !chunk.words[p.word].emphasis && FUNCTION_WORDS.has(normalize(chunk.words[p.word].text))),
    );
    const main = lines.filter((_, i) => !minor[i] || minor.every(Boolean));
    const smallest = Math.min(...main.map((l) => l.fontSize));
    const biggest = Math.max(...main.map((l) => Math.min(l.fontSize, smallest * LAYOUT.maxContrast)));
    lines = lines.map((l) => ({
      ...l,
      fontSize: main.includes(l) ? Math.min(l.fontSize, smallest * LAYOUT.maxContrast) : Math.min(l.fontSize, biggest * LAYOUT.leadIn),
    }));
    const measureBlock = (ls: Line[]) => {
      const sized = ls.map((l) => ({ ...l, width: lineWidth(chunk, l.items, l.fontSize, measure) + 2 * LAYOUT.pad * l.fontSize }));
      const top = Math.max(...sized.map((l) => l.fontSize));
      return {
        lines: sized,
        width: Math.max(...sized.map((l) => l.width)),
        height: sized.reduce((h, l) => h + l.fontSize * LAYOUT.lineHeight, 0) + 2 * LAYOUT.pad * top,
      };
    };
    let block = measureBlock(lines);
    // at the peak of the slam the block is scaled by env.scale and rotated by env.rotateDeg
    const rot = rotatedBox(block.width, block.height, env.rotateDeg);
    const k = Math.min(1, usableW / rot.w, usableH / rot.h);
    if (k < 1) block = measureBlock(lines.map((l) => ({ ...l, fontSize: l.fontSize * k * 0.999 })));
    const minSize = Math.min(...block.lines.filter((_, i) => main.includes(lines[i])).map((l) => l.fontSize));
    const score = minSize * (1 - 0.06 * (block.lines.length - 1));
    if (!best || score > best.score) best = { score, layout: block };
  }
  return best!.layout;
};

/**
 * The block's bounding box at its largest (slam peak, beat punch, shake),
 * in px, around CENTER. Used to check the safe area.
 */
export const worstCaseBounds = (layout: ChunkLayout, emphasis: boolean) => {
  const env = motionEnvelope(emphasis);
  const rot = rotatedBox(layout.width, layout.height, env.rotateDeg);
  const cx = CENTER.x * WIDTH;
  const cy = CENTER.y * HEIGHT;
  const hw = (rot.w * env.scale) / 2 + env.shakePx;
  const hh = (rot.h * env.scale) / 2 + env.shakePx;
  return { left: cx - hw, right: cx + hw, top: cy - hh, bottom: cy + hh };
};
