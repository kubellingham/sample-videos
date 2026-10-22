import { Audio } from "@remotion/media";
import { useMemo } from "react";
import { AbsoluteFill, Sequence, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Background } from "./Background";
import { ChunkView } from "./ChunkView";
import type { Props } from "./data";
import { ensureFont } from "./fonts";
import { beatKick, CENTER, EXIT_FRAMES, HEIGHT, SAFE, WIDTH } from "./lib/motion";

// every page that renders frames loads the fonts (loadFont holds the render until they are ready)
ensureFont("Anton");
ensureFont("Archivo Black");

/** Bold kinetic typography over a drifting gradient, punching on the beat. */
export const KineticShort: React.FC<Props> = ({ data, showSafeZones, mask }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const downbeats = useMemo(() => new Set(data?.downbeats ?? []), [data]);
  if (!data) return null;
  const t = frame / fps;
  const kick = beatKick(t, data.beats, downbeats);
  return (
    <AbsoluteFill style={{ backgroundColor: mask ? "#000" : data.palette.background[0] }}>
      {mask ? null : <Background palette={data.palette} glow={kick.glow} />}
      {/* the "camera" on the type: zoom punch around the text centre and a short shake on each beat */}
      <AbsoluteFill
        style={{
          transformOrigin: `${CENTER.x * WIDTH}px ${CENTER.y * HEIGHT}px`,
          scale: String(kick.zoom),
          translate: `${kick.x}px ${kick.y}px`,
        }}
      >
        {data.chunks.map((chunk, i) => {
          const from = Math.round((chunk.startMs / 1000) * fps);
          const next = data.chunks[i + 1];
          const to = Math.round((chunk.endMs / 1000) * fps);
          const cut = next !== undefined && Math.round((next.startMs / 1000) * fps) <= to;
          const duration = Math.max(1, to - from);
          return (
            <Sequence key={i} from={from} durationInFrames={duration} layout="none" name={chunk.words.map((w) => w.display).join(" ")}>
              <ChunkView chunk={chunk} palette={data.palette} font={data.font} mask={mask} exitFrom={cut ? null : Math.max(0, duration - EXIT_FRAMES)} />
            </Sequence>
          );
        })}
      </AbsoluteFill>
      {showSafeZones ? <SafeZones /> : null}
      {mask ? null : <Audio src={staticFile(data.audio)} />}
    </AbsoluteFill>
  );
};

/** The Shorts UI zones (bottom 20%, right 12%) and the text area, for checking layouts. */
const SafeZones: React.FC = () => (
  <AbsoluteFill style={{ pointerEvents: "none" }}>
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: (1 - SAFE.bottom) * HEIGHT, background: "rgba(255,0,60,0.28)" }} />
    <div style={{ position: "absolute", top: 0, bottom: 0, right: 0, width: (1 - SAFE.right) * WIDTH, background: "rgba(255,0,60,0.28)" }} />
    <div
      style={{
        position: "absolute",
        left: SAFE.left * WIDTH,
        top: SAFE.top * HEIGHT,
        width: (SAFE.right - SAFE.left) * WIDTH,
        height: (SAFE.bottom - SAFE.top) * HEIGHT,
        outline: "3px dashed rgba(255,255,255,0.6)",
      }}
    />
  </AbsoluteFill>
);
