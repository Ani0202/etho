"""Loads the showcase run's labels, bouts and overlay video (data/showcase/calms21-mouse075-live/)."""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np

from video.common import BEHAVIORS, SHOWCASE


@lru_cache(maxsize=1)
def showcase() -> dict:
    r = json.loads((SHOWCASE / "result.json").read_text())
    z = np.load(SHOWCASE / "labels.npz", allow_pickle=True)
    names = r["behaviors"]["names"]
    assert tuple(names) == BEHAVIORS, names
    fps = float(r["fps"])
    labels = z["labels"]                   # code i = names[i], code len(names) = other
    final = r["versions"][-1]["bouts"]     # sample-index [start, end) per behavior; equals labels.npz (checked in tests)
    return {
        "fps": fps,
        "n": int(r["n_samples"]),
        "dur": r["n_samples"] / fps,
        "labels": labels,
        "etho": {b: [list(x) for x in final[b]] for b in names},
        "expert": {b: [list(x) for x in r["expert"]["bouts"][b]] for b in names},
        "overlay": SHOWCASE / "overlay.mp4",
        "clip": r["video"]["name"],
    }


def expert_codes(sc: dict) -> np.ndarray:
    """Per-sample expert code (0..2, 3 = other), mount > attack > investigation on overlap like the CalMS21 order."""
    out = np.full(sc["n"], len(BEHAVIORS), np.int8)
    for pri in ("investigation", "attack", "mount"):
        for s0, s1 in sc["expert"][pri]:
            out[s0:s1] = BEHAVIORS.index(pri)
    return out
