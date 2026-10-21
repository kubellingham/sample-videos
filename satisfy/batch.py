"""batch: many seeds as quick previews + gallery. final: keepers at full quality."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from .config import FINAL, PREVIEW, Quality, VideoConfig
from .gallery import MANIFEST_FILE, read_keepers, write_gallery
from .pipeline import render_video


def _render_one(cfg_dict: dict, seed: int, quality_name: str, out: str, poster: str | None) -> dict:
    cfg = config_mod.from_dict(cfg_dict).with_seed(seed)
    return render_video(cfg, config_mod.QUALITIES[quality_name], out, poster=poster, log=lambda *_: None)


def _run_many(jobs_list: list[tuple], jobs: int, label: str) -> list[dict]:
    results = []
    t0 = time.time()
    if jobs <= 1:
        for args in jobs_list:
            res = _render_one(*args)
            results.append(res)
            print(f"  [{len(results)}/{len(jobs_list)}] seed {res['seed']}: {res['ending']} at {res['end_t']:.1f}s "
                  f"({res['length']:.1f}s video)")
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_render_one, *args): args[1] for args in jobs_list}
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                print(f"  [{len(results)}/{len(jobs_list)}] seed {res['seed']}: {res['ending']} at "
                      f"{res['end_t']:.1f}s ({res['length']:.1f}s video)")
    print(f"{label}: {len(results)} video(s) in {time.time() - t0:.0f}s")
    return sorted(results, key=lambda r: r["seed"])


def run_batch(cfg: VideoConfig, seeds: list[int], out_dir: Path, jobs: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_FILE
    manifest = {"entries": []}
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old.get("config", {}) | {"seed": 0} == cfg.to_dict() | {"seed": 0}:
            manifest = old  # same config: add to the existing batch
        else:
            raise SystemExit(f"{out_dir} holds a batch of a different config; pick another --out")

    jobs = jobs or min(4, os.cpu_count() or 1)
    print(f"Batch: {len(seeds)} preview(s) of {cfg.name or cfg.format} -> {out_dir} ({jobs} job(s))")
    work = [(cfg.to_dict(), s, PREVIEW.name, str(out_dir / "previews" / f"seed_{s}.mp4"),
             str(out_dir / "posters" / f"seed_{s}.jpg")) for s in seeds]
    results = _run_many(work, jobs, "Batch done")

    entries = {e["seed"]: e for e in manifest["entries"]}
    for r in results:
        entries[r["seed"]] = {
            "seed": r["seed"], "preview": f"previews/seed_{r['seed']}.mp4", "poster": f"posters/seed_{r['seed']}.jpg",
            "ending": r["ending"], "end_t": r["end_t"], "length": r["length"], "counts": r["counts"],
            "palette": r["palette"], "variant": r["variant"],
        }
    manifest.update({
        "id": manifest.get("id") or f"{cfg.name or cfg.format}-{int(time.time())}",
        "name": cfg.name or cfg.format,
        "dir": _display_path(out_dir),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "config": cfg.to_dict(),
        "entries": [entries[s] for s in sorted(entries)],
    })
    manifest_path.write_text(json.dumps(manifest, indent=1))
    gallery = write_gallery(out_dir, manifest)
    print(f"Gallery: {gallery}\n  open it in a browser, or run: python -m satisfy serve {_display_path(out_dir)}")
    return gallery


def run_final(batch_dir: Path, seeds: list[int] | None, out_dir: Path, jobs: int = 1,
              quality: Quality = FINAL) -> list[dict]:
    manifest_path = batch_dir / MANIFEST_FILE
    if not manifest_path.exists():
        raise SystemExit(f"No {MANIFEST_FILE} in {batch_dir}. Is this a batch folder?")
    manifest = json.loads(manifest_path.read_text())
    if not seeds:
        seeds = read_keepers(batch_dir)
    if not seeds:
        raise SystemExit("No keepers yet: mark some in the gallery (served), or pass --seeds.")
    cfg = config_mod.from_dict(manifest["config"])
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Final: {len(seeds)} keeper(s) at {quality.width}x{quality.height} {quality.fps}fps -> {out_dir}")
    work = [(cfg.to_dict(), s, quality.name, str(out_dir / f"{cfg.with_seed(s).slug}.mp4"), None) for s in seeds]
    return _run_many(work, jobs, "Final done")


def _display_path(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p.resolve())
