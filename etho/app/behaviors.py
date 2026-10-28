"""Label bookkeeping shared by the runner, the report, the overlay and the correction loop.

Per-sample labels are int codes: behavior i -> i (in the user's order), "other" -> len(names).
Overlapping detector outputs are resolved with the same priority as `scripts/run_pipeline.py`
(mount > attack > investigation); behaviors outside that trio rank first in the user's order.
"""

from __future__ import annotations

import contextlib
import threading

import numpy as np

STD_PRIORITY = ("mount", "attack", "investigation")  # CalMS21 overlap resolution (scripts/run_pipeline.py)

# Light-mode palette; order follows the report's first three series slots (blue, orange, green).
PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#8e5bd9", "#c9a227", "#d0488f", "#3a9db8", "#7a7a72")
STD_COLOR_INDEX = {"attack": 0, "investigation": 1, "mount": 2}


def color_index(names: list[str]) -> dict[str, int]:
    """Standard behaviors keep their report colors; everything else takes the next free slot."""
    out, used = {}, set()
    for n in names:
        if n in STD_COLOR_INDEX:
            out[n] = STD_COLOR_INDEX[n]
            used.add(out[n])
    free = (i for i in range(len(PALETTE)) if i not in used)
    for n in names:
        if n not in out:
            out[n] = next(free, len(PALETTE) - 1)
    return out


def make_codes(names: list[str]) -> dict[str, int]:
    return {**{n: i for i, n in enumerate(names)}, "other": len(names)}


def priority(names: list[str]) -> list[str]:
    """Highest priority first."""
    custom = [n for n in names if n not in STD_PRIORITY]
    return custom + [n for n in STD_PRIORITY if n in names]


def labels_from_masks(masks: dict[str, np.ndarray], names: list[str], n: int) -> np.ndarray:
    codes = make_codes(names)
    lab = np.full(n, codes["other"], np.int8)
    for b in reversed(priority(names)):  # lowest priority first, so higher ones overwrite
        if b in masks:
            lab[np.asarray(masks[b], bool)] = codes[b]
    return lab


def bouts(mask: np.ndarray) -> list[tuple[int, int]]:
    m = np.r_[0, np.asarray(mask).astype(int), 0]
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist(), strict=True))


def bouts_by_behavior(labels: np.ndarray, names: list[str]) -> dict[str, list[list[int]]]:
    codes = make_codes(names)
    return {b: [[s, e] for s, e in bouts(labels == codes[b])] for b in names}


def metrics(labels: np.ndarray, names: list[str], fps: float) -> dict[str, dict]:
    n = len(labels)
    out = {}
    for b, bs in bouts_by_behavior(labels, names).items():
        durs = [(e - s) / fps for s, e in bs]
        out[b] = {"bouts": len(bs), "total_s": round(sum(durs), 1),
                  "pct_time": round(100 * sum(durs) / (n / fps), 1) if n else 0.0,
                  "latency_s": round(bs[0][0] / fps, 1) if bs else None,
                  "mean_bout_s": round(float(np.mean(durs)), 2) if durs else None}
    return out


def tier1_allow(names: list[str]) -> frozenset[str]:
    """Which verdicts tier 1 may apply. The CalMS21 dev finding (investigation and "none" only) is specific
    to those behaviors; for anything else tier 1 may only reject a window ("none")."""
    from etho.adjudication.tier1 import DEFAULT_ALLOW
    return frozenset((DEFAULT_ALLOW & set(names)) | {"none"})


_REPORT_LOCK = threading.Lock()


@contextlib.contextmanager
def report_behaviors(names: list[str]):
    """etho.report.report renders a fixed behavior trio. Point its module-level order/slot tables at this
    job's behaviors for the duration of one build() (the report module itself is unchanged)."""
    from etho.report import report as R
    with _REPORT_LOCK:
        old = R.BEHAVIOR_ORDER, R.SLOTS
        R.BEHAVIOR_ORDER = tuple(names)
        ci = color_index(names)
        R.SLOTS = {n: (ci[n] % 3) + 1 for n in names}  # the report CSS defines 3 series colors
        try:
            yield
        finally:
            R.BEHAVIOR_ORDER, R.SLOTS = old
