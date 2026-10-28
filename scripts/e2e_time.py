"""Uncached end-to-end wall-clock for one video: chunking -> SAM (parallel) -> decode/features -> rules.
Usage: uv run python scripts/e2e_time.py <seq> <detectors.json>   (seq from the unused CalMS21 train pool)"""
import json
import sys
import time
from pathlib import Path

from etho.datasets.remote_zip import extract, open_remote_zip
from etho.rules.sandbox import run_detector
from etho.tracking.pipeline import track_video

seq, det_path = sys.argv[1], Path(sys.argv[2])
src = Path(f"data/calms21/videos/train/{seq}.mp4")
if not src.exists():
    zf = open_remote_zip("https://data.caltech.edu/records/s0vdx-0k302/files/task1_videos_mp4.zip?download=1")
    extract(zf, f"task1_videos_mp4/train/{seq}.mp4", src)
dets = json.loads(det_path.read_text())["detectors"]
t0 = time.monotonic()
run = track_video(src, Path(f"data/runs/e2e/{seq}"), public_data=True, workers=8, tag="eval-sam")
t1 = time.monotonic()
masks = {d["behavior"]: run_detector(d["code"], run.table, 10.0) for d in dets}
t2 = time.monotonic()
out = {"seq": seq, "video_s": run.n_samples / 10, **{f"track_{k}": v for k, v in run.timings.items()},
       "rules_s": t2 - t1, "end_to_end_s": t2 - t0, "x_realtime": (t2 - t0) / (run.n_samples / 10),
       "sam_cost_usd": run.cost_usd, "predicted_samples": {b: int(m.sum()) for b, m in masks.items()}}
Path(f"data/runs/e2e/{seq}/e2e.json").write_text(json.dumps(out, indent=1, default=float))
print(json.dumps(out, indent=1, default=float))
