"""Joint classifier variant: synthesize one classify(t, fps) for all behaviors, then predict a split.
  uv run python scripts/joint.py synth --run R1
  uv run python scripts/joint.py predict --run R1 --split dev_holdout"""
import argparse
import json
from pathlib import Path

import numpy as np

from etho.gateway import Gateway
from etho.rules.sandbox import run_detector
from etho.rules.synthesize import synthesize_joint

CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}
SPLITS = json.loads(Path("eval/splits/calms21_v1.json").read_text())
ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["synth", "predict"])
ap.add_argument("--run", required=True)
ap.add_argument("--split", default="dev")
ap.add_argument("--model", default="muse-spark-1.3-contributor")
a = ap.parse_args()
run = Path("data/runs") / a.run
if a.cmd == "synth":
    defs = json.loads(Path("configs/calms21_definitions.json").read_text())
    tables = [dict(np.load(run / s / "features.npz")) for s in SPLITS["dev"]]
    det = synthesize_joint(defs["behaviors"], CODES, subject=defs["subject"], partner=defs["partner"], tables=tables,
                           fps=10.0, frame_wh=(1024, 570), gw=Gateway(), model=a.model, public_data=True)
    (run / "detector_joint.json").write_text(json.dumps(vars(det), indent=1, default=float))
    print("joint ok", [c["cost_usd"] for c in det.calls])
else:
    det = json.loads((run / "detector_joint.json").read_text())
    out = run / f"pred_{a.split}_joint"
    out.mkdir(exist_ok=True)
    for s in SPLITS[a.split]:
        lab = run_detector(det["code"], dict(np.load(run / s / "features.npz")), 10.0, labels=4)
        np.savez_compressed(out / f"{s}.npz", labels=lab, sample_fps=np.float64(10), sample_stride=np.int64(3))
        print(s, np.bincount(lab, minlength=4).tolist())
    (out / "manifest.json").write_text(json.dumps({"sample_fps": 10, "detector": "detector_joint.json"}))
