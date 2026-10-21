"""Command line: render | batch | serve | final | formats | sounds."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from .config import FINAL, PREVIEW


def _cmd_render(a) -> None:
    from .pipeline import render_video

    cfg = config_mod.load(a.config)
    if a.seed is not None:
        cfg = cfg.with_seed(a.seed)
    if a.duration is not None:
        cfg.duration = a.duration
    q = PREVIEW if a.preview else FINAL
    out = Path(a.out) if a.out else Path("out") / ("preview" if a.preview else "final") / f"{cfg.slug}.mp4"
    print(f"Rendering {cfg.slug} ({q.width}x{q.height} @ {q.fps}fps)")
    render_video(cfg, q, out, poster=out.with_suffix(".jpg") if a.poster else None)


def _seed_list(a) -> list[int]:
    if a.seeds:
        return sorted(set(a.seeds))
    return list(range(a.start_seed, a.start_seed + a.count))


def _cmd_batch(a) -> None:
    from .batch import run_batch

    cfg = config_mod.load(a.config)
    out = Path(a.out) if a.out else Path("out") / "batch" / (cfg.name or cfg.format)
    run_batch(cfg, _seed_list(a), out, jobs=a.jobs)


def _cmd_serve(a) -> None:
    from .gallery import serve

    serve(Path(a.batch_dir), port=a.port, open_browser=not a.no_open)


def _cmd_final(a) -> None:
    from .batch import run_final

    out = Path(a.out) if a.out else Path("out") / "final"
    run_final(Path(a.batch_dir), a.seeds, out, jobs=a.jobs)


def _cmd_formats(a) -> None:
    from . import formats

    for name, cls in sorted(formats.available().items()):
        print(f"{name}: {cls.description}")
        for k, v in cls.all_defaults().items():
            print(f"    {k}: {v!r}")


def _cmd_sounds(a) -> None:
    from .audio.melodies import MELODIES
    from .audio.sound_sets import SOUND_SETS

    for name, cls in SOUND_SETS.items():
        opts = {"min_gap": 0.055, "volume": 1.0, **cls.options}
        print(f"{name}: {cls.description}")
        for k, v in opts.items():
            print(f"    {k}: {v!r}")
    print("melodies: " + ", ".join(sorted(MELODIES)))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="satisfy", description="Oddly satisfying Shorts generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render one config (full quality by default)")
    r.add_argument("config")
    r.add_argument("--seed", type=int)
    r.add_argument("--duration", type=float, help="override the config duration (seconds)")
    r.add_argument("--preview", action="store_true", help="low-res 15fps preview instead of the final")
    r.add_argument("-o", "--out", help="output .mp4 (default: out/final/<name>_seed<N>.mp4)")
    r.add_argument("--poster", action="store_true", help="also write a .jpg poster frame")
    r.set_defaults(func=_cmd_render)

    b = sub.add_parser("batch", help="render many seeds as previews + an HTML gallery")
    b.add_argument("config")
    b.add_argument("-n", "--count", type=int, default=12)
    b.add_argument("--start-seed", type=int, default=1)
    b.add_argument("--seeds", type=int, nargs="+", help="explicit seeds (overrides -n/--start-seed)")
    b.add_argument("--out", help="batch folder (default: out/batch/<config name>); re-running adds seeds")
    b.add_argument("-j", "--jobs", type=int, default=0, help="parallel renders (default: up to 4)")
    b.set_defaults(func=_cmd_batch)

    s = sub.add_parser("serve", help="serve a batch gallery so keeper marks save to keepers.json")
    s.add_argument("batch_dir")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true", help="don't open a browser")
    s.set_defaults(func=_cmd_serve)

    f = sub.add_parser("final", help="re-render a batch's keepers at full quality")
    f.add_argument("batch_dir")
    f.add_argument("--seeds", type=int, nargs="+", help="seeds to render (default: keepers.json)")
    f.add_argument("--out", help="output folder (default: out/final)")
    f.add_argument("-j", "--jobs", type=int, default=1, help="parallel renders (each one is multi-threaded)")
    f.set_defaults(func=_cmd_final)

    sub.add_parser("formats", help="list formats and their params").set_defaults(func=_cmd_formats)
    sub.add_parser("sounds", help="list sound sets, options and melodies").set_defaults(func=_cmd_sounds)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
