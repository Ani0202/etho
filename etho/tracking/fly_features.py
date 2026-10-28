"""Features for two identical, symmetric animals (flies), from SAM masks (ADR-002).

Fly A / fly B are arbitrary labels (see fly_identity.py), so every per-fly feature exists for both
(`a_*`, `b_*`) and every pair-level feature is symmetric (max/min over the two flies) because the
primary metric is pair-level ("either fly does it"). All lengths are in SOURCE pixels (144 px
frame), whatever upscale the video went through; a fly is roughly 25-30 px long.
"""

from __future__ import annotations

import numpy as np

from .features import angle_diff, smooth

FLY_FEATURE_DOC = {
    # ---- per fly (prefix a_ or b_) ----
    "present": "1 if this fly was detected in this sample",
    "cx, cy": "mask centroid, px (source 144x144 frame, y down). Moves when a wing appears or vanishes",
    "bx, by": "body-only centroid, px: centroid of the mask after a 3x3 opening, which removes thin spurs. "
              "All velocities, toward_speed and approach use this, so a wing opening does not look like a lunge. "
              "(Broad translucent wings can survive the opening and still shift it a little)",
    "area": "mask area, px^2. The mask includes the translucent abdomen; a wing held out from the body adds area",
    "area_ratio": "area / this fly's median area in the video (1 = typical; > 1 = bigger than usual, e.g. a wing out)",
    "major_len": "major-axis length of the ellipse with the mask's second moments, px (body length ~ 25-30)",
    "minor_len": "minor-axis length of that ellipse, px (body width; a wing extended sideways increases it)",
    "minor_ratio": "minor_len / this fly's median minor_len",
    "elong": "major/minor axis ratio (a wing extension lowers it)",
    "solidity": "mask area / convex-hull area, 0..1 (an extended wing or leg lowers it)",
    "thin_frac": "share of mask pixels that vanish under a 3x3 opening, 0..1: thin spurs (an extended wing or "
                 "leg) raise it; ~0 for a compact body",
    "reach_ratio": "farthest mask pixel from the centroid / half of major_len (~1 compact; > 1.2 a spur sticking out)",
    "angle": "body-axis orientation, radians in (-pi/2, pi/2] (the axis has no head/tail sign)",
    "heading": "body direction tail->head, radians (NaN where vel_ok_s = 0). The axis sign is taken from "
               "motion (when the fly moves > ~1 body length/s) and carried over while it is still, so it is "
               "wrong for a fly that has never moved and can be reversed after a fast backwards step. Use "
               "`axis_align` when unsure",
    "speed": "body-centroid speed, px/s (smoothed over 0.1 s). NaN where vel_ok = 0",
    "speed_s": "body-centroid speed, px/s (smoothed over 0.5 s). NaN where vel_ok_s = 0",
    "toward_speed": "body-centroid velocity component toward the other fly, px/s (positive = approaching; "
                    "0.1 s smoothing; NaN where vel_ok = 0)",
    "toward_speed_s": "same, 0.5 s smoothing",
    "facing": "angle between this fly's heading and the direction to the other fly, radians 0..pi (0 = facing "
              "it; NaN where vel_ok_s = 0)",
    "axis_align": "angle between this fly's body axis (either end) and the direction to the other fly, "
                  "radians 0..pi/2 (0 = pointing straight at or away from it); needs no heading sign",
    "ang_speed": "rotation speed of the body axis, rad/s (0.1 s smoothing)",
    # ---- per pair ----
    "dist": "centroid distance between the flies, px",
    "dist_bl": "centroid distance in body lengths (dist / median major_len of both flies)",
    "gap": "minimum distance between the two masks, px (0 = touching/overlapping)",
    "contact": "1 if gap <= 1 px (masks touch or overlap)",
    "dist_body": "distance between the body-only centroids, px",
    "approach": "rate of change of dist_body, px/s (negative = closing in; 0.1 s smoothing; NaN where vel_ok = 0)",
    "vel_ok": "1 if the fast (0.1 s) velocity columns are valid: not within 0.1 s of an identity-epoch change or of "
              "the edge of an id_unknown run (where a silent id swap would create a fake jump), or an abrupt "
              "(> 25%) mask-area change (a wing appearing in the mask shifts the centroid)",
    "vel_ok_s": "same for the slow (0.5 s) columns (speed_s, toward_speed_s, heading, facing); a wider margin",
    "both_present": "1 if both flies were detected",
    "n_objects": "SAM objects in the sample before identity (> 2 means extra detections were ignored)",
    "id_unknown": "1 if the flies are within 1 body length, so which is A and which is B cannot be told",
    "id_epoch": "A/B labels are consistent only within one epoch (a chunk stitch the data could not decide "
                "starts a new epoch); pair-level features do not depend on it",
    "src_frame": "source movie frame index of this sample (exact)",
    # ---- pair-level symmetric (the primary metric is pair-level) ----
    "max_speed, max_speed_s": "max over the two flies",
    "max_toward_speed, max_toward_speed_s": "max over the two flies of toward_speed (one fly lunging/charging)",
    "max_area_ratio": "max over the flies of area_ratio",
    "max_minor_ratio": "max over the flies of minor_ratio",
    "min_elong": "min over the flies of elong",
    "min_solidity": "min over the flies of solidity",
    "max_thin_frac": "max over the flies of thin_frac",
    "max_reach_ratio": "max over the flies of reach_ratio",
    "min_axis_align": "min over the flies of axis_align (one of them points at the other)",
    "min_facing": "min over the flies of facing",
    "max_ang_speed": "max over the flies of ang_speed",
}

SHAPE_JUMP = 0.25  # relative mask-area change between consecutive samples that invalidates nearby velocities

PER_FLY_RAW = ("present", "cx", "cy", "bx", "by", "area", "angle", "elong", "major_len", "minor_len", "solidity",
               "thin_frac", "reach_ratio")


def resolve_heading(angle: np.ndarray, vx: np.ndarray, vy: np.ndarray, speed: np.ndarray,
                    thr: float) -> np.ndarray:
    """Give the body axis a head/tail sign from motion. Between anchors (samples moving faster than
    `thr` px/s) the sign follows axis continuity; before the first anchor it is back-filled from it.
    A fly that never moves keeps sign +1 (the raw axis angle)."""
    n = len(angle)
    sgn = np.ones(n)
    prev_u, prev_s = None, 1.0
    first_anchor = None
    for i in range(n):
        if not np.isfinite(angle[i]):
            continue
        u = np.array([np.cos(angle[i]), np.sin(angle[i])])
        s = prev_s
        if prev_u is not None and float(u @ prev_u) < 0:  # axis flipped by pi between samples: keep direction
            s = -prev_s
        if np.isfinite(speed[i]) and speed[i] > thr:
            move = np.array([vx[i], vy[i]])
            s_anchor = 1.0 if float(u @ move) >= 0 else -1.0
            if first_anchor is None:
                first_anchor = i
                sgn[:i][np.isfinite(angle[:i])] *= s_anchor * s
            s = s_anchor
        sgn[i] = s
        prev_u, prev_s = u, s
    # note: continuity compares the *raw* axis vectors u_t, so a pi wrap of `angle` is handled above.
    h = angle + np.where(sgn < 0, np.pi, 0.0)
    return (h + np.pi) % (2 * np.pi) - np.pi


def finalize_fly(t: dict[str, np.ndarray], fps: float, bl: float) -> dict[str, np.ndarray]:
    """Add derived per-fly, pair and pair-level features to the per-sample table (in place)."""
    k_fast = max(1, int(round(0.1 * fps)))
    k_slow = max(1, int(round(0.5 * fps)))
    n = len(t["a_cx"])
    for p in ("a", "b"):
        for c in ("area", "minor_len"):
            med = np.nanmedian(t[f"{p}_{c}"]) if np.isfinite(t[f"{p}_{c}"]).any() else np.nan
            name = "area_ratio" if c == "area" else "minor_ratio"
            t[f"{p}_{name}"] = t[f"{p}_{c}"] / med if np.isfinite(med) and med else np.full(n, np.nan)
    t["dist"] = np.hypot(t["a_cx"] - t["b_cx"], t["a_cy"] - t["b_cy"])
    t["dist_bl"] = t["dist"] / bl
    t["contact"] = np.where(np.isfinite(t["gap"]), (t["gap"] <= 1.0).astype(float), np.nan)
    t["both_present"] = ((t["a_present"] > 0) & (t["b_present"] > 0)).astype(float)
    t["dist_body"] = np.hypot(t["a_bx"] - t["b_bx"], t["a_by"] - t["b_by"])
    t["approach"] = np.gradient(smooth(t["dist_body"], k_fast)) * fps
    for p, q in (("a", "b"), ("b", "a")):
        cxf, cyf = smooth(t[f"{p}_bx"], k_fast), smooth(t[f"{p}_by"], k_fast)
        cxs, cys = smooth(t[f"{p}_bx"], k_slow), smooth(t[f"{p}_by"], k_slow)
        vx, vy = np.gradient(cxf) * fps, np.gradient(cyf) * fps
        vxs, vys = np.gradient(cxs) * fps, np.gradient(cys) * fps
        t[f"{p}_speed"] = np.hypot(vx, vy)
        t[f"{p}_speed_s"] = np.hypot(vxs, vys)
        ux, uy = t[f"{q}_bx"] - t[f"{p}_bx"], t[f"{q}_by"] - t[f"{p}_by"]
        norm = np.hypot(ux, uy)
        with np.errstate(invalid="ignore", divide="ignore"):
            ux, uy = ux / norm, uy / norm
        t[f"{p}_toward_speed"] = vx * ux + vy * uy
        t[f"{p}_toward_speed_s"] = vxs * ux + vys * uy
        # axis angle (no sign) vs direction to the partner; heading (signed) via motion
        to_other = np.arctan2(t[f"{q}_by"] - t[f"{p}_by"], t[f"{q}_bx"] - t[f"{p}_bx"])
        t[f"{p}_axis_align"] = np.minimum(angle_diff(t[f"{p}_angle"], to_other),
                                          np.pi - angle_diff(t[f"{p}_angle"], to_other))
        t[f"{p}_heading"] = resolve_heading(t[f"{p}_angle"], vxs, vys, t[f"{p}_speed_s"], thr=1.0 * bl)
        t[f"{p}_facing"] = angle_diff(t[f"{p}_heading"], to_other)
        d2 = np.diff(2 * t[f"{p}_angle"], prepend=np.nan)  # the axis is pi-periodic, so double the angle
        da = np.abs((d2 + np.pi) % (2 * np.pi) - np.pi) / 2
        t[f"{p}_ang_speed"] = smooth(da, k_fast) * fps
    ok_f, ok_s = edge_ok(t, k_fast), edge_ok(t, k_slow, k_jump=k_fast)
    t["vel_ok"], t["vel_ok_s"] = ok_f.astype(float), ok_s.astype(float)
    for p in ("a", "b"):
        for c in ("speed", "toward_speed", "ang_speed"):
            t[f"{p}_{c}"] = np.where(ok_f, t[f"{p}_{c}"], np.nan)
        for c in ("speed_s", "toward_speed_s", "heading", "facing"):
            t[f"{p}_{c}"] = np.where(ok_s, t[f"{p}_{c}"], np.nan)
    t["approach"] = np.where(ok_f, t["approach"], np.nan)
    for c in ("speed", "speed_s", "toward_speed", "toward_speed_s", "area_ratio", "minor_ratio", "ang_speed",
              "thin_frac", "reach_ratio"):
        t[f"max_{c}"] = _nanmax2(t[f"a_{c}"], t[f"b_{c}"])
    for c in ("elong", "solidity", "axis_align", "facing"):
        t[f"min_{c}"] = _nanmin2(t[f"a_{c}"], t[f"b_{c}"])
    return t


def edge_ok(t: dict[str, np.ndarray], k: int, k_jump: int | None = None) -> np.ndarray:
    """False within +-k samples of an identity-epoch change, of either edge of an id_unknown run, or of an
    abrupt mask-area change (> SHAPE_JUMP): a velocity smoothed over k samples there could straddle a swap
    of the two flies, or a wing appearing in the mask, and show a fake jump."""
    n = len(t["a_cx"])
    bad = np.zeros(n, bool)
    k_jump = k if k_jump is None else k_jump
    edges = []
    if "id_epoch" in t:
        e = np.nan_to_num(t["id_epoch"], nan=-1.0)
        edges += list(np.nonzero(e[1:] != e[:-1])[0] + 1)
    if "id_unknown" in t:
        u = np.nan_to_num(t["id_unknown"], nan=0.0) > 0
        edges += list(np.nonzero(u[1:] != u[:-1])[0] + 1)
    for p in ("a", "b"):  # abrupt mask-size change (a wing appearing or vanishing shifts the centroid)
        a = t[f"{p}_area"]
        with np.errstate(invalid="ignore", divide="ignore"):
            jump = np.abs(np.diff(a)) / np.maximum(a[:-1], 1.0) > SHAPE_JUMP
        for i in np.nonzero(np.nan_to_num(jump, nan=0.0) > 0)[0] + 1:
            bad[max(0, i - k_jump - 1):i + k_jump + 1] = True
    for i in edges:
        bad[max(0, i - k - 1):i + k + 1] = True
    return ~bad


def _nanmax2(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(x), y, np.where(np.isnan(y), x, np.maximum(x, y)))


def _nanmin2(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(x), y, np.where(np.isnan(y), x, np.minimum(x, y)))
