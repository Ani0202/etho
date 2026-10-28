"""Run Etho on a CalMS21 split: track -> synthesize detectors -> predict -> (score with eval/).

  uv run python scripts/run_pipeline.py track   --split dev --run R1
  uv run python scripts/run_pipeline.py synth   --split dev --run R1 [--model muse-spark-1.3-contributor]
  uv run python scripts/run_pipeline.py predict --split dev --run R1 --detectors data/runs/R1/detectors.json
  uv run python -m eval.score --split dev --pred-dir data/runs/R1/pred

Product code never reads labels: this script only touches videos and features. Scoring is eval/'s job.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from etho.gateway import Gateway
from etho.rules.sandbox import run_detector
from etho.rules.synthesize import Detector, synthesize
from etho.tracking.pipeline import track_video

SPLITS = json.loads(Path("eval/splits/calms21_v1.json").read_text())
PRIORITY = ("mount", "attack", "investigation")  # overlap resolution, same default as eval/score.py
CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}


def seqs_for(split: str) -> list[str]:
    return SPLITS[split]


def video_for(seq: str, split: str) -> Path:
    sub = "test" if split == "test" else "train"
    return Path(f"data/calms21/videos/{sub}/{seq}.mp4")


def cmd_track(a) -> None:
    gw = Gateway()
    out = Path("data/runs") / a.run
    rows = []
    for seq in seqs_for(a.split):
        t0 = time.monotonic()
        r = track_video(video_for(seq, a.split), out / seq, gw=gw, public_data=True, workers=a.workers,
                        tag=a.tag)
        rows.append({"seq": seq, **r.timings, "cost_usd": r.cost_usd, "outer_wall_s": time.monotonic() - t0})
        print(json.dumps(rows[-1], default=float), flush=True)
    tot_v = sum(r["video_s"] for r in rows)
    tot_w = sum(r["wall_s"] for r in rows)
    summary = {"split": a.split, "videos": len(rows), "video_s": tot_v, "wall_s": tot_w,
               "x_realtime": tot_w / tot_v, "cost_usd": sum(r["cost_usd"] for r in rows), "per_video": rows}
    (out / f"track_{a.split}.json").write_text(json.dumps(summary, indent=1, default=float))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_video"}, default=float))


def load_tables(run: str, split: str) -> dict[str, dict[str, np.ndarray]]:
    return {s: dict(np.load(Path("data/runs") / run / s / "features.npz")) for s in seqs_for(split)}


def cmd_synth(a) -> None:
    defs = json.loads(Path(a.definitions).read_text())
    tables = list(load_tables(a.run, a.split).values())
    gw = Gateway()
    dets = []
    for name, definition in defs["behaviors"].items():
        d = synthesize(name, definition, subject=defs["subject"], partner=defs["partner"], tables=tables, fps=10.0,
                       frame_wh=(1024, 570), gw=gw, model=a.model, public_data=True, tag="eval-llm",
                       effort=a.effort)
        dets.append(d)
        print(name, "ok", [c["cost_usd"] for c in d.calls], flush=True)
    path = Path("data/runs") / a.run / "detectors.json"
    path.write_text(json.dumps({"definitions": a.definitions, "model": a.model, "effort": a.effort,
                                "stats_from_split": a.split, "detectors": [vars(d) for d in dets]},
                               indent=1, default=float))
    print(path)


def cmd_predict(a) -> None:
    spec = json.loads(Path(a.detectors).read_text())
    dets = [Detector(**d) for d in spec["detectors"]]
    out = Path("data/runs") / a.run / f"pred_{a.split}"
    out.mkdir(parents=True, exist_ok=True)
    t_all = 0.0
    for seq, t in load_tables(a.run, a.split).items():
        t0 = time.monotonic()
        masks = {d.behavior: run_detector(d.code, t, 10.0) for d in dets}
        n = len(next(iter(t.values())))
        lab = np.full(n, CODES["other"], np.int8)
        for b in reversed(PRIORITY):  # lowest priority first, so higher ones overwrite
            if b in masks:
                lab[masks[b]] = CODES[b]
        np.savez_compressed(out / f"{seq}.npz", labels=lab, sample_fps=np.float64(10.0), sample_stride=np.int64(3))
        t_all += time.monotonic() - t0
        print(seq, {b: int(m.sum()) for b, m in masks.items()}, flush=True)
    (out / "manifest.json").write_text(json.dumps({"sample_fps": 10, "detectors": a.detectors,
                                                   "predict_wall_s": t_all}, indent=1))
    print(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["track", "synth", "predict"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--run", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="eval-sam")
    ap.add_argument("--definitions", default="configs/calms21_definitions.json")
    ap.add_argument("--model", default="muse-spark-1.3-contributor")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--detectors")
    a = ap.parse_args()
    {"track": cmd_track, "synth": cmd_synth, "predict": cmd_predict}[a.cmd](a)


if __name__ == "__main__":
    main()
