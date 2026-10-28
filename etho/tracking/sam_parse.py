"""Decode SAM 3.1 output lines into per-object, per-frame geometry, one frame at a time.

Masks are decoded, reduced to features, and dropped; only a compact COCO RLE is kept (if asked).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np
from meta_sam_parser import (
    CompletedOutcome,
    decode_mask_to_raster,
    image_segmentation_format,
    video_segmentation_format,
)
from pycocotools import mask as mask_utils


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


def mask_moments(m: np.ndarray, left: int, top: int) -> tuple[int, float, float, float, float]:
    ys, xs = np.nonzero(m)
    area = int(len(xs))
    if area == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    cx, cy = xs.mean(), ys.mean()
    dx, dy = xs - cx, ys - cy
    mu20, mu02, mu11 = (dx * dx).mean(), (dy * dy).mean(), (dx * dy).mean()
    angle = 0.5 * np.arctan2(2 * mu11, mu20 - mu02)
    common = np.sqrt(4 * mu11 ** 2 + (mu20 - mu02) ** 2)
    l1, l2 = (mu20 + mu02 + common) / 2, (mu20 + mu02 - common) / 2
    elong = float(np.sqrt(l1 / l2)) if l2 > 1e-9 else float("inf")
    return area, float(cx + left), float(cy + top), float(angle), elong


def parse_line(line: str, media: str = "video", frame_shape: tuple[int, int] | None = None,
               keep_rle: bool = False, want_mask: bool = False):
    """Parse one SAM output line. Returns list of (ObjectFrame, local_mask or None)."""
    fmt = video_segmentation_format() if media == "video" else image_segmentation_format()
    p = fmt.create_parser()
    p.push(line + "\n", emit=False)
    res = p.finish(CompletedOutcome()).result
    out = []
    for rec in res.records:
        if rec.kind != "mask":
            continue
        m = np.frombuffer(decode_mask_to_raster(rec.mask), dtype=np.uint8).reshape(
            rec.mask.height, rec.mask.width)
        b = rec.bounds
        left, top = int(b.left), int(b.top)
        area, cx, cy, ang, el = mask_moments(m, left, top)
        frame = rec.frame.frame_index if rec.frame is not None else 0
        rle = None
        if keep_rle and frame_shape is not None:
            full = np.zeros(frame_shape, dtype=np.uint8, order="F")
            h, w = m.shape
            full[top:top + h, left:left + w] = m[: frame_shape[0] - top, : frame_shape[1] - left]
            rle = mask_utils.encode(full)
            rle["counts"] = rle["counts"].decode()
        of = ObjectFrame(frame, rec.object_id, (left, top, int(b.right), int(b.bottom)), area, cx, cy, ang, el,
                         rec.confidence, None, rle)
        out.append((of, m if want_mask else None))
    return out


def iter_objects(lines: Iterable[str], **kw) -> Iterator[tuple[ObjectFrame, np.ndarray | None]]:
    for line in lines:
        yield from parse_line(line, **kw)
