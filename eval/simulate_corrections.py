"""Simulated user for the correction loop (docs/EVAL_PLAN.md section 6a). No model calls.

A real user looks at Etho's ethogram, finds a few mistakes, and fixes them. This script plays that user
reproducibly from the **dev** split's expert labels, so "accuracy with N corrections" can be measured.
It reads labels (so it lives in eval/) and never imports product code (etho/).

Protocol (all at the prediction sample grid, default 10 fps; sample k = source frame step*k, step = 30/fps):
  1. Ground truth per sample = the 30 fps label at frame step*k. Predictions are loaded with the scorer's own
     loader (eval.score.load_prediction: per-sample npz or 30 fps bouts jsonl) and read at the same frames.
  2. Bouts are maximal runs of one behavior (no gap merging, no minimum length unless --min-bout-samples).
     overlap(bout) = fraction of the bout's samples where the *other* side has the same behavior.
       FP bout of b: a predicted bout of b with overlap(GT == b) < 0.2.
       FN bout of b: a GT bout of b with overlap(pred == b) < 0.2.
  3. For each (behavior, kind) stratum, candidates are pooled over all dev sequences (split-file order, then
     start sample) and K are drawn uniformly **without replacement** with
     numpy.random.default_rng([seed, behavior_index, kind_index]) (behavior_index in attack, investigation,
     mount; kind_index fp 0, fn 1), so each stratum's draw is independent of the others. Fewer than K
     candidates: all are taken and the shortfall is logged.
  4. A bout longer than the window cap (3 s = 30 samples at 10 fps) is clipped to a cap-length window at a
     uniformly drawn offset (same stratum RNG, drawn in selection order). Shorter bouts are used whole.
  5. Each correction carries the per-sample predicted and correct labels in the window, their majority
     labels (ties -> lower VOCAB code), and median/min/max of every feature in <features-dir>/<seq>/features.npz
     over the window (NaN-aware; null if no finite value).
Windows of different strata may overlap (e.g. a predicted attack on GT investigation is an attack FP and can
also sit inside an investigation FN); overlaps are counted in the output, not removed.

`frames_30fps` = [f0, f1) is the 30 fps frame range the scorer maps onto the window's samples (nearest-sample
upsampling, eval.score.upsample_samples); use it to score "dev minus the corrected windows".

Run log: one "started" record (predictions sha256) before any label is opened, then an "ok"/"failed" record
with the output sha256 and per-stratum counts, in eval/results/runs.jsonl (kind "simulate-corrections").

Usage:
  uv run python -m eval.simulate_corrections --split dev --pred-dir data/runs/R1/pred_dev_t1 \\
      --features-dir data/runs/R1 --seed 20261028 --out eval/results/corrections/R1_dev_t1.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from eval import common, score
from eval.common import BEHAVIORS, NATIVE_FPS, VOCAB

KINDS = ("fp", "fn")
CODE_TO_LABEL = {v: k for k, v in VOCAB.items()}
DEFAULT_OVERLAP = 0.2
DEFAULT_MAX_WINDOW_S = 3.0
DEFAULT_SAMPLE_FPS = 10
VERSION = 1


# --------------------------------------------------------------------------- pure helpers (tested)

def error_bouts(gt: np.ndarray, pred: np.ndarray, behavior: str, kind: str,
                overlap_thr: float = DEFAULT_OVERLAP, min_len: int = 1) -> list[dict]:
    """Error bouts of one behavior on one sequence (sample grid). Returns [{s0, s1, overlap}]."""
    if len(gt) != len(pred):
        raise ValueError(f"gt has {len(gt)} samples, pred {len(pred)}")
    code = VOCAB[behavior]
    src, other = (pred, gt) if kind == "fp" else (gt, pred) if kind == "fn" else (None, None)
    if src is None:
        raise ValueError(f"kind must be fp or fn, got {kind!r}")
    out = []
    for s0, s1 in score.runs(src, code):
        s0, s1 = int(s0), int(s1)
        if s1 - s0 < min_len:
            continue
        ov = float(np.mean(other[s0:s1] == code))
        if ov < overlap_thr:
            out.append({"s0": s0, "s1": s1, "overlap": ov})
    return out


def frames_for_window(s0: int, s1: int, n_frames: int, step: int) -> tuple[int, int]:
    """30 fps frames [f0, f1) that nearest-sample upsampling (frame f -> sample round(f/step), clamped to
    the last sample) maps onto samples [s0, s1)."""
    k_last = math.ceil(n_frames / step) - 1
    f0 = 0 if s0 == 0 else min(n_frames, math.ceil((s0 - 0.5) * step))
    f1 = n_frames if s1 - 1 >= k_last else min(n_frames, math.ceil((s1 - 0.5) * step))
    return f0, f1


def majority(labels: np.ndarray) -> str:
    return CODE_TO_LABEL[int(np.argmax(np.bincount(labels.astype(np.int64), minlength=len(VOCAB))))]


def feature_summary(features: dict, s0: int, s1: int) -> dict:
    out = {}
    for k in sorted(features):
        w = np.asarray(features[k], dtype=np.float64)[s0:s1]
        fin = w[np.isfinite(w)]
        if len(fin) == 0:
            out[k] = {"median": None, "min": None, "max": None, "n_finite": 0}
        else:
            out[k] = {"median": float(np.median(fin)), "min": float(fin.min()), "max": float(fin.max()),
                      "n_finite": int(len(fin))}
    return out


def select(candidates: list[dict], k: int, seed: int, behavior: str, kind: str, max_len: int) -> list[dict]:
    """Uniform draw of k candidates without replacement, then clip each to <= max_len samples at a uniform
    offset. candidates must be in a canonical order (split order, then s0)."""
    rng = np.random.default_rng([seed, BEHAVIORS.index(behavior), KINDS.index(kind)])
    n = len(candidates)
    idx = sorted(rng.choice(n, size=min(k, n), replace=False).tolist()) if n else []
    out = []
    for i in idx:
        c = candidates[i]
        L = c["s1"] - c["s0"]
        off = int(rng.integers(0, L - max_len + 1)) if L > max_len else 0
        w0 = c["s0"] + off
        out.append({**c, "candidate_index": i, "w0": w0, "w1": w0 + min(L, max_len)})
    return out


def count_overlaps(corrections: list[dict]) -> int:
    n = 0
    for i, a in enumerate(corrections):
        for b in corrections[i + 1:]:
            if a["seq"] == b["seq"] and a["s0"] < b["s1"] and b["s0"] < a["s1"]:
                n += 1
    return n


def excluded_frames(corrections: list[dict], seq: str, n_frames: int) -> np.ndarray:
    """bool[n_frames]: True on 30 fps frames covered by any correction window of `seq`."""
    m = np.zeros(n_frames, dtype=bool)
    for c in corrections:
        if c["seq"] == seq:
            m[c["frames_30fps"][0]:c["frames_30fps"][1]] = True
    return m


# --------------------------------------------------------------------------- driver

def _load_features(features_dir: Path, seq: str) -> dict:
    short = seq.split("_task1")[0]
    for stem in (seq, short):
        p = Path(features_dir) / stem / "features.npz"
        if p.exists():
            d = np.load(p)
            return {k: d[k] for k in d.files}
    raise FileNotFoundError(f"no features.npz for {seq} under {features_dir}")


def build(split: str, pred_dir: Path, features_dir: Path, out: Path, k_fp: int = 3, k_fn: int = 3,
          seed: int = common.SEED, overlap_thr: float = DEFAULT_OVERLAP,
          max_window_s: float = DEFAULT_MAX_WINDOW_S, sample_fps: float = DEFAULT_SAMPLE_FPS,
          min_bout_samples: int = 1, labels_dir: Path = common.LABELS_DIR, split_file: Path = common.SPLIT_FILE,
          log_path: Path = common.RUNS_LOG, notes: str = "") -> dict:
    if common.canonical_split(split) != "dev":
        raise SystemExit(f"simulate_corrections: split {split!r} refused; corrections come only from dev "
                         "(dev-holdout and test are where their effect is measured)")
    split = "dev"
    if k_fp < 0 or k_fn < 0:
        raise SystemExit("K must be >= 0")
    step = score.step_from_fps(sample_fps)
    max_len = int(math.floor(max_window_s * NATIVE_FPS / step + 1e-9))
    if max_len < 1:
        raise SystemExit("--max-window-s is shorter than one sample")
    seqs, _ = common.load_split(split, split_file)
    pred_dir, features_dir, out = Path(pred_dir), Path(features_dir), Path(out)
    manifest = score.read_manifest(pred_dir)
    files = {s: score.find_pred_file(pred_dir, s) for s in seqs}
    run_id = common.new_run_id("corrections-dev")
    pred_hash, per_file = score._hash_predictions(pred_dir, files)
    config = {"k_fp": k_fp, "k_fn": k_fn, "seed": seed, "overlap_thr": overlap_thr, "max_window_s": max_window_s,
              "max_window_samples": max_len, "sample_fps": sample_fps, "min_bout_samples": min_bout_samples}
    base = {"run_id": run_id, "kind": "simulate-corrections", "split": split, "purpose": "corrections",
            **common.git_info(), "split_file": str(split_file),
            "split_file_sha256": common.sha256_file(Path(split_file)), "sequences": seqs,
            "pred_dir": str(pred_dir), "predictions_sha256": pred_hash, "predictions_files_sha256": per_file,
            "features_dir": str(features_dir), "config": config, "config_sha256": common.sha256_json(config),
            "cost": {"usd": 0.0, "model_calls": 0}, "notes": notes}
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, log_path)
    t0 = time.time()
    labels_loaded = False
    try:
        missing = [s for s, p in files.items() if p is None]
        if missing:
            raise ValueError(f"no prediction file for {missing}")
        per_seq = {}
        for s in seqs:
            labels_loaded = True
            gt30 = common.load_sequence(split, s, labels_dir)["labels"]
            excl, _, _ = score.load_prediction(files[s], len(gt30), manifest)
            gt, pred = gt30[::step], excl[::step]
            feats = _load_features(features_dir, s)
            bad = {k: len(v) for k, v in feats.items() if len(v) != len(gt)}
            if bad:
                raise ValueError(f"{s}: features length {bad} != {len(gt)} samples")
            per_seq[s] = (gt, pred, feats, len(gt30))
        corrections, strata = [], {}
        for b in BEHAVIORS:
            for kind, k in (("fp", k_fp), ("fn", k_fn)):
                cands = []
                for s in seqs:
                    gt, pred = per_seq[s][:2]
                    cands += [{"seq": s, **c} for c in error_bouts(gt, pred, b, kind, overlap_thr, min_bout_samples)]
                chosen = select(cands, k, seed, b, kind, max_len)
                lens = [c["s1"] - c["s0"] for c in cands]
                strata[f"{b}/{kind}"] = {
                    "candidates": len(cands), "requested": k, "selected": len(chosen), "shortfall": k - len(chosen),
                    "candidate_len_samples": ({"min": min(lens), "median": float(np.median(lens)), "max": max(lens)}
                                              if lens else None)}
                for c in chosen:
                    s = c["seq"]
                    gt, pred, feats, n_frames = per_seq[s]
                    w0, w1 = c["w0"], c["w1"]
                    corrections.append({
                        "id": f"{b}-{kind}-{len([x for x in corrections if x['behavior'] == b and x['kind'] == kind])}",
                        "seq": s, "s0": w0, "s1": w1, "frames_30fps": list(frames_for_window(w0, w1, n_frames, step)),
                        "behavior": b, "kind": kind,
                        "predicted_label": majority(pred[w0:w1]), "correct_label": majority(gt[w0:w1]),
                        "predicted_labels": [CODE_TO_LABEL[int(x)] for x in pred[w0:w1]],
                        "correct_labels": [CODE_TO_LABEL[int(x)] for x in gt[w0:w1]],
                        "source_bout": {"s0": c["s0"], "s1": c["s1"], "overlap": c["overlap"],
                                        "candidate_index": c["candidate_index"],
                                        "clipped": (c["s1"] - c["s0"]) > max_len},
                        "features": feature_summary(feats, w0, w1)})
        doc = {"version": VERSION, "generator": "eval/simulate_corrections.py", "run_id": run_id, "split": split,
               "sample_fps": sample_fps, "sample_step_frames": step, "config": config,
               "pred_dir": str(pred_dir), "predictions_sha256": pred_hash, "features_dir": str(features_dir),
               "git_commit": base["git_commit"], "git_dirty": base["git_dirty"],
               "supervision": "few-shot: windows and correct labels come from dev expert labels; never zero-shot",
               "protocol": __doc__.split("Usage:")[0].strip(), "strata": strata,
               "n_corrections": len(corrections), "n_overlapping_pairs": count_overlaps(corrections),
               "corrections": corrections}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1))
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True, "out": str(out),
               "out_sha256": common.sha256_file(out), "n_corrections": len(corrections), "strata": strata,
               "n_overlapping_pairs": doc["n_overlapping_pairs"],
               "selected": [{k: c[k] for k in ("id", "seq", "s0", "s1", "behavior", "kind", "predicted_label",
                                               "correct_label")} for c in corrections],
               "wall_clock_s": round(time.time() - t0, 3)}
    except Exception as e:
        common.append_run({**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
                           "labels_loaded": labels_loaded, "traceback": traceback.format_exc(limit=5)}, log_path)
        raise
    common.append_run(rec, log_path)
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="dev", help="dev only (dev-holdout and test are refused)")
    ap.add_argument("--pred-dir", required=True, type=Path)
    ap.add_argument("--features-dir", required=True, type=Path, help="contains <seq>/features.npz")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--k-fp", type=int, default=3, help="false-positive windows per behavior")
    ap.add_argument("--k-fn", type=int, default=3, help="false-negative windows per behavior")
    ap.add_argument("--seed", type=int, default=common.SEED)
    ap.add_argument("--overlap-thr", type=float, default=DEFAULT_OVERLAP)
    ap.add_argument("--max-window-s", type=float, default=DEFAULT_MAX_WINDOW_S)
    ap.add_argument("--sample-fps", type=float, default=DEFAULT_SAMPLE_FPS)
    ap.add_argument("--min-bout-samples", type=int, default=1)
    ap.add_argument("--labels-dir", type=Path, default=common.LABELS_DIR)
    ap.add_argument("--split-file", type=Path, default=common.SPLIT_FILE)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    doc = build(a.split, a.pred_dir, a.features_dir, a.out, a.k_fp, a.k_fn, a.seed, a.overlap_thr, a.max_window_s,
                a.sample_fps, a.min_bout_samples, a.labels_dir, a.split_file, a.log, a.notes)
    for name, st in doc["strata"].items():
        print(f"{name:20s} candidates {st['candidates']:4d}  selected {st['selected']}/{st['requested']}")
    for c in doc["corrections"]:
        print(f"  {c['id']:18s} {c['seq']:28s} samples [{c['s0']},{c['s1']}) "
              f"pred {c['predicted_label']:13s} correct {c['correct_label']}")
    print(f"{doc['n_corrections']} corrections, {doc['n_overlapping_pairs']} overlapping pairs -> {a.out}")
    print(f"run {doc['run_id']} logged to {a.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
