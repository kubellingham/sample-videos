"""End-to-end checks (need ffmpeg)."""

import json
import shutil
from pathlib import Path

import pytest

from satisfy import config as config_mod
from satisfy import formats
from satisfy.batch import run_batch, run_final
from satisfy.config import PREVIEW, VideoConfig
from satisfy.encode import probe
from satisfy.palettes import resolve
from satisfy.pipeline import render_video

ROOT = Path(__file__).resolve().parents[1]
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _short(seed=5):
    return VideoConfig(format="shrinking_ring", seed=seed, duration=6.0, hook_text="Will it escape?",
                       params={"tries": 1}, name="t")


@pytest.mark.parametrize("path", sorted((ROOT / "configs").glob("*.yaml")), ids=lambda p: p.name)
def test_example_configs_are_valid(path):
    cfg = config_mod.load(path)
    formats.get(cfg.format)(cfg, resolve(cfg.palette, cfg.seed))  # validates params


def test_unknown_config_key_is_rejected():
    with pytest.raises(ValueError, match="Unknown config keys"):
        config_mod.from_dict({"format": "shrinking_ring", "colour": "red"})


@needs_ffmpeg
def test_render_preview_end_to_end(tmp_path):
    out = tmp_path / "v.mp4"
    summary = render_video(_short(), PREVIEW, out, poster=tmp_path / "v.jpg", log=lambda *_: None)
    streams = {s["codec_type"]: s for s in probe(out)["streams"]}
    v, a = streams["video"], streams["audio"]
    assert (v["codec_name"], v["width"], v["height"], v["r_frame_rate"]) == ("h264", 360, 640, "15/1")
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", "48000", 2)
    assert int(v["nb_frames"]) == summary["frames"]
    assert abs(float(v["duration"]) - float(a["duration"])) < 0.03
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["summary"]["ending"] in ("escape", "trapped", "timeout")
    assert (tmp_path / "v.jpg").stat().st_size > 1000


@needs_ffmpeg
def test_batch_gallery_and_final(tmp_path):
    batch = tmp_path / "batch"
    run_batch(_short(), [3, 4], batch, jobs=1)
    manifest = json.loads((batch / "manifest.json").read_text())
    assert [e["seed"] for e in manifest["entries"]] == [3, 4]
    html = (batch / "gallery.html").read_text()
    assert "previews/seed_3.mp4" in html and "keepers" in html
    (batch / "keepers.json").write_text(json.dumps({"seeds": [4]}))
    results = run_final(batch, None, tmp_path / "final", quality=PREVIEW)
    assert [r["seed"] for r in results] == [4]
    assert (tmp_path / "final" / "t_seed4.mp4").exists()
