"""Extra pair-level motion features that are NOT masked by `vel_ok` (ADR-002 dev finding).

The tracker's `speed` / `toward_speed` / `approach` columns are NaN within 0.1 s of an id_unknown run edge or an
abrupt mask-area change (a silent A/B swap or a wing entering the mask would fake a jump). On dev, that is exactly
where a lunge happens: the strike is made at about one body length with the flies touching, and only about 10% of
the annotated lunge frames have a valid fast velocity. These columns keep the signal:

* the two flies' centroids are matched between consecutive samples by the pairing with the smaller total
  displacement, so an A/B label swap cannot produce a fake jump (`swap-robust`);
* nothing is masked beyond "both flies present now and one sample ago".

Cost: a wing appearing in the mask can still shift the body centroid a little (the body centroid already removes
thin spurs). Everything is computed from columns already in the feature table: no label, no new tracking.
"""

from __future__ import annotations

import numpy as np

from etho.tracking.features import smooth

EXTRA_DOC = {
    "max_speed_raw": "max over the two flies of the body-centroid speed, px/s (0.1 s smoothing). Unlike `max_speed`, "
                     "not masked where flies touch or masks change shape, and robust to A/B label swaps (the "
                     "flies are re-matched between samples). NaN only where a fly is missing",
    "min_speed_raw": "min over the two flies of the same speed, px/s (the quieter fly)",
    "max_toward_speed_raw": "max over the two flies of the velocity component toward the other fly, px/s (positive = "
                            "one fly closing in on the other; same matching as `max_speed_raw`; 0.1 s smoothing)",
    "closing_speed_raw": "-d(dist_body)/dt, px/s (positive = the flies approach each other; label-free; 0.1 s "
                         "smoothing)",
    "peak_toward_raw": "max of `max_toward_speed_raw` over the next 0.3 s (a strike is a short burst: use this to "
                       "mark the whole burst, including the samples just before the peak)",
}


def _pos(t: dict, p: str) -> tuple[np.ndarray, np.ndarray]:
    x, y = np.asarray(t[f"{p}_bx"], float), np.asarray(t[f"{p}_by"], float)
    return np.where(np.isfinite(x), x, np.asarray(t[f"{p}_cx"], float)), \
        np.where(np.isfinite(y), y, np.asarray(t[f"{p}_cy"], float))


def _matched_motion(t: dict[str, np.ndarray], fps: float):
    """Per-sample speed and toward-partner speed of each label (a, b), re-matching the flies between samples."""
    ax, ay = _pos(t, "a")
    bx, by = _pos(t, "b")
    ident = np.hypot(ax[1:] - ax[:-1], ay[1:] - ay[:-1]) + np.hypot(bx[1:] - bx[:-1], by[1:] - by[:-1])
    swap = np.hypot(ax[1:] - bx[:-1], ay[1:] - by[:-1]) + np.hypot(bx[1:] - ax[:-1], by[1:] - ay[:-1])
    sw = swap < ident
    pax, pay = np.where(sw, bx[:-1], ax[:-1]), np.where(sw, by[:-1], ay[:-1])  # previous position of fly A
    pbx, pby = np.where(sw, ax[:-1], bx[:-1]), np.where(sw, ay[:-1], by[:-1])
    va = np.stack([ax[1:] - pax, ay[1:] - pay], 1) * fps
    vb = np.stack([bx[1:] - pbx, by[1:] - pby], 1) * fps
    ux, uy = bx[1:] - ax[1:], by[1:] - ay[1:]
    norm = np.hypot(ux, uy)
    with np.errstate(invalid="ignore", divide="ignore"):
        ux, uy = ux / norm, uy / norm
    tow_a = va[:, 0] * ux + va[:, 1] * uy       # fly A toward B
    tow_b = -(vb[:, 0] * ux + vb[:, 1] * uy)    # fly B toward A

    def lead(x: np.ndarray) -> np.ndarray:
        return np.concatenate([[np.nan], x])

    return (lead(np.hypot(va[:, 0], va[:, 1])), lead(np.hypot(vb[:, 0], vb[:, 1])), lead(tow_a), lead(tow_b),
            ax, ay, bx, by)


def per_fly_toward_raw(t: dict[str, np.ndarray], fps: float) -> tuple[np.ndarray, np.ndarray]:
    """Unmasked, swap-robust toward-partner speed of fly a and fly b (0.1 s smoothing); used to name the actor."""
    k = max(1, int(round(0.1 * fps)))
    _, _, ta, tb, *_ = _matched_motion(t, fps)
    return smooth(ta, k), smooth(tb, k)


def add_extras(t: dict[str, np.ndarray], fps: float) -> dict[str, np.ndarray]:
    """Return a copy of the feature table with the EXTRA_DOC columns added."""
    out = dict(t)
    sa, sb, ta, tb, ax, ay, bx, by = _matched_motion(t, fps)
    n = len(ax)
    k = max(1, int(round(0.1 * fps)))
    out["max_speed_raw"] = smooth(np.fmax(sa, sb), k)
    out["min_speed_raw"] = smooth(np.fmin(sa, sb), k)
    tw = smooth(np.fmax(ta, tb), k)
    out["max_toward_speed_raw"] = tw
    dist_body = np.hypot(ax - bx, ay - by)
    out["closing_speed_raw"] = -np.gradient(smooth(dist_body, k)) * fps
    w = max(1, int(round(0.3 * fps)))
    fwd = np.where(np.isfinite(tw), tw, -np.inf)
    pk = np.full(n, -np.inf)
    for s in range(w):  # sliding forward max over [i, i + w)
        pk[: n - s] = np.maximum(pk[: n - s], fwd[s:])
    out["peak_toward_raw"] = np.where(np.isfinite(pk), pk, np.nan)
    return out
