"""ffmpeg: raw RGBA frames on stdin + a WAV file -> H.264/AAC MP4."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import Quality


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH. Install it (e.g. `apt install ffmpeg` or `brew install ffmpeg`).")
    return exe


class VideoWriter:
    """Streams frames into ffmpeg and muxes the audio track in the same pass."""

    def __init__(self, path: str | Path, quality: Quality, audio_path: str | Path, audio_preroll: float = 0.0):
        """``audio_preroll``: seconds at the start of the WAV that play before
        the video starts. They are encoded and then trimmed by the MP4's edit
        list, so the AAC encoder never starts cold on a loud first sample
        (it smears a transient at sample 0); used for loops whose first frame
        has a chord on it."""
        self.path = Path(path)
        q = quality
        offset = ["-itsoffset", f"{-audio_preroll:.6f}"] if audio_preroll > 0 else []
        cmd = [
            ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{q.width}x{q.height}", "-r", str(q.fps), "-i", "-",
            *offset, "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            # RGB -> BT.709 limited-range YUV, tagged so players and YouTube agree on colors
            "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int",
            "-c:v", "libx264", "-preset", q.preset, "-crf", str(q.crf), "-tune", "animation",
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", str(q.fps * 2), "-bf", "2",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", q.audio_bitrate, "-ar", "48000",
            "-movflags", "+faststart",
            str(self.path),
        ]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, rgba) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(memoryview(rgba).cast("B"))
        except BrokenPipeError:
            self.close()
            raise

    def close(self) -> None:
        if self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        err = self.proc.stderr.read().decode(errors="replace") if self.proc.stderr else ""
        code = self.proc.wait()
        if code != 0:
            raise RuntimeError(f"ffmpeg failed ({code}): {err.strip()}")


def probe(path: str | Path) -> dict:
    """Basic stream info via ffprobe (used by tests and the CLI summary)."""
    import json

    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    out = subprocess.run(
        [exe, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,r_frame_rate,nb_frames,sample_rate,channels,duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)
