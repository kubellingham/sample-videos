"""Oddly-satisfying vertical video generator.

Pipeline: YAML config -> simulation (event timeline + per-frame scenes)
-> skia renderer -> numpy audio synth -> ffmpeg mux.
"""

__version__ = "0.1.0"

# Logical canvas. Simulations and the renderer always work in these units;
# lower-resolution previews are produced by scaling the canvas.
WIDTH = 1080
HEIGHT = 1920
