"""Skia renderer: turns a simulation's scenes into RGBA frames.

The renderer is format-agnostic. It draws the primitives in
:mod:`satisfy.scene`, applies full-frame effects, and overlays the hook text.
Everything is drawn in the logical 1080x1920 canvas and scaled to the output
resolution, so previews and finals frame identically.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import skia

from . import HEIGHT, WIDTH
from .formats.base import SimResult
from .palettes import Palette, mix
from .scene import Arc, Circle, Effects, Label, Particle, Ripple, Scene

FONT_CANDIDATES = ["Inter", "Montserrat", "Poppins", "Roboto", "DejaVu Sans", "Liberation Sans", "Arial"]
WEIGHTS = {"regular": 400, "bold": 700, "black": 900}


@lru_cache(maxsize=None)
def typeface(weight: str = "bold") -> skia.Typeface:
    style = skia.FontStyle(WEIGHTS.get(weight, 700), skia.FontStyle.kNormal_Width, skia.FontStyle.kUpright_Slant)
    for family in FONT_CANDIDATES:
        tf = skia.Typeface.MakeFromName(family, style)
        if tf is not None and tf.getFamilyName().lower() == family.lower():
            return tf
    return skia.Typeface.MakeFromName(None, style) or skia.Typeface.MakeDefault()


def _c(rgb, alpha: float = 1.0) -> int:
    a = int(round(255 * min(1.0, max(0.0, alpha))))
    return skia.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]), a)


def wrap_text(text: str, font: skia.Font, max_width: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        line = ""
        for word in words:
            trial = f"{line} {word}".strip()
            if line and font.measureText(trial) > max_width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return [ln for ln in lines if ln] or [""]


class Renderer:
    def __init__(self, sim: SimResult, palette: Palette, hook_text: str, width: int, height: int):
        self.sim = sim
        self.palette = palette
        self.width, self.height = width, height
        self.scale = width / WIDTH
        if abs(height / HEIGHT - self.scale) > 1e-6:
            raise ValueError("Output must keep the 9:16 aspect ratio")
        self.buffer = np.zeros((height, width, 4), dtype=np.uint8)
        self.surface = skia.Surface(self.buffer, colorType=skia.kRGBA_8888_ColorType,
                                    alphaType=skia.kPremul_AlphaType)
        self.background = self._make_background()
        self.background_px = self._dither(self.background.toarray(colorType=skia.kRGBA_8888_ColorType,
                                                                  alphaType=skia.kPremul_AlphaType))
        self.background = skia.Image.fromarray(self.background_px, colorType=skia.kRGBA_8888_ColorType)
        self.hook = self._make_hook(hook_text) if hook_text.strip() else None
        self._blur_cache: dict[float, skia.MaskFilter] = {}

    # ------------------------------------------------------------ static art
    def _surface(self, width: int, height: int) -> skia.Surface:
        # Same pixel format as the output buffer, so blits need no conversion.
        info = skia.ImageInfo.Make(width, height, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
        return skia.Surface.MakeRaster(info)

    def _make_background(self) -> skia.Image:
        pal = self.palette
        surf = self._surface(self.width, self.height)
        c = surf.getCanvas()
        c.scale(self.scale, self.scale)
        paint = skia.Paint(Shader=skia.GradientShader.MakeLinear(
            [skia.Point(0, 0), skia.Point(0, HEIGHT)], [_c(pal.bg_top), _c(pal.bg_bottom)]))
        c.drawRect(skia.Rect(0, 0, WIDTH, HEIGHT), paint)
        # soft spotlight behind the action
        glow = skia.Paint(Shader=skia.GradientShader.MakeRadial(
            skia.Point(WIDTH / 2, HEIGHT * 0.53), WIDTH * 0.75,
            [_c(pal.glow, 0.55), _c(pal.glow, 0.18), _c(pal.glow, 0.0)], [0.0, 0.45, 1.0]))
        c.drawRect(skia.Rect(0, 0, WIDTH, HEIGHT), glow)
        vignette = skia.Paint(Shader=skia.GradientShader.MakeRadial(
            skia.Point(WIDTH / 2, HEIGHT / 2), HEIGHT * 0.62,
            [_c((0, 0, 0), 0.0), _c((0, 0, 0), 0.0), _c((0, 0, 0), 0.55)], [0.0, 0.55, 1.0]))
        c.drawRect(skia.Rect(0, 0, WIDTH, HEIGHT), vignette)
        return surf.makeImageSnapshot()

    @staticmethod
    def _dither(px: np.ndarray) -> np.ndarray:
        """Static +-1 LSB noise so dark gradients don't band after 8-bit YUV
        encoding. It is identical every frame, so it costs almost no bitrate."""
        rng = np.random.default_rng(1234)
        noise = rng.integers(-1, 2, size=px.shape[:2] + (1,), dtype=np.int16)
        out = px.astype(np.int16)
        out[..., :3] += noise
        return np.clip(out, 0, 255).astype(np.uint8)

    def _make_hook(self, text: str) -> tuple[skia.Image, int]:
        """Pre-render the hook text block (it never changes). Returns the image
        and its top offset in output pixels."""
        pal = self.palette
        max_w, size = WIDTH - 140, 78.0
        tf = typeface("black")
        while True:
            font = skia.Font(tf, size)
            lines = wrap_text(text, font, max_w)
            if len(lines) <= 3 or size <= 44:
                break
            size -= 4
        metrics = font.getMetrics()
        line_h = size * 1.12
        block_h = line_h * len(lines)
        top = 330 - block_h / 2  # block is centered on y=330, clear of the Shorts top bar
        y0 = max(0.0, top - 40)
        y1 = top + block_h + 60
        oy = int(math.floor(y0 * self.scale))
        surf = self._surface(self.width, int(math.ceil(y1 * self.scale)) - oy)
        c = surf.getCanvas()
        c.clear(skia.ColorTRANSPARENT)
        c.translate(0, -oy)
        c.scale(self.scale, self.scale)
        shadow = skia.Paint(AntiAlias=True, Color=_c((0, 0, 0), 0.55),
                            MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 10))
        stroke = skia.Paint(AntiAlias=True, Color=_c(pal.text_stroke), Style=skia.Paint.kStroke_Style,
                            StrokeWidth=size * 0.16, StrokeJoin=skia.Paint.kRound_Join)
        fill = skia.Paint(AntiAlias=True, Color=_c(pal.text))
        for i, line in enumerate(lines):
            w = font.measureText(line)
            x = (WIDTH - w) / 2
            baseline = top + i * line_h + line_h / 2 - (metrics.fAscent + metrics.fDescent) / 2
            blob = skia.TextBlob.MakeFromString(line, font)
            c.drawTextBlob(blob, x + 4, baseline + 8, shadow)
            c.drawTextBlob(blob, x, baseline, stroke)
            c.drawTextBlob(blob, x, baseline, fill)
        return surf.makeImageSnapshot(), oy

    # --------------------------------------------------------------- helpers
    def _blur(self, sigma: float) -> skia.MaskFilter:
        key = round(sigma, 1)
        mf = self._blur_cache.get(key)
        if mf is None:
            mf = skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, key)
            self._blur_cache[key] = mf
        return mf

    def _trail(self, n: int, circle: Circle) -> list[tuple[float, float]]:
        fps = self.sim.fps
        k = max(1, math.ceil(circle.trail * fps))
        pts = []
        for i in range(max(0, n - k), n):
            for item in self.sim.frames[i].items:
                if isinstance(item, Circle) and item.id == circle.id:
                    pts.append((item.x, item.y))
                    break
        pts.append((circle.x, circle.y))
        return pts

    # ----------------------------------------------------------- primitives
    def _draw_circle(self, c: skia.Canvas, n: int, o: Circle) -> None:
        if o.r <= 0.2:
            return
        if o.trail > 0 and o.id is not None:
            pts = self._trail(n, o)
            m = len(pts) - 1
            for i in range(m):
                k = (i + 1) / (m + 1)
                paint = skia.Paint(AntiAlias=True, Color=_c(o.color, 0.32 * k ** 1.4 * o.alpha),
                                   Style=skia.Paint.kStroke_Style, StrokeCap=skia.Paint.kRound_Cap,
                                   StrokeWidth=2 * o.r * (0.35 + 0.6 * k))
                c.drawLine(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], paint)
        if o.glow > 0:
            outer = o.r * (1.55 + 0.6 * o.glow)
            paint = skia.Paint(AntiAlias=True, Shader=skia.GradientShader.MakeRadial(
                skia.Point(o.x, o.y), outer,
                [_c(o.color, 0.5 * o.glow * o.alpha), _c(o.color, 0.18 * o.glow * o.alpha), _c(o.color, 0.0)],
                [o.r / outer, (o.r / outer + 1) / 2, 1.0]))
            c.drawCircle(o.x, o.y, outer, paint)
        if o.shade:
            hi = mix(o.color, (255, 255, 255), 0.45)
            lo = mix(o.color, (0, 0, 0), 0.18)
            paint = skia.Paint(AntiAlias=True, Shader=skia.GradientShader.MakeRadial(
                skia.Point(o.x - o.r * 0.32, o.y - o.r * 0.38), o.r * 1.45,
                [_c(hi, o.alpha), _c(o.color, o.alpha), _c(lo, o.alpha)], [0.0, 0.55, 1.0]))
        else:
            paint = skia.Paint(AntiAlias=True, Color=_c(o.color, o.alpha))
        c.drawCircle(o.x, o.y, o.r, paint)
        if o.outline is not None and o.outline_width > 0:
            paint = skia.Paint(AntiAlias=True, Color=_c(o.outline, o.alpha), Style=skia.Paint.kStroke_Style,
                               StrokeWidth=o.outline_width)
            c.drawCircle(o.x, o.y, o.r - o.outline_width / 2, paint)

    def _draw_arc(self, c: skia.Canvas, o: Arc) -> None:
        if o.alpha <= 0.003:
            return
        transformed = o.rot or o.dx or o.dy
        if transformed:
            c.save()
            c.translate(o.dx, o.dy)
            c.rotate(o.rot, o.px, o.py)
        rect = skia.Rect(o.cx - o.r, o.cy - o.r, o.cx + o.r, o.cy + o.r)
        path = skia.Path()
        if o.sweep >= 360:
            path.addCircle(o.cx, o.cy, o.r)
        else:
            path.addArc(rect, o.start, o.sweep)
        cap = skia.Paint.kRound_Cap if o.round_caps else skia.Paint.kButt_Cap
        if o.glow > 0:
            glow = skia.Paint(AntiAlias=True, Color=_c(o.color, 0.55 * o.glow * o.alpha),
                              Style=skia.Paint.kStroke_Style, StrokeWidth=o.width * 2.2, StrokeCap=cap,
                              MaskFilter=self._blur(o.width * 0.9))
            c.drawPath(path, glow)
        paint = skia.Paint(AntiAlias=True, Color=_c(o.color, o.alpha), Style=skia.Paint.kStroke_Style,
                           StrokeWidth=o.width, StrokeCap=cap)
        c.drawPath(path, paint)
        if transformed:
            c.restore()

    def _draw_ripple(self, c: skia.Canvas, o: Ripple) -> None:
        if o.alpha <= 0.003 or o.r <= 0:
            return
        paint = skia.Paint(AntiAlias=True, Color=_c(o.color, o.alpha), Style=skia.Paint.kStroke_Style,
                           StrokeWidth=max(0.5, o.width))
        c.drawCircle(o.x, o.y, o.r, paint)

    def _draw_particle(self, c: skia.Canvas, o: Particle) -> None:
        if o.alpha <= 0.003 or o.r <= 0.1:
            return
        if o.glow:
            c.drawCircle(o.x, o.y, o.r * 2.6, skia.Paint(AntiAlias=True, Color=_c(o.color, 0.25 * o.alpha)))
        c.drawCircle(o.x, o.y, o.r, skia.Paint(AntiAlias=True, Color=_c(o.color, o.alpha)))

    def _draw_label(self, c: skia.Canvas, o: Label) -> None:
        if o.alpha <= 0.003 or not o.text:
            return
        font = skia.Font(typeface(o.weight), o.size)
        m = font.getMetrics()
        w = font.measureText(o.text)
        x = o.x - w / 2
        baseline = o.y - (m.fAscent + m.fDescent) / 2
        blob = skia.TextBlob.MakeFromString(o.text, font)
        if o.stroke is not None and o.stroke_width > 0:
            c.drawTextBlob(blob, x, baseline, skia.Paint(
                AntiAlias=True, Color=_c(o.stroke, o.alpha), Style=skia.Paint.kStroke_Style,
                StrokeWidth=o.stroke_width, StrokeJoin=skia.Paint.kRound_Join))
        c.drawTextBlob(blob, x, baseline, skia.Paint(AntiAlias=True, Color=_c(o.color, o.alpha)))

    def _draw_items(self, c: skia.Canvas, n: int, items: list) -> None:
        for o in items:
            if isinstance(o, Circle):
                self._draw_circle(c, n, o)
            elif isinstance(o, Arc):
                self._draw_arc(c, o)
            elif isinstance(o, Ripple):
                self._draw_ripple(c, o)
            elif isinstance(o, Particle):
                self._draw_particle(c, o)
            elif isinstance(o, Label):
                self._draw_label(c, o)
            else:
                raise TypeError(f"Renderer cannot draw {type(o).__name__}")

    @staticmethod
    def _color_filter(fx: Effects) -> skia.ColorFilter | None:
        s = fx.desaturate
        ta = fx.tint_amount if fx.tint is not None else 0.0
        if s <= 0.001 and ta <= 0.001:
            return None
        lum = (0.2126, 0.7152, 0.0722)
        rows = []
        tint = fx.tint or (255, 255, 255)
        for ch in range(3):
            row = []
            for j in range(3):
                ident = 1.0 if j == ch else 0.0
                v = ident * (1 - s) + lum[j] * s
                v = v * (1 - ta) + ta * (tint[ch] / 255.0) * lum[j]
                row.append(v)
            rows.append(row + [0.0, 0.0])
        rows.append([0.0, 0.0, 0.0, 1.0, 0.0])
        return skia.ColorFilters.Matrix([v for row in rows for v in row])

    # ------------------------------------------------------------------ frame
    def render(self, n: int) -> np.ndarray:
        """Draw frame ``n`` and return the RGBA buffer (reused between calls)."""
        scene: Scene = self.sim.frames[n]
        fx = scene.fx
        c = self.surface.getCanvas()
        cf = self._color_filter(fx)
        if cf is not None:
            c.saveLayer(None, skia.Paint(ColorFilter=cf))
            c.drawImage(self.background, 0, 0)  # already at output resolution
        else:
            np.copyto(self.buffer, self.background_px)
        c.save()
        c.scale(self.scale, self.scale)
        if fx.shake_x or fx.shake_y:
            c.translate(fx.shake_x, fx.shake_y)
        if abs(fx.zoom - 1.0) > 1e-4:
            c.translate(fx.zoom_x, fx.zoom_y)
            c.scale(fx.zoom, fx.zoom)
            c.translate(-fx.zoom_x, -fx.zoom_y)
        self._draw_items(c, n, scene.items)
        c.restore()
        if cf is not None:
            c.restore()

        c.save()
        c.scale(self.scale, self.scale)
        if fx.vignette > 0:
            c.drawRect(skia.Rect(0, 0, WIDTH, HEIGHT), skia.Paint(Shader=skia.GradientShader.MakeRadial(
                skia.Point(WIDTH / 2, HEIGHT / 2), HEIGHT * 0.6,
                [_c((0, 0, 0), 0.0), _c((0, 0, 0), fx.vignette)], [0.35, 1.0])))
        if fx.flash > 0.002:
            c.drawRect(skia.Rect(0, 0, WIDTH, HEIGHT), skia.Paint(Color=_c(fx.flash_color, 0.3 * fx.flash)))
        self._draw_items(c, n, scene.overlay)
        c.restore()
        if self.hook is not None:
            c.drawImage(self.hook[0], 0, self.hook[1])
        return self.buffer
