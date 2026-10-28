"""Track Fly vs. Fly (Aggression) movies with SAM 3.1 -> features (ADR-002). Tracking only.

  ETHO_ROOT=/home/ubuntu/work/etho uv run python scripts/run_fly.py track --movies 1 2 --run F1

Reads only data/flyvsfly/Aggression/movie<N>/movie<N>.mp4 (never *_actions/_track/_feat.mat).
Outputs: <root>/data/runs/<run>/movie<N>/{features.npz,tracking.json,tracks.npz,raw_c###.jsonl.gz}.
Default window: the middle 5 min, source frames 22500-31499, at 30 fps, native 144 px.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from etho.gateway import Gateway
from etho.tracking.fly_pipeline import PHRASE, track_fly_movie

DEV_MOVIES = (1, 2, 3, 4, 5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["track"])
    ap.add_argument("--movies", type=int, nargs="+", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("ETHO_ROOT", ".")))
    ap.add_argument("--start", type=int, default=22500)
    ap.add_argument("--stop", type=int, default=31500, help="exclusive")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--phrase", default=PHRASE)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--allow-test", action="store_true", help="movies 6-10 (test split): not for dev runs")
    ap.add_argument("--tag", default="ablation")
    a = ap.parse_args()
    if not a.allow_test and any(m not in DEV_MOVIES for m in a.movies):
        raise SystemExit("movies 6-10 are the test split; pass --allow-test only for the frozen test run")
    gw = Gateway()
    for m in a.movies:
        src = a.root / "data/flyvsfly/Aggression" / f"movie{m}" / f"movie{m}.mp4"
        run = track_fly_movie(src, a.root / "data/runs" / a.run / f"movie{m}", gw=gw, start=a.start, stop=a.stop,
                              stride=a.stride, scale=a.scale, phrase=a.phrase, tag=a.tag, workers=a.workers,
                              chunk_dir=a.root / "cache/fly_chunks")
        i = run["info"]
        print(json.dumps({"movie": m, "samples": run["n_samples"], "cost_usd": round(run["cost_usd"], 4),
                          "wall_s": round(run["timings"]["wall_s"], 1),
                          "x_realtime": round(run["timings"]["x_realtime"], 3),
                          "frac_both_present": round(i["frac_both_present"], 4),
                          "frac_id_unknown_of_both": round(i["frac_id_unknown_of_both"], 4),
                          "n_epochs": i["n_epochs"], "suspect_swaps": f"{i['suspect_swaps']}/{i['close_runs']}",
                          "frac_n_objects_gt2": round(i["frac_n_objects_gt2"], 4),
                          "body_len_px": round(i["body_len_px"], 1)}), flush=True)


if __name__ == "__main__":
    main()
