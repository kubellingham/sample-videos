import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import type { Palette } from "./lib/config";

const wave = (t: number, period: number, phase = 0) => Math.sin((2 * Math.PI * t) / period + phase);

/**
 * Slowly drifting gradient: a base linear gradient whose angle sways, plus
 * three soft colour blobs on slow Lissajous paths. `glow` (0..1, from the
 * beats) briefly lifts the brightness.
 */
export const Background: React.FC<{ palette: Palette; glow: number }> = ({ palette, glow }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const [c0, c1, c2 = c0, c3 = c1] = palette.background;
  const blobs = [
    { color: c2, x: 30 + 22 * wave(t, 17), y: 28 + 16 * wave(t, 13, 1), r: 60 },
    { color: c3, x: 72 + 20 * wave(t, 19, 2), y: 70 + 18 * wave(t, 11, 0.5), r: 55 },
    { color: c1, x: 55 + 25 * wave(t, 23, 4), y: 45 + 25 * wave(t, 29, 3), r: 45 },
  ];
  return (
    <AbsoluteFill
      style={{
        background: [
          ...blobs.map((b) => `radial-gradient(circle at ${b.x}% ${b.y}%, ${b.color}cc 0%, ${b.color}00 ${b.r}%)`),
          `linear-gradient(${160 + 30 * wave(t, 21)}deg, ${c0} 0%, ${c1} 100%)`,
        ].join(", "),
        filter: `brightness(${1 + 0.1 * glow}) saturate(${1.05 + 0.1 * glow})`,
      }}
    >
      {/* soft vignette keeps the eye on the type */}
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at 46% 42%, rgba(0,0,0,0) 45%, rgba(0,0,0,0.35) 100%)" }} />
    </AbsoluteFill>
  );
};
