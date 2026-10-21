"""One video: config -> simulation -> frames + audio -> MP4."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import skia

from . import formats, palettes
from .audio import render_audio, write_wav
from .config import Quality, VideoConfig
from .encode import VideoWriter
from .render import Renderer


def simulate(cfg: VideoConfig, fps: int):
    palette = palettes.resolve(cfg.palette, cfg.seed)
    return formats.get(cfg.format).simulate(cfg, palette, fps), palette


def render_video(cfg: VideoConfig, quality: Quality, out_path: str | Path, poster: str | Path | None = None,
                 log=print) -> dict:
    """Render ``cfg`` to ``out_path``. Writes ``<out>.json`` (config, summary and
    the event timeline) next to it and returns the summary."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    sim, palette = simulate(cfg, quality.fps)
    tl = sim.timeline
    log(f"  simulated {cfg.slug}: {tl.ending} at {tl.end_t:.1f}s, {sim.n_frames} frames "
        f"({sim.length:.1f}s), {tl.counts().get('bounce', 0)} bounces")

    audio = render_audio(tl, cfg.sound_set, cfg.sound, cfg.seed, quality.fps, sim.n_frames)
    renderer = Renderer(sim, palette, cfg.hook_text, quality.width, quality.height)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        write_wav(wav, audio)
        writer = VideoWriter(out_path, quality, wav)
        step = max(1, sim.n_frames // 10)
        try:
            for n in range(sim.n_frames):
                writer.write(renderer.render(n))
                if quality.name == "final" and n and n % step == 0:
                    log(f"    {100 * n // sim.n_frames}%")
        finally:
            writer.close()

    if poster is not None:
        # a frame from the middle of the action, before the ending
        n = min(sim.n_frames - 1, int(tl.end_t * 0.7 * quality.fps))
        renderer.render(n)
        img = renderer.surface.makeImageSnapshot()
        Path(poster).parent.mkdir(parents=True, exist_ok=True)
        Path(poster).write_bytes(bytes(img.encodeToData(skia.kJPEG, 82)))

    summary = {
        "file": out_path.name,
        "seed": cfg.seed,
        "format": cfg.format,
        "quality": quality.name,
        "width": quality.width,
        "height": quality.height,
        "fps": quality.fps,
        "frames": sim.n_frames,
        "length": round(sim.length, 3),
        "ending": tl.ending,
        "end_t": round(tl.end_t, 3),
        "variant": tl.variant,
        "palette": palette.name,
        "counts": tl.counts(),
        "render_seconds": round(time.time() - t0, 1),
    }
    meta = {"config": cfg.to_dict(), "summary": summary, "timeline": tl.to_dict()}
    out_path.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    log(f"  wrote {out_path} in {summary['render_seconds']}s")
    return summary
