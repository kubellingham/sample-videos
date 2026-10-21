"""Oddly-satisfying vertical video generator.

Pipeline: YAML config -> simulation (event timeline + per-frame scenes)
-> skia renderer -> numpy audio synth -> ffmpeg mux.
"""

__version__ = "0.1.0"

# Logical canvas. Simulations and the renderer always work in these units;
# lower-resolution previews are produced by scaling the canvas.
WIDTH = 1080
HEIGHT = 1920

# Shorts UI covers the bottom ~20% (title, channel) and the right ~12%
# (like/comment buttons). Text must stay out of both; the renderer enforces it.
SAFE_RIGHT = WIDTH * 0.88
SAFE_BOTTOM = HEIGHT * 0.80
