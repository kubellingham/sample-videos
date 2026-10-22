// Contact sheet of a rendered video: one tile per chunk, taken `--after`
// frames after it slams in, labelled with its time and words (a note marks
// a downbeat within 100 ms).
//
//   npm run sheet -- <id> [--after 9] [--cols 8] [--width 216]
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import type { Caption } from "@remotion/captions";
import { groupWords } from "../src/lib/chunks";
import { ConfigSchema } from "../src/lib/config";
import { FPS } from "../src/lib/motion";

const ROOT = path.resolve(__dirname, "..");

const findFont = () => {
  try {
    const f = execFileSync("fc-match", ["-f", "%{file}", "DejaVu Sans:bold"]).toString().trim();
    return existsSync(f) ? f : null;
  } catch {
    return null;
  }
};

const esc = (s: string) => s.replace(/\\/g, "\\\\").replace(/'/g, "\u2019").replace(/:/g, "\\:").replace(/%/g, "\\%");

const main = () => {
  const args = process.argv.slice(2);
  const id = args.find((a) => !a.startsWith("--"));
  if (!id) throw new Error("usage: npm run sheet -- <id>");
  const opt = (name: string, def: number) => (args.includes(`--${name}`) ? Number(args[args.indexOf(`--${name}`) + 1]) : def);
  const after = opt("after", 9);
  const cols = opt("cols", 8);
  const tw = opt("width", 216);
  const th = Math.round((tw * 16) / 9);
  const dir = path.join(ROOT, "public", "videos", id);
  const video = path.join(ROOT, "out", `${id}.mp4`);
  if (!existsSync(video)) throw new Error(`Render it first: ${path.relative(ROOT, video)} is missing`);
  const config = ConfigSchema.parse(JSON.parse(readFileSync(path.join(dir, "config.json"), "utf8")));
  const meta = JSON.parse(readFileSync(path.join(dir, "meta.json"), "utf8"));
  const transcript: Caption[] = JSON.parse(readFileSync(path.join(dir, "transcript.json"), "utf8"));
  const beats = existsSync(path.join(dir, "beats.json")) ? JSON.parse(readFileSync(path.join(dir, "beats.json"), "utf8")) : { downbeats: [], tempo: 0 };
  const chunks = groupWords(
    transcript.map((c) => ({ text: c.text.trim(), startMs: c.startMs, endMs: c.endMs })),
    { maxWords: config.maxWords, pauseMs: config.pauseMs, emphasis: [...config.emphasis, ...(meta.emphasis ?? [])] },
  );
  const font = findFont();
  const tmp = path.join(ROOT, ".cache", `sheet-${id}`);
  rmSync(tmp, { recursive: true, force: true });
  mkdirSync(tmp, { recursive: true });
  chunks.forEach((c, i) => {
    const frame = Math.round((c.startMs / 1000) * FPS) + after;
    const t = frame / FPS;
    const onBeat = beats.downbeats.some((d: number) => Math.abs(d - c.startMs / 1000) < 0.1);
    const label = `${(c.startMs / 1000).toFixed(2)}s ${onBeat ? "\u266a " : ""}${c.words.map((w) => w.display).join(" ")}`;
    const draw = font
      ? `,drawbox=x=0:y=ih-26:w=iw:h=26:color=black@0.75:t=fill,drawtext=fontfile='${font}':text='${esc(label)}':fontsize=13:fontcolor=white:x=6:y=h-19`
      : "";
    const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-ss", t.toFixed(4), "-i", video, "-frames:v", "1", "-vf", `scale=${tw}:${th}${draw}`, path.join(tmp, `t${String(i).padStart(3, "0")}.png`)]);
    if (r.status !== 0) throw new Error(r.stderr.toString());
  });
  const rows = Math.ceil(chunks.length / cols);
  const out = path.join(ROOT, "out", `${id}_contact.png`);
  const header = `${id}  \u00b7  ${chunks.length} chunks, each ${after} frames after it slams in  \u00b7  ${meta.duration.toFixed(1)} s  \u00b7  ${beats.tempo} BPM  \u00b7  \u266a = on a downbeat`;
  const head = font ? `,pad=iw:ih+40:0:40:color=0x0e0e12,drawtext=fontfile='${font}':text='${esc(header)}':fontsize=18:fontcolor=white:x=10:y=12` : "";
  const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-framerate", "1", "-i", path.join(tmp, "t%03d.png"), "-vf", `tile=${cols}x${rows}:padding=2:color=0x0e0e12${head}`, "-frames:v", "1", out]);
  if (r.status !== 0) throw new Error(r.stderr.toString());
  rmSync(tmp, { recursive: true, force: true });
  console.log(`contact sheet: ${path.relative(ROOT, out)} (${chunks.length} tiles)`);
};

main();
