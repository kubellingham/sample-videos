// Verify that no text ever enters the Shorts UI zones (bottom 20%, right 12%).
//
//   npx tsx scripts/check-safe-zones.ts <id>
//
// Renders the video in "mask" mode (white type, outline and hard shadow on
// black; no background) at quarter size, then scans every frame for lit
// pixels inside the zones, and reports how close the type ever came.
import path from "node:path";
import { spawnSync } from "node:child_process";
import { SAFE } from "../src/lib/motion";

const ROOT = path.resolve(__dirname, "..");

const main = () => {
  const id = process.argv[2];
  if (!id) throw new Error("usage: npx tsx scripts/check-safe-zones.ts <id>");
  const out = path.join(ROOT, ".cache", `${id}-mask.mp4`);
  const r = spawnSync(
    "npx",
    ["remotion", "render", "KineticShort", out, `--props=${JSON.stringify({ id, mask: true })}`, "--scale=0.25", "--muted", "--crf=1", "--concurrency=4", "--log=error"],
    { cwd: ROOT, stdio: "inherit" },
  );
  if (r.status !== 0) throw new Error("mask render failed");
  const W = 270;
  const H = 480;
  const raw = spawnSync("ffmpeg", ["-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"], { maxBuffer: 1 << 30 }).stdout;
  const frames = raw.length / (W * H);
  const xMax = Math.floor(SAFE.right * W); // first forbidden column
  const yMax = Math.floor(SAFE.bottom * H); // first forbidden row
  let violations = 0;
  let maxRight = 0;
  let maxBottom = 0;
  let minLeft = W;
  let minTop = H;
  const worst: { frame: number; px: number }[] = [];
  for (let f = 0; f < frames; f++) {
    let px = 0;
    const base = f * W * H;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (raw[base + y * W + x] < 100) continue;
        maxRight = Math.max(maxRight, x);
        maxBottom = Math.max(maxBottom, y);
        minLeft = Math.min(minLeft, x);
        minTop = Math.min(minTop, y);
        if (x >= xMax || y >= yMax) px++;
      }
    }
    if (px) {
      violations++;
      worst.push({ frame: f, px });
    }
  }
  const pct = (v: number, n: number) => ((100 * v) / n).toFixed(1);
  console.log(`${frames} frames checked. Text reached x = ${pct(maxRight + 1, W)}% (limit ${SAFE.right * 100}%), y = ${pct(maxBottom + 1, H)}% (limit ${SAFE.bottom * 100}%), left edge ${pct(minLeft, W)}%, top ${pct(minTop, H)}%.`);
  if (violations) {
    console.log(`FAIL: ${violations} frames put text in the Shorts UI zones, e.g. ${worst.slice(0, 5).map((w) => `frame ${w.frame} (${w.px} px)`).join(", ")}`);
    process.exit(1);
  }
  console.log("OK: no text in the bottom 20% or the right 12% in any frame.");
};

main();
