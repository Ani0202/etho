"""Decode SAM 3.1 output lines into per-object, per-frame geometry, one frame at a time.

Masks are decoded, reduced to features, and dropped; only a compact COCO RLE is kept (if asked).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np
from numba import njit
from pycocotools import mask as mask_utils

from etho.tracking.mask_codec import MaskDecodeError, decode_mask
from etho.tracking.sam_lines import parse_sam_line

log = logging.getLogger(__name__)


@dataclass
class ObjectFrame:
    frame: int
    object_id: str
    box: tuple[int, int, int, int]  # left, top, right, bottom (half-open), source pixels
    area: int
    cx: float
    cy: float
    angle: float  # major-axis orientation, radians in (-pi/2, pi/2], image coords (y down)
    elongation: float  # major/minor axis ratio
    confidence: float | None = None
    mean_intensity: float | None = None  # filled by caller when the source frame is available
    rle: dict | None = None  # COCO RLE in full-frame coordinates (only if keep_rle)


@njit(cache=True, nogil=True)
def _central_moments(m):
    """Two-pass centroid and second central moments of the non-zero pixels (no temporaries)."""
    h, w = m.shape
    n = 0
    sx = 0
    sy = 0
    for y in range(h):
        for x in range(w):
            if m[y, x] != 0:
                n += 1
                sx += x
                sy += y
    if n == 0:
        return 0, 0.0, 0.0, 0.0, 0.0, 0.0
    cx = sx / n
    cy = sy / n
    a = 0.0
    b = 0.0
    c = 0.0
    for y in range(h):
        dy = y - cy
        for x in range(w):
            if m[y, x] != 0:
                dx = x - cx
                a += dx * dx
                b += dy * dy
                c += dx * dy
    return n, cx, cy, a / n, b / n, c / n


def mask_moments(m: np.ndarray, left: int, top: int) -> tuple[int, float, float, float, float]:
    area, cx, cy, mu20, mu02, mu11 = _central_moments(m)
    if area == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    angle = 0.5 * np.arctan2(2 * mu11, mu20 - mu02)
    common = np.sqrt(4 * mu11 ** 2 + (mu20 - mu02) ** 2)
    l1, l2 = (mu20 + mu02 + common) / 2, (mu20 + mu02 - common) / 2
    elong = float(np.sqrt(l1 / l2)) if l2 > 1e-9 else float("inf")
    return int(area), float(cx + left), float(cy + top), float(angle), elong


def parse_line(line: str, media: str = "video", frame_shape: tuple[int, int] | None = None,
               keep_rle: bool = False, want_mask: bool = False):
    """Parse one SAM output line. Returns list of (ObjectFrame, local_mask or None).

    Uses the decode-free line parser plus the numba mask decoder (each mask is decoded exactly once).
    An object whose payload is corrupt is skipped with a warning, as the reference parser did.
    """
    out = []
    for rec in parse_sam_line(line, media):
        try:
            m = decode_mask(rec.payload, rec.width, rec.height)
        except MaskDecodeError as e:
            log.warning("skipping frame %d object %s: %s", rec.frame, rec.object_id, e)
            continue
        left, top, frame = rec.left, rec.top, rec.frame
        area, cx, cy, ang, el = mask_moments(m, left, top)
        rle = None
        if keep_rle and frame_shape is not None:
            full = np.zeros(frame_shape, dtype=np.uint8, order="F")
            h, w = m.shape
            full[top:top + h, left:left + w] = m[: frame_shape[0] - top, : frame_shape[1] - left]
            rle = mask_utils.encode(full)
            rle["counts"] = rle["counts"].decode()
        of = ObjectFrame(frame, rec.object_id, rec.box, area, cx, cy, ang, el, rec.confidence, None, rle)
        out.append((of, m if want_mask else None))
    return out


def iter_objects(lines: Iterable[str], **kw) -> Iterator[tuple[ObjectFrame, np.ndarray | None]]:
    for line in lines:
        yield from parse_line(line, **kw)
