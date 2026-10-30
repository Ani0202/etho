"""Frame drawing primitives: ethogram bars (app colors and fill patterns), alpha blits, glyph text.

Text is rendered by Chromium (real fonts) and blitted; Python only draws rectangles, so there is no imaging
library beyond OpenCV and NumPy. Everything is deterministic.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from video import common as C

TILE = 14


def _pattern_masks() -> dict[str, np.ndarray]:
    """White-overlay alpha masks (TILE x TILE) per behavior, mirroring etho/app/static/ethogram.js patternDefs:
    solid (attack), diagonal lines (investigation), dots (mount)."""
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    diag = (((xx + yy) % TILE) < 3).astype(np.float32) * 0.55
    dots = np.zeros((TILE, TILE), np.float32)
    for cx, cy in ((TILE // 4, TILE // 4), (3 * TILE // 4, 3 * TILE // 4)):
        dots[((xx - cx) ** 2 + (yy - cy) ** 2) <= 2.3 ** 2] = 0.8
    return {"attack": np.zeros((TILE, TILE), np.float32), "investigation": diag, "mount": dots}


PATTERNS = _pattern_masks()


def fill_bar(img: np.ndarray, x0: float, x1: float, y0: int, y1: int, behavior: str, alpha: float = 1.0) -> None:
    xa, xb = int(round(max(0, x0))), int(round(min(img.shape[1], x1)))
    if xb <= xa:
        xb = xa + 1 if xa < img.shape[1] else xa
    if xb <= xa:
        return
    region = img[y0:y1, xa:xb].astype(np.float32)
    col = np.array(C.hex2bgr(C.COLORS[behavior]), np.float32)
    tile = PATTERNS[behavior]
    ys = (np.arange(y0, y1) % TILE)[:, None]
    xs = (np.arange(xa, xb) % TILE)[None, :]
    m = tile[ys, xs][..., None]
    bar = col * (1 - m) + 255.0 * m
    img[y0:y1, xa:xb] = (region * (1 - alpha) + bar * alpha).astype(np.uint8)


@dataclass
class Rows:
    """A block of behavior rows (one per behavior) sharing a time axis."""
    x0: int            # pixel x of view start
    x1: int            # pixel x of view end
    y0: int            # top of the first row
    row_h: int
    gap: int
    fps: float = 10.0
    names: tuple[str, ...] = C.BEHAVIORS

    def y(self, i: int) -> int:
        return self.y0 + i * (self.row_h + self.gap)

    @property
    def bottom(self) -> int:
        return self.y(len(self.names) - 1) + self.row_h

    def x_of(self, t: float, view: tuple[float, float]) -> float:
        return self.x0 + (self.x1 - self.x0) * (t - view[0]) / (view[1] - view[0])

    def draw(self, img, bouts: dict, view: tuple[float, float], upto: float | None = None,
             min_px: int = 2) -> None:
        """Draw bouts (sample index ranges) clipped to the view and to `upto` seconds (live fill-in)."""
        for i, b in enumerate(self.names):
            ya, yb = self.y(i), self.y(i) + self.row_h
            cv2.line(img, (self.x0, yb + 3), (self.x1, yb + 3), C.hex2bgr(C.GRID), 1)
            for s0, s1 in bouts.get(b, []):
                t0, t1 = s0 / self.fps, s1 / self.fps
                if upto is not None:
                    t1 = min(t1, upto)
                if t1 <= t0 or t1 < view[0] or t0 > view[1]:
                    continue
                xa = self.x_of(max(t0, view[0]), view)
                xb = self.x_of(min(t1, view[1]), view)
                fill_bar(img, xa, max(xb, xa + min_px), ya, yb, b)

    def gridlines(self, img, ticks: list[float], view, color=C.GRID) -> None:
        for t in ticks:
            if view[0] <= t <= view[1]:
                x = int(round(self.x_of(t, view)))
                cv2.line(img, (x, self.y(0) - 8), (x, self.bottom + 8), C.hex2bgr(color), 1)


def blit(img: np.ndarray, layer: np.ndarray, x: int = 0, y: int = 0, opacity: float = 1.0) -> None:
    """Alpha-blend a BGRA layer onto a BGR frame at (x, y), clipped to the frame."""
    h, w = layer.shape[:2]
    xa, ya = max(0, x), max(0, y)
    xb, yb = min(img.shape[1], x + w), min(img.shape[0], y + h)
    if xb <= xa or yb <= ya:
        return
    sub = layer[ya - y:yb - y, xa - x:xb - x]
    a = (sub[..., 3:4].astype(np.float32) / 255.0) * opacity
    dst = img[ya:yb, xa:xb].astype(np.float32)
    img[ya:yb, xa:xb] = (dst * (1 - a) + sub[..., :3].astype(np.float32) * a).astype(np.uint8)


_CACHE = C.OUT / "cache"


def text_layer(html_body: str, w: int, h: int, name: str | None = None) -> np.ndarray:
    """Render an HTML snippet (transparent background, app fonts) into a BGRA array. Cached by content."""
    css = (f"html,body{{margin:0;background:transparent}}"
           f"body{{width:{w}px;height:{h}px;font-family:'{C.FONT}',sans-serif;color:{C.TEXT}}}")
    html = f"<!doctype html><meta charset=utf-8><style>{css}</style><body>{html_body}</body>"
    key = hashlib.sha1(html.encode()).hexdigest()[:16]
    out = _CACHE / f"{name or 'txt'}-{key}.png"
    if not out.exists():
        C.html_to_png(html, out, w, h, transparent=True)
    im = cv2.imread(str(out), cv2.IMREAD_UNCHANGED)
    if im.shape[2] == 3:
        im = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
    return im


class Glyphs:
    """Fixed-cell monospaced glyph atlas for dynamic numbers (time labels) in a given color and pixel size."""
    CHARS = "0123456789:.-+ sx"

    def __init__(self, px: int, color: str = C.TEXT2, bold: bool = True):
        self.px = px
        self.cw = int(round(px * 0.66))
        self.ch = int(round(px * 1.35))
        cells = "".join(f"<span style='display:inline-block;width:{self.cw}px;text-align:center'>"
                        f"{c if c != ' ' else '&nbsp;'}</span>"
                        for c in self.CHARS)
        body = (f"<div style=\"font:{'700' if bold else '400'} {px}px/{self.ch}px 'DejaVu Sans Mono',monospace;"
                f"color:{color};white-space:nowrap\">{cells}</div>")
        self.atlas = text_layer(body, self.cw * len(self.CHARS), self.ch, name=f"glyph{px}")

    def put(self, img, text: str, x: int, y: int, anchor: str = "l") -> None:
        width = self.cw * len(text)
        if anchor == "m":
            x -= width // 2
        elif anchor == "r":
            x -= width
        for i, c in enumerate(text):
            j = self.CHARS.index(c)
            blit(img, self.atlas[:, j * self.cw:(j + 1) * self.cw], x + i * self.cw, y)


def fmt_clock(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def read_image(path: Path | str) -> np.ndarray:
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise FileNotFoundError(path)
    return im


def fit_into(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """Letterbox a BGR image into a w x h canvas (page color), keeping aspect ratio."""
    ih, iw = img.shape[:2]
    s = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * s)), max(1, int(ih * s))
    out = np.full((h, w, 3), C.hex2bgr(C.PAGE), np.uint8)
    r = cv2.resize(img[..., :3], (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    out[(h - nh) // 2:(h - nh) // 2 + nh, (w - nw) // 2:(w - nw) // 2 + nw] = r
    return out
