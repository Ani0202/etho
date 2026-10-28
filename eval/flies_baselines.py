"""Simple feature-threshold baselines for Fly vs. Fly (EVAL_PLAN 14.12, report-only, never gates).

They read ONLY Etho's own per-sample fly features (`<run>/movieN/features.npz`, written by
etho/tracking/fly_features.py; FLY_FEATURE_DOC documents the columns), never the GT tracker. Thresholds are
chosen on dev movies 1-2 (mid5, 30 fps) by pooled pair-level frame F1 over a small fixed grid, then frozen in
eval/flies_baselines_v1.json and applied unchanged to spare, train and test features. Durations are in seconds,
so the same parameters apply at 10 fps.

- `lunge_speed`: a lunge onset where max_toward_speed >= T (px/s; NaN = invalid velocity = no detection) while
  the flies are within D body lengths (dist_bl); one bout of fixed length L s centred on each run's speed peak,
  with a refractory interval R s. Actor = the fly with the larger toward_speed at the peak.
- `wing_proxy`: frames where max_minor_ratio >= T (mask widened, e.g. wings out) while max_speed_s <= S (the
  flies are still; NaN speed counts as still) and Dlo <= dist_bl <= Dhi; runs shorter than M s are dropped.
  Actor = the fly with the larger minor_ratio.

CLI:
    uv run python -m eval.flies_baselines tune      # dev movies 1-2 only -> eval/flies_baselines_v1.json
    uv run python -m eval.flies_baselines predict --run-dir data/runs/F1 --split dev --window mid5 \\
        --out data/runs/F1_baselines
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

from eval import common
from eval import flies_extract as fx
from eval.score import prf

PARAMS_FILE = Path("eval/flies_baselines_v1.json")
GRID = {
    "lunge_speed": {"T": [10, 15, 20, 25, 30, 40, 50, 60, 80], "D": [1.0, 1.25, 1.5, 2.0], "L_s": [4 / 30],
                    "R_s": [0.3]},
    "wing_proxy": {"T": [1.15, 1.2, 1.25, 1.3, 1.35, 1.4], "S": [5, 10, 20, 40, float("inf")],
                   "Dlo": [0.0, 1.0, 1.5], "Dhi": [3.0, float("inf")], "M_s": [0.2, 0.5, 1.0]},
}
BEHAVIOR = {"lunge_speed": "lunge", "wing_proxy": "wing_threat"}


def load_features(run_dir: Path, movie: int, window: tuple[int, int], fps: float) -> dict:
    p = Path(run_dir) / f"movie{movie}" / "features.npz"
    f = dict(np.load(p))
    step = int(round(fx.NATIVE_FPS / fps))
    want = window[0] + step * np.arange(math.ceil((window[1] - window[0]) / step))
    if not np.array_equal(f["src_frame"], want):
        raise ValueError(f"{p}: src_frame does not match window {window} at {fps} fps")
    f["_sha256"] = common.sha256_file(p)
    return f


def _nan0(x):
    return np.nan_to_num(np.asarray(x, float), nan=0.0)


def lunge_speed(f: dict, fps: float, T: float, D: float, L_s: float, R_s: float) -> tuple[np.ndarray, np.ndarray]:
    sp = _nan0(f["max_toward_speed"])
    cand = (sp >= T) & (np.nan_to_num(f["dist_bl"], nan=np.inf) <= D)
    n = len(sp)
    pair = np.zeros(n, bool)
    by = np.zeros((2, n), bool)
    L, R = max(1, int(round(L_s * fps))), int(round(R_s * fps))
    last = -10**9
    for s, e in fx.runs(cand):
        pk = s + int(np.argmax(sp[s:e]))
        if pk - last < R:
            continue
        last = pk
        a, b = pk - L // 2, pk - L // 2 + L
        a, b = max(a, 0), min(b, n)
        pair[a:b] = True
        t = int(_nan0(f["b_toward_speed"])[pk] > _nan0(f["a_toward_speed"])[pk])
        by[t, a:b] = True
    return pair, by


def wing_proxy(f: dict, fps: float, T: float, S: float, Dlo: float, Dhi: float, M_s: float):
    still = np.nan_to_num(f["max_speed_s"], nan=0.0) <= S
    d = np.nan_to_num(f["dist_bl"], nan=-1.0)
    cand = (np.nan_to_num(f["max_minor_ratio"], nan=0.0) >= T) & still & (d >= Dlo) & (d <= Dhi)
    M = int(round(M_s * fps))
    pair = np.zeros(len(cand), bool)
    for s, e in fx.runs(cand):
        if e - s >= M:
            pair[s:e] = True
    t = (np.nan_to_num(f["b_minor_ratio"]) > np.nan_to_num(f["a_minor_ratio"])).astype(int)
    by = np.zeros((2, len(cand)), bool)
    by[0] = pair & (t == 0)
    by[1] = pair & (t == 1)
    return pair, by


FUNCS = {"lunge_speed": lunge_speed, "wing_proxy": wing_proxy}


def _pooled_f1(name, params, feats, labels, fps, step):
    c = np.zeros(3, np.int64)
    b = BEHAVIOR[name]
    for m, f in feats.items():
        pair, _ = FUNCS[name](f, fps, **params)
        full = np.repeat(pair, step)[: len(labels[m]["pair"][b])]  # 30 fps tune: step 1
        g, k = labels[m]["pair"][b], ~labels[m]["excluded"]
        c += (np.sum(g[k] & full[k]), np.sum(~g[k] & full[k]), np.sum(g[k] & ~full[k]))
    return prf(*c)[2]


def tune(run_dir: Path, out: Path = PARAMS_FILE, window: str = "mid5", fps: float = 30.0) -> dict:
    movies = fx.SPLITS["dev"]
    w = fx.WINDOWS[window]
    feats = {m: load_features(run_dir, m, w, fps) for m in movies}
    labels = {m: fx.load_window_labels(m, window) for m in movies}
    res = {"doc": __doc__.split("\n\n")[0], "tuned_on": {"split": "dev", "movies": list(movies), "window": window,
                                                         "sample_fps": fps, "objective": "pooled pair frame F1"},
           "features": {f"movie{m}": {"path": str(Path(run_dir) / f"movie{m}" / "features.npz"),
                                      "sha256": feats[m]["_sha256"]} for m in movies},
           "grid": GRID, "baselines": {}}
    for name, grid in GRID.items():
        best, best_p, n = -1.0, None, 0
        keys = list(grid)
        for vals in itertools.product(*(grid[k] for k in keys)):
            p = dict(zip(keys, vals, strict=True))
            n += 1
            f1 = _pooled_f1(name, p, feats, labels, fps, 1)
            if f1 > best:
                best, best_p = f1, p
        res["baselines"][name] = {"behavior": BEHAVIOR[name], "params": best_p, "dev_pooled_frame_f1": best,
                                  "n_grid_points": n}
    res["tuned_utc"] = common.now_utc()
    res.update({k: v for k, v in common.git_info().items() if k != "git_dirty_paths"})
    Path(out).write_text(json.dumps(res, indent=1, default=lambda x: "inf" if x == float("inf") else x) + "\n")
    common.append_run({"run_id": common.new_run_id("flies-baseline-tune"), "kind": "flies-baseline-tune",
                       "dataset": "flyvsfly", "split": "flies-dev", "purpose": "baseline-tune", "status": "ok",
                       "time_utc": common.now_utc(), **common.git_info(), "cost": {"usd": 0.0},
                       "labels_loaded": True, "movies": list(movies), "output": str(out),
                       "output_sha256": common.sha256_file(Path(out)),
                       "result": {k: {"params": v["params"], "dev_pooled_frame_f1": v["dev_pooled_frame_f1"]}
                                  for k, v in res["baselines"].items()}})
    return res


def _params(doc: dict, name: str) -> dict:
    return {k: (float("inf") if v == "inf" else v) for k, v in doc["baselines"][name]["params"].items()}


def predict(run_dir: Path, movies, window: str, out_root: Path, params_file: Path = PARAMS_FILE) -> dict:
    """Write one prediction directory per baseline (eval/flies_score.py format). Label-free."""
    doc = json.loads(Path(params_file).read_text())
    w = fx.WINDOWS[window]
    written = {}
    for name in GRID:
        out = Path(out_root) / name
        out.mkdir(parents=True, exist_ok=True)
        shas = {}
        for m in movies:
            tj = json.loads((Path(run_dir) / f"movie{m}" / "tracking.json").read_text())
            fps = float(tj["sample_fps"])
            f = load_features(run_dir, m, w, fps)
            pair, by = FUNCS[name](f, fps, **_params(doc, name))
            xy = np.stack([np.stack([f["a_cx"], f["a_cy"]], 1), np.stack([f["b_cx"], f["b_cy"]], 1)], 1)
            xy[~(np.asarray(f["a_present"]) > 0), 0] = np.nan
            xy[~(np.asarray(f["b_present"]) > 0), 1] = np.nan
            b = BEHAVIOR[name]
            d = {"window": np.array(w), "sample_fps": np.array(fps), "track_xy": xy,
                 "identity_unknown": np.asarray(f["id_unknown"]) > 0, "id_epoch": np.asarray(f["id_epoch"]),
                 b: pair.astype(np.uint8),
                 f"{b}_by_track": by.astype(np.uint8)}
            other = [x for x in fx.SCORED if x != b]
            for o in other:  # the scorer needs every scored behavior; a single-behavior baseline predicts none
                d[o] = np.zeros(len(pair), np.uint8)
            np.savez(out / f"movie{m}.npz", **d)
            shas[f"movie{m}"] = f["_sha256"]
        (out / "manifest.json").write_text(json.dumps({"config": {"baseline": name, "params": doc["baselines"][name]
                                                                  ["params"], "params_sha256":
                                                                  common.sha256_file(Path(params_file)),
                                                                  "features_sha256": shas},
                                                       "cost": {"usd": 0.0}, "notes": f"baseline {name}"}, indent=1))
        written[name] = str(out)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("tune", "predict"))
    ap.add_argument("--run-dir", type=Path, default=Path("data/runs/F1"))
    ap.add_argument("--split", default="dev")
    ap.add_argument("--window", default="mid5")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "tune":
        res = tune(a.run_dir, a.out or PARAMS_FILE, a.window)
        print(json.dumps({k: v for k, v in res["baselines"].items()}, indent=1, default=str))
        return 0
    print(json.dumps(predict(a.run_dir, fx.split_movies(a.split), a.window, a.out or Path("data/runs/F1_baselines")),
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
