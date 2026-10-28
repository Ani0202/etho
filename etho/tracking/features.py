"""Per-object and per-pair geometry from decoded SAM masks, one frame at a time.

Everything here works on box-local masks (as SAM sends them) so no full-frame dense array is
ever built for a whole video.
"""

from __future__ import annotations

import cv2
import numpy as np

FEATURE_DOC = {
    # per animal (prefix r_ = resident, i_ = intruder)
    "present": "1 if the animal was detected in this sample",
    "cx, cy": "mask centroid, px",
    "area": "mask area, px",
    "visible_ratio": "area / this animal's median area in the video (drops when occluded, e.g. under a mount)",
    "angle": "body axis orientation, radians (image coords, y down)",
    "elong": "major/minor axis ratio of the mask",
    "head_x, head_y": "estimated head point, px",
    "heading": "direction tail->head, radians",
    "speed": "centroid speed, px/s (smoothed over 0.3 s)",
    # per pair
    "dist": "centroid distance, px",
    "gap": "minimum distance between the two masks, px (0 = touching/overlapping)",
    "head_to_body": "distance from this animal's head to the nearest pixel of the other animal, px",
    "facing": "angle between this animal's heading and the direction to the other animal, radians (0 = facing)",
    "approach": "rate of change of centroid distance, px/s (negative = closing in)",
}


def head_point(mask: np.ndarray, left: int, top: int, cx: float, cy: float, angle: float,
               part_xy: tuple[float, float] | None = None) -> tuple[float, float]:
    """Head estimate. If a part attached to the animal is known (e.g. a head implant), use it.
    Otherwise, along the body axis the tail end reaches much farther (thin tail): head = the
    other end (95th percentile of the projection on that side)."""
    if part_xy is not None:
        return part_xy
    ys, xs = np.nonzero(mask)
    if len(xs) < 5 or not np.isfinite(angle):
        return cx, cy
    ux, uy = np.cos(angle), np.sin(angle)
    proj = (xs + left - cx) * ux + (ys + top - cy) * uy
    lo, hi = np.percentile(proj, 1), np.percentile(proj, 99)
    if abs(lo) > abs(hi):  # tail on the negative side
        d = np.percentile(proj[proj > 0], 95) if (proj > 0).any() else 0.0
    else:
        d = np.percentile(proj[proj < 0], 5) if (proj < 0).any() else 0.0
    return float(cx + d * ux), float(cy + d * uy)


def mask_gap(ma: np.ndarray, la: int, ta: int, mb: np.ndarray, lb: int, tb: int) -> float:
    """Minimum pixel distance between two box-local masks (0 if they touch or overlap)."""
    l0, t0 = min(la, lb), min(ta, tb)
    r1 = max(la + ma.shape[1], lb + mb.shape[1])
    b1 = max(ta + ma.shape[0], tb + mb.shape[0])
    h, w = b1 - t0, r1 - l0
    if h * w > 4_000_000:  # absurd boxes: fall back to box gap
        return 0.0
    a = np.zeros((h, w), np.uint8)
    a[ta - t0:ta - t0 + ma.shape[0], la - l0:la - l0 + ma.shape[1]] = ma
    b = np.zeros((h, w), np.uint8)
    b[tb - t0:tb - t0 + mb.shape[0], lb - l0:lb - l0 + mb.shape[1]] = mb
    if not a.any() or not b.any():
        return float("nan")
    if (a & b).any():
        return 0.0
    dist = cv2.distanceTransform((1 - a).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return float(dist[b.astype(bool)].min())


def point_to_mask(px: float, py: float, m: np.ndarray, left: int, top: int) -> float:
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return float("nan")
    return float(np.sqrt(((xs + left - px) ** 2 + (ys + top - py) ** 2).min()))


def angle_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs((a - b + np.pi) % (2 * np.pi) - np.pi)


def smooth(x: np.ndarray, k: int) -> np.ndarray:
    """Centered moving average that ignores NaNs."""
    if k <= 1:
        return x
    v = np.where(np.isfinite(x), x, 0.0)
    c = np.isfinite(x).astype(float)
    ker = np.ones(k)
    num = np.convolve(v, ker, mode="same")
    den = np.convolve(c, ker, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def finalize(table: dict[str, np.ndarray], fps: float) -> dict[str, np.ndarray]:
    """Add time-derivative and relational features to the per-sample table (in place)."""
    k = max(1, int(round(0.3 * fps)))
    for a, b in (("r", "i"), ("i", "r")):
        cx, cy = smooth(table[f"{a}_cx"], k), smooth(table[f"{a}_cy"], k)
        table[f"{a}_speed"] = np.hypot(np.gradient(cx), np.gradient(cy)) * fps
        hx, hy = table[f"{a}_head_x"], table[f"{a}_head_y"]
        heading = np.arctan2(hy - table[f"{a}_cy"], hx - table[f"{a}_cx"])
        table[f"{a}_heading"] = heading
        to_other = np.arctan2(table[f"{b}_cy"] - table[f"{a}_cy"], table[f"{b}_cx"] - table[f"{a}_cx"])
        table[f"{a}_facing"] = angle_diff(heading, to_other)
    table["approach"] = np.gradient(smooth(table["dist"], k)) * fps
    for a in ("r", "i"):
        med = np.nanmedian(table[f"{a}_area"]) if np.isfinite(table[f"{a}_area"]).any() else np.nan
        ok = bool(med) and np.isfinite(med)
        table[f"{a}_visible_ratio"] = table[f"{a}_area"] / med if ok else table[f"{a}_area"] * np.nan
    return table
