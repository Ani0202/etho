"""Loads the showcase run's labels, bouts and overlay video (data/showcase/calms21-mouse075-live/), and the labels of
the Nov 2 live run (data/app_nov2/jobs/j46d77dd8aee9) for the results beat when video.common.SOURCE == "live"."""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np

from video.common import BEHAVIORS, LIVE_JOB, SHOWCASE, SOURCE


@lru_cache(maxsize=1)
def showcase() -> dict:
    r = json.loads((SHOWCASE / "result.json").read_text())
    z = np.load(SHOWCASE / "labels.npz", allow_pickle=True)
    names = r["behaviors"]["names"]
    assert tuple(names) == BEHAVIORS, names
    fps = float(r["fps"])
    labels = z["labels"]                   # code i = names[i], code len(names) = other
    # sample-index [start, end) per behavior; equals labels.npz (checked in tests)
    final = r["versions"][-1]["bouts"]
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


@lru_cache(maxsize=1)
def results_run() -> dict:
    """The run shown in the results beat: the live run's labels and bouts (SOURCE == "live") against the same clip's
    expert labels, which only the showcase package carries. SOURCE == "showcase" returns showcase()."""
    sc = showcase()
    if SOURCE != "live":
        return sc
    r = json.loads((LIVE_JOB / "result.json").read_text())
    names = r["behaviors"]["names"]
    assert tuple(names) == BEHAVIORS, names
    # the upload is the same 0-300 s clip under another file name (mouse075_0-300s_h264.mp4)
    assert int(r["n_samples"]) == sc["n"] and float(r["fps"]) == sc["fps"], "live run is not the showcase clip"
    z = np.load(LIVE_JOB / "labels.npz", allow_pickle=True)
    final = r["versions"][-1]["bouts"]
    return {**sc, "labels": z["labels"], "etho": {b: [list(x) for x in final[b]] for b in names},
            "overlay": LIVE_JOB / "overlay.mp4"}


def expert_codes(sc: dict) -> np.ndarray:
    """Per-sample expert code (0..2, 3 = other), mount > attack > investigation on overlap like the CalMS21 order."""
    out = np.full(sc["n"], len(BEHAVIORS), np.int8)
    for pri in ("investigation", "attack", "mount"):
        for s0, s1 in sc["expert"][pri]:
            out[s0:s1] = BEHAVIORS.index(pri)
    return out
