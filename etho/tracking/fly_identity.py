"""Identity for identical-looking animals (ADR-002 c). No appearance cue, no labels.

Two levels:
  1. Within one SAM request (a chunk): SAM's own track ids. `slot_chunk` binds each track id to
     one of two slots the first time it appears (Hungarian against the slots' constant-velocity
     predictions when both are free) and keeps the binding; a third object while both slots are
     held by live tracks is ignored (it is counted in `n_objects` only). When two tracks bound to
     the same slot are both present, the one with more samples in the chunk keeps it, so a short-lived
     spurious object that grabbed a free slot never displaces the real fly when it comes back.
     `relabel_swaps` then looks at every close-contact run: if the paths before and after fit better with
     the two tracks exchanged, the rest of the chunk is relabeled and the identity epoch is bumped.
  2. Across the parallel, overlapping chunks: `stitch` matches the new chunk's slots to the global
     slots (A, B) by Hungarian on the overlap samples. Cost = distance from the global fly's
     (observed, or constant-velocity predicted) centroid, in body lengths, + normalized area
     difference. When both chunks have no common samples the global fly's last state is
     extrapolated to the new slot's first observation instead.
Whenever the two centroids are within ~1 body length the per-sample identity is `unknown`, never a
guess. A stitch whose two candidate pairings are within `AMBIGUOUS_MARGIN` of each other (or that has
nothing to compare) starts a new identity *epoch*: A/B labels are only consistent inside an epoch.
(Flies close together on the overlap do not by themselves make a stitch ambiguous: two tracks that agree
with the previous chunk to a pixel are decisive even 0.9 body lengths apart; the margin test catches the
truly overlapping case.)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

UNKNOWN_BL = 1.0        # centroids closer than this many body lengths: identity unknown
AMBIGUOUS_MARGIN = 0.5  # cost margin (body lengths) between best and alternative pairing
MAX_EXTRAP = 6          # samples of constant-velocity extrapolation before holding the position
AREA_W = 1.0            # weight of the normalized area difference relative to distance in body lengths
MIN_SAMPLES = 5         # a track must appear in this many samples of the chunk to hold a slot
NEUTRAL_COST = 3.0      # body lengths: cost of a pairing with no data on one side (never attractive, never free)
SWAP_MARGIN_BL = 0.25   # per-fly cost margin (body lengths) for relabeling after a contact run


def major_len(area: float, elong: float) -> float:
    """Major-axis length (px) of the ellipse with this area and axis ratio."""
    if not (np.isfinite(area) and np.isfinite(elong)) or area <= 0:
        return float("nan")
    return float(2.0 * np.sqrt(area * elong / np.pi))


def body_length(objs: list[dict]) -> float:
    v = [major_len(o["area"], o["elong"]) for o in objs]
    v = [x for x in v if np.isfinite(x)]
    return float(np.median(v)) if v else float("nan")


@dataclass
class Traj:
    """Sparse trajectory: sample -> (cx, cy, area)."""
    pts: dict[int, tuple[float, float, float]] = field(default_factory=dict)

    def add(self, g: int, cx: float, cy: float, area: float) -> None:
        self.pts[g] = (cx, cy, area)

    def last_before(self, g: int) -> int | None:
        c = [k for k in self.pts if k <= g]
        return max(c) if c else None

    def predict(self, g: int, max_extrap: int = MAX_EXTRAP) -> tuple[float, float, float] | None:
        """Position at sample g: observed, else constant-velocity extrapolation from the last two
        observations before g (at most `max_extrap` samples ahead, then held)."""
        if g in self.pts:
            return self.pts[g]
        g1 = self.last_before(g)
        if g1 is None:
            return None
        x1, y1, a1 = self.pts[g1]
        g0 = self.last_before(g1 - 1)
        if g0 is None or g1 - g0 > 5:
            return x1, y1, a1
        x0, y0, _ = self.pts[g0]
        vx, vy = (x1 - x0) / (g1 - g0), (y1 - y0) / (g1 - g0)
        k = min(g - g1, max_extrap)
        return x1 + vx * k, y1 + vy * k, a1


def pair_cost(prev: Traj, new: Traj, samples: list[int], bl: float) -> tuple[float, int]:
    """Mean matching cost of `new` against `prev` over `samples`; (nan, 0) if nothing to compare."""
    cs = []
    for g in samples:
        if g not in new.pts:
            continue
        p = prev.predict(g)
        if p is None:
            continue
        n = new.pts[g]
        d = np.hypot(p[0] - n[0], p[1] - n[1]) / bl
        da = abs(p[2] - n[2]) / max(p[2], n[2], 1.0)
        cs.append(d + AREA_W * da)
    return (float(np.mean(cs)), len(cs)) if cs else (float("nan"), 0)


@dataclass
class Stitch:
    perm: tuple[int, int]    # perm[k] = new slot index matched to global slot k
    cost: float
    alt_cost: float
    ambiguous: bool
    n_compared: int
    n_apart: int             # overlap samples where the two flies were > UNKNOWN_BL apart
    reason: str = ""


def stitch(prev: tuple[Traj, Traj], new: tuple[Traj, Traj], overlap: list[int], bl: float) -> Stitch:
    """Match the new chunk's two slots to the global slots. `overlap` = samples both chunks cover."""
    C = np.zeros((2, 2))
    cnt = 0
    nodata = False
    for k in range(2):
        for j in range(2):
            c, n = pair_cost(prev[k], new[j], overlap, bl)
            if n == 0:  # no common samples: bridge the gap by extrapolating the global fly forward
                first = min(new[j].pts) if new[j].pts else None
                p = prev[k].predict(first) if first is not None else None
                if p is not None:
                    q = new[j].pts[first]
                    c = float(np.hypot(p[0] - q[0], p[1] - q[1]) / bl
                              + AREA_W * abs(p[2] - q[2]) / max(p[2], q[2], 1.0))
                    n = 1
            if n == 0:  # nothing to compare: a large neutral cost, and the stitch cannot be trusted
                nodata = True
                c = NEUTRAL_COST
            C[k, j] = c
            cnt = max(cnt, n)
    keep, swap = C[0, 0] + C[1, 1], C[0, 1] + C[1, 0]
    rows, cols = linear_sum_assignment(C)
    perm = (int(cols[0]), int(cols[1]))
    best, alt = (keep, swap) if perm == (0, 1) else (swap, keep)
    # How many overlap samples had the flies clearly apart (so that position evidence is decisive)?
    n_apart = 0
    for g in overlap:
        if g in prev[0].pts and g in prev[1].pts:
            a, b = prev[0].pts[g], prev[1].pts[g]
            n_apart += int(np.hypot(a[0] - b[0], a[1] - b[1]) > UNKNOWN_BL * bl)
    margin = (alt - best) / 2.0  # per-fly cost units
    reasons = []
    if cnt == 0:
        reasons.append("no comparable samples")
    elif nodata:
        reasons.append("a slot has no data to compare")
    if margin < AMBIGUOUS_MARGIN:
        reasons.append(f"margin {margin:.2f} < {AMBIGUOUS_MARGIN}")
    return Stitch(perm, float(best), float(alt), bool(reasons), cnt, n_apart, "; ".join(reasons))


def slot_chunk(objs: list[dict], first: int, last: int, bl: float, area_med: float) -> tuple[list[Traj], dict]:
    """Bind SAM tracks to two slots inside one chunk. objs: dicts with g, track, cx, cy, area (source px).
    Returns (two slot trajectories keyed by global sample, per-sample [slot0 obj, slot1 obj])."""
    by_g: dict[int, list[dict]] = defaultdict(list)
    ntr: dict[str, int] = defaultdict(int)
    for o in objs:
        ntr[o["track"]] += 1
    for o in objs:
        # drop slivers (a track seen in fewer than MIN_SAMPLES samples) and blobs far from fly-sized
        if ntr[o["track"]] < MIN_SAMPLES:
            continue
        if area_med > 0 and not (0.35 * area_med <= o["area"] <= 3.0 * area_med):
            continue
        by_g[o["g"]].append(o)
    bind: dict[str, int] = {}
    assigned: dict[int, list[dict | None]] = {}
    slots = [Traj(), Traj()]
    for g in sorted(by_g):
        row: list[dict | None] = [None, None]
        pend = []
        # longest-lived tracks first: they win a slot over a short-lived track bound to the same slot
        for o in sorted(by_g[g], key=lambda o: (-ntr[o["track"]], -o["area"])):
            s = bind.get(o["track"])
            if s is not None and row[s] is None:
                row[s] = o
            elif s is None:
                pend.append(o)
        free = [s for s in (0, 1) if row[s] is None]
        # a free slot is only offered new tracks; never steal a slot from a live bound track
        if pend and free:
            pend = pend[:len(free)]
            C = np.zeros((len(pend), len(free)))
            for i, o in enumerate(pend):
                for j, s in enumerate(free):
                    p = slots[s].predict(g)
                    if p is not None:
                        C[i, j] = (np.hypot(p[0] - o["cx"], p[1] - o["cy"]) / bl
                                   + AREA_W * abs(p[2] - o["area"]) / max(p[2], o["area"], 1.0))
            for i, j in zip(*linear_sum_assignment(C), strict=True):
                row[free[j]] = pend[i]
                bind[pend[i]["track"]] = free[j]
        for s in (0, 1):
            if row[s] is not None:
                slots[s].add(g, row[s]["cx"], row[s]["cy"], row[s]["area"])
        assigned[g] = row
    return slots, assigned


def traj_from_rows(assigned: dict[int, list[dict | None]], slot: int) -> Traj:
    t = Traj()
    for g, row in assigned.items():
        o = row[slot]
        if o is not None:
            t.add(g, o["cx"], o["cy"], o["area"])
    return t


def _runs(flag: np.ndarray) -> list[tuple[int, int]]:
    runs, i, n = [], 0, len(flag)
    while i < n:
        if flag[i]:
            j = i
            while j + 1 < n and flag[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def relabel_swaps(assigned: dict[int, list[dict | None]], first: int, last: int, bl: float) -> list[int]:
    """After each close-contact run, test whether the paths fit better with the two slots exchanged;
    if so (by SWAP_MARGIN_BL per fly) exchange them for the rest of the chunk. Runs in time order, so a
    later run sees the labels as already corrected. Edits `assigned` in place; returns the global
    samples at which the labeling changed (each starts a new identity epoch)."""
    n = last - first
    a_xy = np.full((n, 2), np.nan)
    b_xy = np.full((n, 2), np.nan)
    for g, row in assigned.items():
        if row[0] is not None:
            a_xy[g - first] = (row[0]["cx"], row[0]["cy"])
        if row[1] is not None:
            b_xy[g - first] = (row[1]["cx"], row[1]["cy"])
    unk = unknown_mask(a_xy, b_xy, bl)
    bumps = []
    for i, j in _runs(unk):
        c = _run_costs(a_xy, b_xy, i, j)
        if c is None or j + 1 >= n:
            continue
        keep, swap = c
        if (keep - swap) / 2.0 > SWAP_MARGIN_BL * bl:
            a_xy[j + 1:], b_xy[j + 1:] = b_xy[j + 1:].copy(), a_xy[j + 1:].copy()
            for g in range(first + j + 1, last):
                if g in assigned:
                    assigned[g] = [assigned[g][1], assigned[g][0]]
            bumps.append(first + j + 1)
    return bumps


def unknown_mask(a_xy: np.ndarray, b_xy: np.ndarray, bl: float, thr: float = UNKNOWN_BL) -> np.ndarray:
    """True where the two centroids are within `thr` body lengths (identity not assignable).
    a_xy, b_xy: (n, 2) with NaN where absent (absent -> False: nothing to confuse)."""
    d = np.hypot(a_xy[:, 0] - b_xy[:, 0], a_xy[:, 1] - b_xy[:, 1])
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(d), d < thr * bl, False)


def _extrap(xy: np.ndarray, idx: np.ndarray, t: float) -> np.ndarray | None:
    """Linear fit of the valid points of xy (rows aligned with idx), evaluated at time t."""
    ok = np.isfinite(xy).all(1)
    if not ok.any():
        return None
    if ok.sum() == 1:
        return xy[ok][0]
    return np.array([np.polyval(np.polyfit(idx[ok], xy[ok, d], 1), t) for d in (0, 1)])


def _run_costs(a_xy: np.ndarray, b_xy: np.ndarray, i: int, j: int, k: int = 5) -> tuple[float, float] | None:
    """(keep, swap) fit cost of the run [i, j]: the paths before and after are extrapolated linearly to the
    middle of the run and compared as kept vs exchanged. None if either side has no data."""
    n = len(a_xy)
    mid = (i + j) / 2.0
    pre_i, post_i = np.arange(max(0, i - k), i), np.arange(j + 1, min(n, j + 1 + k))
    pts = [_extrap(a_xy[pre_i], pre_i, mid), _extrap(b_xy[pre_i], pre_i, mid),
           _extrap(a_xy[post_i], post_i, mid), _extrap(b_xy[post_i], post_i, mid)]
    if any(p is None for p in pts):
        return None
    a0, b0, a1, b1 = pts
    return (float(np.hypot(*(a1 - a0)) + np.hypot(*(b1 - b0))),
            float(np.hypot(*(b1 - a0)) + np.hypot(*(a1 - b0))))


def suspect_swaps(a_xy: np.ndarray, b_xy: np.ndarray, unknown: np.ndarray, k: int = 5) -> tuple[int, int]:
    """Diagnostic: (n close-contact runs, n runs where exchanging the post-run paths fits better at all)."""
    runs = _runs(unknown)
    sus = 0
    for i, j in runs:
        c = _run_costs(a_xy, b_xy, i, j, k)
        sus += int(c is not None and c[1] < c[0])
    return len(runs), sus
