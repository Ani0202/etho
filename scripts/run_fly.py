"""Track Fly vs. Fly (Aggression) movies with SAM 3.1 -> features (ADR-002). Tracking only.

  ETHO_ROOT=/home/ubuntu/work/etho uv run python scripts/run_fly.py track --movies 1 2 --run F1
  ETHO_ROOT=/home/ubuntu/work/etho uv run python scripts/run_fly.py rederive --movies 1 2 --run F1   # $0

The root is taken ONLY from ETHO_ROOT (the shared checkout): its ops/spend.jsonl ledger and caps pay for
every SAM call, so a worktree can never write its own ledger. `track` prints a job estimate first and
refuses anything over $5 without --i-have-approval (CLAUDE.md: any single job over $5 needs approval).
Reads only data/flyvsfly/Aggression/movie<N>/movie<N>.mp4. Outputs under <root>/data/runs/<run>/movie<N>/.
Default window: the middle 5 min, source frames 22500-31499, 30 fps, native 144 px.
Overlap: 1 s by default so a re-run of dev movies 1-2 hits the gateway cache; use --overlap-s 2 for new
runs (SAM needs up to ~25 samples at a chunk start to find the flies; 1 s only just covers it).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from etho.tracking.fly_pipeline import PHRASE, estimate_sam_cost, make_gateway, rederive_fly_run, track_fly_movie

DEV_MOVIES = (1, 2, 3, 4, 5)
JOB_LIMIT_USD = 5.0


def summary(m: int, n: int, cost: float, timings: dict | None, i: dict) -> dict:
    return {"movie": m, "samples": n, "cost_usd": round(cost, 4),
            "wall_s": round(timings["wall_s"], 1) if timings else None,
            "x_realtime": round(timings["x_realtime"], 3) if timings else None,
            "frac_both_present": round(i["frac_both_present"], 4),
            "frac_id_unknown_of_both": round(i["frac_id_unknown_of_both"], 4),
            "n_epochs": i["n_epochs"], "relabels": len(i["relabels_at"]), "close_runs": i["close_runs"],
            "suspect_swaps_left": i["suspect_swaps_left"], "frac_vel_ok": round(i["frac_vel_ok"], 4),
            "frac_vel_ok_s": round(i["frac_vel_ok_s"], 4), "body_len_px": round(i["body_len_px"], 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["track", "rederive"])
    ap.add_argument("--movies", type=int, nargs="+", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--start", type=int, default=22500)
    ap.add_argument("--stop", type=int, default=31500, help="exclusive")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--overlap-s", type=float, default=1.0)
    ap.add_argument("--phrase", default=PHRASE)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--allow-test", action="store_true", help="movies 6-10 (test split): not for dev runs")
    ap.add_argument("--i-have-approval", action="store_true", help="allow a job estimated over $5")
    ap.add_argument("--tag", default="ablation")
    ap.add_argument("--branch", choices=["GO", "NO-GO"],
                    help="take window, stride, phrase, overlap, scale and workers from the frozen tracking config")
    ap.add_argument("--tracking-config", type=Path,
                    default=Path(__file__).resolve().parents[1] / "configs/flies_tracking.json")
    a = ap.parse_args()
    if a.branch:  # the frozen pipeline's tracking parameters (configs/flies_tracking.json), not the flags above
        tc = json.loads(a.tracking_config.read_text())
        br = tc["branches"][a.branch]
        a.start, a.stop, a.stride = br["start"], br["stop"], br["stride"]
        a.phrase, a.overlap_s, a.scale = tc["phrase"], tc["overlap_s"], tc["scale"]
        a.workers, a.tag = tc["workers"], tc["tag"]
        print(f"tracking config {a.tracking_config.name}, branch {a.branch}: frames [{a.start}, {a.stop}) stride "
              f"{a.stride}, phrase {a.phrase!r}, overlap {a.overlap_s} s", flush=True)
    if not a.allow_test and any(m not in DEV_MOVIES for m in a.movies):
        raise SystemExit("movies 6-10 are the test split; pass --allow-test only for the frozen test run")
    if not os.environ.get("ETHO_ROOT"):
        raise SystemExit("set ETHO_ROOT to the shared checkout (its ops/ ledger enforces the spend caps)")
    root = Path(os.environ["ETHO_ROOT"]).resolve()
    for m in a.movies:
        out = root / "data/runs" / a.run / f"movie{m}"
        if a.cmd == "rederive":
            r = rederive_fly_run(out)
            print(json.dumps(summary(m, len(r["table"]["a_cx"]), 0.0, None, r["info"])), flush=True)
    if a.cmd == "rederive":
        return
    frames, usd = estimate_sam_cost(a.start, a.stop, a.stride, overlap_s=a.overlap_s)
    total = usd * len(a.movies)
    print(f"estimate: {len(a.movies)} movie(s) x {frames} frames = ${total:.2f} (upper bound; cached chunks are free)",
          flush=True)
    if total > JOB_LIMIT_USD and not a.i_have_approval:
        raise SystemExit(f"estimated ${total:.2f} > ${JOB_LIMIT_USD:.0f}: needs approval (--i-have-approval)")
    gw = make_gateway(root)
    for m in a.movies:
        src = root / "data/flyvsfly/Aggression" / f"movie{m}" / f"movie{m}.mp4"
        # Fly vs. Fly Aggression is a public CC0 dataset, so contributor model IDs are allowed.
        run = track_fly_movie(src, root / "data/runs" / a.run / f"movie{m}", gw=gw, public_data=True,
                              start=a.start, stop=a.stop, stride=a.stride, scale=a.scale, phrase=a.phrase,
                              tag=a.tag, workers=a.workers, overlap_s=a.overlap_s, chunk_dir=root / "cache/fly_chunks")
        print(json.dumps(summary(m, run["n_samples"], run["cost_usd"], run["timings"], run["info"])), flush=True)


if __name__ == "__main__":
    main()
