import { useCurrentFrame, useVideoConfig } from "remotion";
import type { Chunk } from "./lib/chunks";
import type { Palette } from "./lib/config";
import { LAYOUT, pieceScale, type ChunkLayout } from "./lib/layout";
import { CENTER, EXIT_FRAMES, HEIGHT, slam, WIDTH } from "./lib/motion";

type Props = {
  chunk: Chunk & { layout: ChunkLayout };
  palette: Palette;
  font: string;
  /** Frame (relative to the chunk's start) where it starts leaving, or null if the next chunk cuts it. */
  exitFrom: number | null;
  mask: boolean;
};

/**
 * One chunk: slams in (spring scale down from big, slight rotation that
 * settles), words not yet spoken wait at low opacity and pop when spoken,
 * emphasised words take the accent colour and a bigger size.
 */
export const ChunkView: React.FC<Props> = ({ chunk, palette, font, exitFrom, mask }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const s = slam(t, chunk.index, chunk.emphasis);
  const exit = exitFrom === null ? 0 : Math.min(1, Math.max(0, (frame - exitFrom) / EXIT_FRAMES));
  const text = mask ? "#ffffff" : palette.text;
  const accent = mask ? "#ffffff" : palette.accent;
  const stroke = mask ? "#ffffff" : palette.stroke;
  const { lines } = chunk.layout;
  return (
    <div
      style={{
        position: "absolute",
        left: CENTER.x * WIDTH,
        top: CENTER.y * HEIGHT,
        translate: "-50% -50%",
        scale: String(s.scale * (1 - 0.12 * exit)),
        rotate: `${s.rotate}deg`,
        opacity: s.opacity * (1 - exit),
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        fontFamily: font,
        whiteSpace: "pre",
        padding: `${LAYOUT.pad * Math.max(...lines.map((l) => l.fontSize))}px`,
      }}
    >
      {lines.map((line, li) => {
        return (
          <div key={li} style={{ fontSize: line.fontSize, lineHeight: LAYOUT.lineHeight, display: "flex", alignItems: "baseline" }}>
            {line.items.map((piece, k) => {
              const wi = piece.word;
              const word = chunk.words[wi];
              const age = (word.startMs - chunk.startMs) / 1000;
              const spoken = t >= age;
              // a word spoken after the slam pops a little (not during the slam: it must stay in the safe area)
              const pop = spoken && age > 0 ? Math.exp(-(t - age) / 0.06) * Math.min(1, Math.max(0, (t - 0.12) / 0.1)) : 0;
              const size = pieceScale(chunk, line.items, piece);
              const color = word.emphasis ? accent : text;
              return [
                piece.space ? <span key={`s${k}`}> </span> : null,
                <span
                  key={k}
                  style={{
                    fontSize: `${size}em`,
                    color,
                    opacity: spoken || mask ? 1 : 0.3,
                    scale: String(1 + 0.1 * pop),
                    display: "inline-block",
                    WebkitTextStroke: `${0.045 * size}em ${stroke}`,
                    paintOrder: "stroke fill",
                    textShadow: mask
                      ? `0 0.06em 0 ${stroke}`
                      : `0 0.06em 0 ${stroke}${word.emphasis ? `, 0 0 0.35em ${accent}88` : ""}, 0 0.12em 0.3em rgba(0,0,0,0.35)`,
                  }}
                >
                  {piece.text}
                </span>,
              ];
            })}
          </div>
        );
      })}
    </div>
  );
};
