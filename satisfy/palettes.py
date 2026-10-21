"""Named color palettes.

A config can name a palette, use ``random`` (picked from the seed), or give an
inline mapping with the same keys as :class:`Palette`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .scene import Color


def hex_color(value: str | Color) -> Color:
    if isinstance(value, (tuple, list)):
        return tuple(int(c) for c in value[:3])  # type: ignore[return-value]
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def mix(a: Color, b: Color, t: float) -> Color:
    t = min(1.0, max(0.0, t))
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


@dataclass
class Palette:
    name: str
    bg_top: Color
    bg_bottom: Color
    glow: Color  # soft spotlight behind the action
    ring: Color
    accents: list[Color] = field(default_factory=list)  # balls / notes
    text: Color = (255, 255, 255)
    text_stroke: Color = (0, 0, 0)

    def accent(self, i: int) -> Color:
        return self.accents[i % len(self.accents)]


def _p(name, bg_top, bg_bottom, glow, ring, accents, text="#ffffff", text_stroke="#000000"):
    return Palette(
        name=name,
        bg_top=hex_color(bg_top),
        bg_bottom=hex_color(bg_bottom),
        glow=hex_color(glow),
        ring=hex_color(ring),
        accents=[hex_color(a) for a in accents],
        text=hex_color(text),
        text_stroke=hex_color(text_stroke),
    )


PALETTES: dict[str, Palette] = {
    p.name: p
    for p in [
        _p("neon", "#0d0221", "#190634", "#3d1a78", "#f5f3ff",
           ["#ff2e88", "#2ef2ff", "#ffe62e", "#7cff4f", "#b14dff", "#ff8a2e"]),
        _p("sunset", "#1a0b2e", "#3b0f3a", "#7a2b5a", "#ffe8d6",
           ["#ff6b6b", "#ffa94d", "#ffd43b", "#ff8fab", "#c77dff", "#f783ac"]),
        _p("ocean", "#020c1b", "#06305a", "#0f5d8c", "#e0fbfc",
           ["#3ddbd9", "#80ffdb", "#ffffff", "#48bfe3", "#b8f2e6", "#ffd6a5"]),
        _p("forest", "#071a12", "#10302a", "#1f5a42", "#f1faee",
           ["#9be564", "#d7f75b", "#ffd166", "#5ad2f4", "#e6ffb1", "#ff9f68"]),
        _p("candy", "#2b1240", "#5a1e6b", "#a8458f", "#fff0f8",
           ["#ff5d8f", "#ffd166", "#6ce5e8", "#b8f35a", "#ffb3c6", "#9d8cff"]),
        _p("mono", "#050505", "#141414", "#2a2a2a", "#f5f5f5",
           ["#ffffff", "#d9d9d9", "#bfbfbf", "#f0f0f0"]),
        _p("aurora", "#020617", "#0b1d3a", "#14532d", "#ecfeff",
           ["#34d399", "#22d3ee", "#a78bfa", "#f0abfc", "#a3e635", "#67e8f9"]),
        _p("ember", "#120604", "#2a0c06", "#5c1a0b", "#fff4e6",
           ["#ff5400", "#ff8500", "#ffb700", "#ffd000", "#ff006e", "#fffcf2"]),
    ]
}


def resolve(spec, seed: int) -> Palette:
    """Turn a config palette value into a :class:`Palette`."""
    if isinstance(spec, dict):
        base = PALETTES[spec.get("base", "neon")]
        merged = {
            "name": spec.get("name", "custom"),
            "bg_top": hex_color(spec.get("bg_top", base.bg_top)),
            "bg_bottom": hex_color(spec.get("bg_bottom", base.bg_bottom)),
            "glow": hex_color(spec.get("glow", base.glow)),
            "ring": hex_color(spec.get("ring", base.ring)),
            "accents": [hex_color(a) for a in spec.get("accents", base.accents)],
            "text": hex_color(spec.get("text", base.text)),
            "text_stroke": hex_color(spec.get("text_stroke", base.text_stroke)),
        }
        return Palette(**merged)
    name = str(spec or "neon")
    if name == "random":
        name = random.Random(f"palette:{seed}").choice(sorted(PALETTES))
    if name not in PALETTES:
        raise ValueError(f"Unknown palette '{name}'. Known: {', '.join(sorted(PALETTES))}, random")
    return PALETTES[name]
