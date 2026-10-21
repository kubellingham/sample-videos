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
from satisfy.pipeline import render_video, simulate

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


def _decode_audio(path):
    import subprocess

    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T


@needs_ffmpeg
def test_loop_audio_starts_clean_and_in_sync(tmp_path):
    """A loop's first frame has a chord on it. The MP4's audio must start with
    that chord intact and on time (the encoder gets a pre-roll from the end of
    the cycle, trimmed by the edit list), and last exactly one cycle."""
    import numpy as np

    from satisfy.audio import SR, render_audio

    cfg = VideoConfig(format="polyrhythm_loop", seed=1, duration=4.0, params={"rings": 4, "base": 2})
    out = tmp_path / "loop.mp4"
    render_video(cfg, PREVIEW, out, log=lambda *_: None)
    sim, _ = simulate(cfg, PREVIEW.fps)
    src = render_audio(sim.timeline, cfg.sound_set, cfg.sound, cfg.seed, PREVIEW.fps, sim.n_frames)
    dec = _decode_audio(out)
    n = src.shape[1]
    assert 0 <= dec.shape[1] - n < 1024  # one cycle (plus the last AAC frame's padding), no pre-roll left in
    head = slice(0, SR // 25)  # the first 40 ms: the t = 0 chord's attack
    scale = float(np.sum(src[:, head] * dec[:, head]) / np.sum(dec[:, head] ** 2))
    assert 0.8 < scale < 1.25
    # without the pre-roll the encoder smears this attack (~45% of peak off); with it, ~7.5% at 96k
    assert np.abs(src[:, head] - scale * dec[:, head]).max() < 0.15 * np.abs(src).max()
