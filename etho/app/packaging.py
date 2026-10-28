"""Shared by the showcase packaging scripts (scripts/make_showcase.py, scripts/package_job_showcase.py).

The expert row is display-only: a showcase may carry the dataset's own annotation, drawn under Etho's row so a
viewer can compare. The app never reads labels itself; the packaging script is handed a labels file explicitly."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from . import behaviors as B

CALMS21 = {"name": "CalMS21 (task 1)", "license": "CC BY 4.0", "url": "https://data.caltech.edu/records/s0vdx-0k302"}


SPLITS = Path(__file__).resolve().parents[2] / "eval" / "splits" / "calms21_v1.json"


def expert_split(seq: str, splits: Path | None = None, allow_test: bool = False) -> str:
    """"dev" or "test" for a sequence allowed to carry an expert row; exits with a plain message otherwise. The splits
    file is only read. A test-split sequence needs allow_test (the lead's decision: the real annotation timeline of
    the demo test clip may be shown; CalMS21 test stays final)."""
    d = json.loads((splits or SPLITS).read_text())
    if seq in d.get("dev", []):
        return "dev"
    if seq in d.get("test", []):
        if not allow_test:
            sys.exit(f"{seq} is in the TEST split: pass --allow-test-split to show its annotation (display only)")
        print("!" * 70 + f"\nNOTE: {seq} is a TEST-split sequence. Its expert row is display only, for the demo; "
              "it must never be used to tune anything.\n" + "!" * 70, flush=True)
        return "test"
    sys.exit(f"{seq} is in neither the dev nor the test split of {(splits or SPLITS).name}; no expert row")


def expert_record(npz: Path, names: list[str], n: int, fps: float, start_s: float = 0.0, *, seq: str,
                  splits: Path | None = None, allow_test: bool = False, label: str = "Expert (CalMS21)") -> dict:
    """The `expert` entry of result.json from a labels .npz (key `labels`, one int code per 10 fps sample, codes in
    `names` order with "other" last, the same coding as the app's own labels). `start_s` slices a longer sequence
    down to a trimmed clip. Codes outside 0..len(names) are an error; per-code counts are printed."""
    split = expert_split(seq, splits, allow_test)
    lab = np.load(npz)["labels"]
    i0 = int(round(start_s * fps))
    lab = lab[i0:i0 + n]
    if len(lab) != n:
        sys.exit(f"expert labels cover {len(lab)} samples from {start_s} s, the video has {n}")
    if lab.min() < 0 or lab.max() > len(names) or not np.issubdtype(lab.dtype, np.integer):
        sys.exit(f"expert label codes must be integers in 0..{len(names)} ({', '.join(names)}, other); "
                 f"found {lab.min()}..{lab.max()}")
    counts = {(names[c] if c < len(names) else "other"): int((lab == c).sum()) for c in range(len(names) + 1)}
    print(f"expert labels for {seq} ({split} split), samples per code: {counts}", flush=True)
    return {"label": label, "bouts": B.bouts_by_behavior(lab, names), "source": str(Path(npz).name),
            "sequence": seq, "split": split,
            "note": "The dataset's own annotation, shown for comparison only; Etho never saw it."}
