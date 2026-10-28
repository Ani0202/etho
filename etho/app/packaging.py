"""Shared by the showcase packaging scripts (scripts/make_showcase.py, scripts/package_job_showcase.py).

The expert row is display-only: a showcase may carry the dataset's own annotation, drawn under Etho's row so a
viewer can compare. The app never reads labels itself; the packaging script is handed a labels file explicitly."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import behaviors as B

CALMS21 = {"name": "CalMS21 (task 1)", "license": "CC BY 4.0", "url": "https://data.caltech.edu/records/s0vdx-0k302"}


def expert_record(npz: Path, names: list[str], n: int, fps: float, start_s: float = 0.0,
                  label: str = "Expert (CalMS21)") -> dict:
    """The `expert` entry of result.json from a labels .npz (key `labels`, one int code per 10 fps sample, codes in
    `names` order with "other" last, the same coding as the app's own labels). `start_s` slices a longer sequence
    down to a trimmed clip."""
    lab = np.load(npz)["labels"]
    i0 = int(round(start_s * fps))
    lab = lab[i0:i0 + n]
    if len(lab) != n:
        raise ValueError(f"expert labels cover {len(lab)} samples from {start_s} s, the video has {n}")
    return {"label": label, "bouts": B.bouts_by_behavior(lab, names), "source": str(Path(npz).name),
            "note": "The dataset's own annotation, shown for comparison only; Etho never saw it."}
