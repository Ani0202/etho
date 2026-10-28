"""Label-free ensembling: K independent detector syntheses per behavior, per-sample majority vote.
  uv run python scripts/ensemble.py synth --run R1 --k 5
  uv run python scripts/ensemble.py predict --run R1 --split dev_holdout [--k 5]"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from etho.gateway import Gateway
from etho.rules.sandbox import run_detector
from etho.rules.synthesize import synthesize

CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}
PRIORITY = ("mount", "attack", "investigation")
SPLITS = json.loads(Path("eval/splits/calms21_v1.json").read_text())
ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["synth", "predict"])
ap.add_argument("--run", required=True)
ap.add_argument("--split", default="dev")
ap.add_argument("--k", type=int, default=5)
ap.add_argument("--model", default="muse-spark-1.3-contributor")
a = ap.parse_args()
run = Path("data/runs") / a.run
path = run / f"detectors_ens{a.k}.json"
if a.cmd == "synth":
    defs = json.loads(Path("configs/calms21_definitions.json").read_text())
    tables = [dict(np.load(run / s / "features.npz")) for s in SPLITS["dev"]]
    gw = Gateway()
    jobs = [(b, d, c) for b, d in defs["behaviors"].items() for c in range(1, a.k + 1)]

    def one(j):
        b, d, c = j
        return synthesize(b, d, subject=defs["subject"], partner=defs["partner"], tables=tables, fps=10.0,
                          frame_wh=(1024, 570), gw=gw, model=a.model, public_data=True, candidate=c)
    with ThreadPoolExecutor(5) as ex:
        dets = list(ex.map(one, jobs))
    path.write_text(json.dumps({"k": a.k, "model": a.model, "detectors": [vars(d) for d in dets]}, indent=1,
                               default=float))
    print(path, f"${sum(c['cost_usd'] for d in dets for c in d.calls):.3f}")
else:
    spec = json.loads(path.read_text())
    out = run / f"pred_{a.split}_ens{a.k}"
    out.mkdir(exist_ok=True)
    for s in SPLITS[a.split]:
        t = dict(np.load(run / s / "features.npz"))
        votes = {}
        for d in spec["detectors"]:
            votes.setdefault(d["behavior"], []).append(run_detector(d["code"], t, 10.0))
        masks = {b: np.mean(v, axis=0) > 0.5 for b, v in votes.items()}
        lab = np.full(len(next(iter(t.values()))), CODES["other"], np.int8)
        for b in reversed(PRIORITY):
            lab[masks[b]] = CODES[b]
        np.savez_compressed(out / f"{s}.npz", labels=lab, sample_fps=np.float64(10), sample_stride=np.int64(3))
    (out / "manifest.json").write_text(json.dumps({"sample_fps": 10, "detectors": str(path), "vote": "majority"}))
    print(out)
