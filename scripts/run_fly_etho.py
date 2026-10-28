"""Etho on Fly vs. Fly: synthesize pair-level detectors, then predict (ADR-002; EVAL_PLAN section 14).

  ETHO_ROOT=/home/ubuntu/work/etho uv run python scripts/run_fly_etho.py synth --run F1 --movies 1 2 \\
      --variant physics --n-candidates 3 --out <detectors.json>
  ETHO_ROOT=/home/ubuntu/work/etho uv run python scripts/run_fly_etho.py predict --run F1 --movies 1 2 \\
      --detectors <detectors.json> --out <pred dir> [--override '{"lunge": {"post": {"min_s": 0.1}}}']
  uv run python -m eval.flies_score --split dev --window mid5 --pred-dir <pred dir> --root <data root>

`synth` reads the label-free feature statistics of the given dev movies and calls the gateway (Muse Spark
contributor, effort high, public data, tag ablation); `predict` is deterministic and makes no model call. Neither
reads a label or a .mat file. `predict` on movies other than the dev movies 1-2 needs --frozen-config (the gates and
the test run, started by the lead).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from etho.flies.extras import add_extras
from etho.flies.frozen import DECISION_FILE, check_frozen_run, run_branch
from etho.flies.predict import (
    REPO,
    WINDOWS,
    detect_pair,
    predict_movie,
    sha256_file,
    write_predictions,
)
from etho.flies.synth import HINTS, select_medoid, synthesize_candidate
from etho.tracking.fly_pipeline import make_gateway

DEV_MOVIES = (1, 2)
DEFS = Path("configs/flies_definitions.json")
MODEL = "muse-spark-1.3-contributor"  # public CC0 data


def root() -> Path:
    if not os.environ.get("ETHO_ROOT"):
        raise SystemExit("set ETHO_ROOT to the shared checkout (its ops/ ledger enforces the spend caps)")
    return Path(os.environ["ETHO_ROOT"]).resolve()


def load_table(run: str, m: int) -> tuple[dict[str, np.ndarray], float]:
    d = root() / "data/runs" / run / f"movie{m}"
    return dict(np.load(d / "features.npz")), float(json.loads((d / "tracking.json").read_text())["sample_fps"])


def cmd_synth(a) -> None:
    if any(m not in DEV_MOVIES for m in a.movies):
        raise SystemExit("synthesis statistics come from the dev movies 1-2 only")
    defs = json.loads(DEFS.read_text())
    loaded = [load_table(a.run, m) for m in a.movies]
    tables, fps = [t for t, _ in loaded], loaded[0][1]
    if a.extras:
        tables = [add_extras(t, fps) for t in tables]
    gw = make_gateway(root())
    behaviors = a.behaviors or list(defs["behaviors"])
    spec = {"version": 1, "name": a.name or Path(a.out).stem, "definitions": str(DEFS),
            "definitions_sha256": sha256_file(DEFS), "model": a.model, "effort": a.effort, "variant": a.variant,
            "public_data": True, "tag": a.tag, "extras": a.extras,
            "stats_from": {"run": a.run, "movies": a.movies, "labels": False}, "behaviors": {}}

    def one(job: tuple[str, int]):
        name, c = job
        cand = synthesize_candidate(name, defs["behaviors"][name], subject=defs["subject"], partner=defs["partner"],
                                    tables=tables, fps=fps, gw=gw, model=a.model, public_data=True, tag=a.tag,
                                    effort=a.effort, variant=a.variant, candidate=c, extras=a.extras)
        return cand, [detect_pair({"candidates": [vars(cand)], "use": [0]}, t, fps) for t in tables]

    jobs = [(name, c) for name in behaviors for c in range(a.n_candidates)]
    with ThreadPoolExecutor(min(len(jobs), a.workers)) as ex:  # candidates are independent calls
        done = list(ex.map(one, jobs))
    for name in behaviors:
        res = [r for (n, _), r in zip(jobs, done, strict=True) if n == name]
        cands, masks = [vars(c) for c, _ in res], [m for _, m in res]
        for i, (c, m) in enumerate(res):
            print(name, "candidate", i, "cost", round(sum(x["cost_usd"] for x in c.calls), 5),
                  "flagged frames", [int(x.sum()) for x in m], flush=True)
        best, agree = select_medoid(masks)
        spec["behaviors"][name] = {"candidates": cands, "use": [best], "min_votes": 1, "post": {},
                                   "selection": {"rule": "medoid by pooled frame-F1 agreement, label-free",
                                                 "agreement": agree, "chosen": best}}
        print(name, "medoid", best, "agreement", [round(x, 3) for x in agree], flush=True)
    spec["cost_usd"] = sum(x["cost_usd"] for b in spec["behaviors"].values() for c in b["candidates"]
                           for x in c["calls"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(spec, indent=1, default=float))
    print(f"wrote {a.out}  (${spec['cost_usd']:.4f})")


def cmd_predict(a) -> None:
    frozen = a.frozen_config is not None
    if not frozen and any(m not in DEV_MOVIES for m in a.movies):
        raise SystemExit("movies other than the dev movies 1-2 need --frozen-config (gate and test runs only)")
    spec = json.loads(Path(a.detectors).read_text())
    frozen_rec, window, want_fps = None, a.window or "mid5", None
    if frozen:  # the frozen pipeline only: same detectors file, no override, clean frozen trees, the tree's window
        fcfg = json.loads(a.frozen_config.read_text())
        frozen_rec = check_frozen_run(a.frozen_config, a.detectors, override=a.override)
        br = run_branch(fcfg, REPO / DECISION_FILE, a.window)
        window, want_fps = br["window"], float(br["sample_fps"])
        spec["behaviors"] = {b: v for b, v in spec["behaviors"].items() if b in br["behaviors"]}
        frozen_rec.update({"branch": br["branch"], "decision_record_sha256": br["decision_record_sha256"],
                           "behaviors": br["behaviors"]})
    override = json.loads(a.override) if a.override else {}
    for b, o in override.items():
        spec["behaviors"][b] = {**spec["behaviors"][b], **o}
    post = {b: v.get("post") for b, v in spec["behaviors"].items()}
    t0 = time.monotonic()
    per_movie, inputs = {}, {}
    for m in a.movies:
        table, fps = load_table(a.run, m)
        if want_fps is not None and fps != want_fps:
            raise SystemExit(f"frozen run refused: movie{m} was tracked at {fps:g} fps, branch {frozen_rec['branch']} "
                             f"needs {want_fps:g}")
        d = root() / "data/runs" / a.run / f"movie{m}"
        inputs[f"movie{m}"] = {"features_sha256": sha256_file(d / "features.npz"),
                               "tracking_sha256": sha256_file(d / "tracking.json"),
                               "features_path": str(d / "features.npz"), "tracking_path": str(d / "tracking.json")}
        per_movie[m] = predict_movie(table, spec, window, fps)
        print(f"movie{m}", {b: int(per_movie[m][b].sum()) for b in spec["behaviors"]}, flush=True)
    cfg = {"pipeline": "etho.flies", "detectors": str(a.detectors), "detectors_sha256": sha256_file(a.detectors),
           "override": override, "post": post, "window": list(WINDOWS[window]), "window_name": window,
           "tracking_run": a.run, "inputs": inputs, "frozen_run": frozen_rec, "variant": spec.get("variant"),
           "model": spec.get("model"), "effort": spec.get("effort"),
           "use": {b: v["use"] for b, v in spec["behaviors"].items()},
           "min_votes": {b: v.get("min_votes", 1) for b, v in spec["behaviors"].items()}}
    write_predictions(a.out, per_movie, config=cfg, cost_usd=0.0, wall_clock_s=time.monotonic() - t0, calls=0,
                      notes=a.notes, frozen_config=a.frozen_config)
    print(f"wrote {a.out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["synth", "predict"])
    ap.add_argument("--run", default="F1")
    ap.add_argument("--movies", type=int, nargs="+", default=list(DEV_MOVIES))
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--name")
    ap.add_argument("--behaviors", nargs="+")
    ap.add_argument("--variant", default="plain", choices=sorted(HINTS))
    ap.add_argument("--extras", action="store_true", help="give the detectors the unmasked *_raw motion columns")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--n-candidates", type=int, default=3)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--effort", default="high")
    ap.add_argument("--tag", default="ablation")
    ap.add_argument("--detectors", type=Path)
    ap.add_argument("--window", choices=sorted(WINDOWS), help="free runs: default mid5; frozen runs take "
                    "the window from the frozen tree and refuse a different one")
    ap.add_argument("--override", help="JSON {behavior: {use, min_votes, post}} applied on top of the spec")
    ap.add_argument("--frozen-config", type=Path)
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    if a.cmd == "synth":
        cmd_synth(a)
    else:
        if a.detectors is None:
            raise SystemExit("predict needs --detectors")
        cmd_predict(a)


if __name__ == "__main__":
    main()
