"""Turn raw SAM object tracks into animal identities, with no labels (ADR-001).

SAM's "mouse" concept returns the two animals plus extras: a head implant/cable connector
(dark, attached to the resident), wall reflections, frame-edge blobs. Rules:
  1. Split tracks into dark/bright by the midpoint between the darkest and brightest
     persistent tracks' median mask intensity (per video).
  2. In each class the primary track is the one with the largest presence x median area: that
     is the animal (resident = dark, intruder = bright for CalMS21's black/white pair).
  3. A secondary track whose box overlaps its class primary's box in >= 50% of shared frames is a
     *part* of that animal (e.g. the implant: a free head cue). Anything else is "unknown".
  4. Per frame, a primary whose own intensity sits on the other side of the midpoint for >= 5
     consecutive samples is relabeled for that run (guards against a silent SAM ID swap).
If the intensity gap between the two primaries is small, identities fall back to SAM track
persistence and the result is flagged (`identity_by="persistence"`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

MIN_PRESENCE = 0.05       # fraction of samples a track must appear in to be considered
MIN_INTENSITY_GAP = 30.0  # 0-255 gray levels between the two animals' medians
RELABEL_RUN = 5


@dataclass
class TrackSummary:
    track: str
    n: int
    presence: float
    median_area: float
    median_intensity: float
    role: str = "unknown"        # resident | intruder | resident:part | intruder:part | unknown


@dataclass
class IdentityResult:
    roles: dict[str, str]
    midpoint: float | None
    identity_by: str             # "intensity" | "persistence"
    summaries: list[TrackSummary]
    relabeled: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)  # track -> [(f0, f1, role)]


def _box_overlap(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def assign_identities(objs: list[dict], n_samples: int, dark: str = "resident",
                      bright: str = "intruder") -> IdentityResult:
    """objs: dicts with keys frame, track, area, intensity, box (l, t, r, b)."""
    by_track: dict[str, list[dict]] = defaultdict(list)
    for o in objs:
        by_track[o["track"]].append(o)
    sums = [TrackSummary(t, len(v), len({o["frame"] for o in v}) / max(n_samples, 1),
                         float(np.median([o["area"] for o in v])),
                         float(np.nanmedian([o["intensity"] for o in v])))
            for t, v in by_track.items()]
    cand = [s for s in sums if s.presence >= MIN_PRESENCE]
    roles = {s.track: "unknown" for s in sums}
    if not cand:
        return IdentityResult(roles, None, "none", sums)
    lo, hi = min(s.median_intensity for s in cand), max(s.median_intensity for s in cand)
    mid = (lo + hi) / 2
    dark_c = [s for s in cand if s.median_intensity < mid]
    bright_c = [s for s in cand if s.median_intensity >= mid]
    score = lambda s: s.presence * s.median_area  # noqa: E731
    prim = {}
    if dark_c:
        prim[dark] = max(dark_c, key=score)
    if bright_c:
        prim[bright] = max(bright_c, key=score)
    identity_by = "intensity"
    if len(prim) == 2 and prim[bright].median_intensity - prim[dark].median_intensity < MIN_INTENSITY_GAP:
        identity_by = "persistence"  # animals look alike: keep the two most persistent tracks, roles arbitrary
    if len(prim) < 2:  # only one class: take the two most persistent tracks
        top = sorted(cand, key=score, reverse=True)[:2]
        prim = {dark: top[0]} | ({bright: top[1]} if len(top) > 1 else {})
        identity_by = "persistence"
    for role, s in prim.items():
        roles[s.track] = role
    # Parts: secondary tracks attached to a primary of their class.
    boxes = {t: {o["frame"]: o["box"] for o in v} for t, v in by_track.items()}
    for s in cand:
        if roles[s.track] != "unknown":
            continue
        cls = dark if s.median_intensity < mid else bright
        p = prim.get(cls)
        if p is None:
            continue
        shared = [f for f in boxes[s.track] if f in boxes[p.track]]
        if shared and sum(_box_overlap(boxes[s.track][f], boxes[p.track][f]) for f in shared) / len(shared) >= 0.5:
            roles[s.track] = f"{cls}:part"
    for s in sums:
        s.role = roles[s.track]
    res = IdentityResult(roles, mid, identity_by, sums)
    if identity_by == "intensity":
        for role, s in prim.items():
            seq = sorted(by_track[s.track], key=lambda o: o["frame"])
            wrong = [(o["frame"], (o["intensity"] < mid) != (role == dark)) for o in seq]
            runs, start = [], None
            for i, (_f, w) in enumerate(wrong):
                if w and start is None:
                    start = i
                if (not w or i == len(wrong) - 1) and start is not None:
                    end = i if w else i - 1
                    if end - start + 1 >= RELABEL_RUN:
                        other = bright if role == dark else dark
                        runs.append((wrong[start][0], wrong[end][0], other))
                    start = None
            if runs:
                res.relabeled[s.track] = runs
    return res


def role_at(res: IdentityResult, track: str, frame: int) -> str:
    for f0, f1, role in res.relabeled.get(track, []):
        if f0 <= frame <= f1:
            return role
    return res.roles.get(track, "unknown")
