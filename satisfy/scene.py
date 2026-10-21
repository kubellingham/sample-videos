"""Drawable primitives.

A simulation describes each frame as a :class:`Scene`: a flat list of these
primitives plus a few full-frame effects. The renderer knows how to draw every
primitive, so a new format never has to touch rendering code.

Colors are ``(r, g, b)`` tuples in 0..255. All coordinates are in the logical
1080x1920 canvas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Color = tuple[int, int, int]


@dataclass(slots=True)
class Circle:
    x: float
    y: float
    r: float
    color: Color
    alpha: float = 1.0
    glow: float = 0.0  # 0..1 soft halo strength
    shade: bool = True  # subtle radial highlight
    outline: Color | None = None
    outline_width: float = 0.0
    id: int | None = None  # stable id across frames (enables trails)
    trail: float = 0.0  # trail length in seconds (needs id)


@dataclass(slots=True)
class Arc:
    """Thick circular arc (a ring when ``sweep`` is 360). Angles in degrees,
    clockwise from +x (screen coordinates)."""

    cx: float
    cy: float
    r: float  # centerline radius
    width: float
    start: float
    sweep: float
    color: Color
    alpha: float = 1.0
    glow: float = 0.0
    round_caps: bool = True
    # Optional rigid transform, used for flying shards: rotate by ``rot``
    # degrees around (px, py), then translate by (dx, dy).
    rot: float = 0.0
    px: float = 0.0
    py: float = 0.0
    dx: float = 0.0
    dy: float = 0.0


@dataclass(slots=True)
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    color: Color
    alpha: float = 1.0
    glow: float = 0.0
    round_caps: bool = True


@dataclass(slots=True)
class Ripple:
    """Expanding outline circle."""

    x: float
    y: float
    r: float
    width: float
    color: Color
    alpha: float = 1.0


@dataclass(slots=True)
class Particle:
    x: float
    y: float
    r: float
    color: Color
    alpha: float = 1.0
    glow: bool = False


@dataclass(slots=True)
class Label:
    text: str
    x: float
    y: float
    size: float
    color: Color = (255, 255, 255)
    alpha: float = 1.0
    weight: str = "bold"  # regular | bold | black
    stroke: Color | None = None
    stroke_width: float = 0.0


@dataclass(slots=True)
class Effects:
    flash: float = 0.0  # 0..1 white flash overlay
    flash_color: Color = (255, 255, 255)
    desaturate: float = 0.0  # 0..1
    tint: Color | None = None
    tint_amount: float = 0.0
    zoom: float = 1.0
    zoom_x: float = 540.0
    zoom_y: float = 960.0
    shake_x: float = 0.0
    shake_y: float = 0.0
    vignette: float = 0.0  # extra vignette on top of the background one


@dataclass(slots=True)
class Scene:
    items: list = field(default_factory=list)
    fx: Effects = field(default_factory=Effects)
    # Optional overlay drawn above the effects layer (e.g. counters).
    overlay: list = field(default_factory=list)
