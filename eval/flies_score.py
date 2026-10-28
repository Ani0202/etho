"""Scorer for Fly vs. Fly (Aggression) predictions (docs/EVAL_PLAN.md section 14).

Conventions follow eval/score.py: frame-level precision / recall / F1 per behavior at native 30 fps,
**pooled over all scored frames of all movies** of the split; 95% percentile CIs from a bootstrap over whole
movies (10,000 resamples, seed 20261028); a prevalence-matched random reference. Fly-specific additions:
- Primary unit is the **pair**: a frame is positive if either fly does the behavior (GT = union of both flies'
  bouts). Frames where either fly is annotated `unknown` are excluded from every count.
- Bout-level F1 (one-to-one matching, any overlap, greedy by temporal IoU; also IoU >= 0.3 / 0.5) on maximal
  runs of the pair-level masks. Reference: GT bouts shuffled to random positions (eval.flies_extract).
- Secondary per-fly (actor) F1 and identity metrics, when the predictions carry tracks (below).

Prediction directory (`--pred-dir`): one `movieN.npz` per movie, plus optional `manifest.json`
({"config", "cost", "wall_clock_s", "calls", "notes"}; copied into the run log; on test `config.frozen_sha256`
must equal the sha256 of `--frozen-config`).

  key                       dtype / shape            meaning
  window                    int [2]                  absolute native frames [start, end), must equal the
                                                     window being scored: mid5 = [22500, 31500),
                                                     mid10 = [18000, 36000)
  sample_fps                scalar                   30 or 10 (any divisor of 30); step = 30 / sample_fps
  wing_threat, lunge        uint8/bool [K]           pair-level 0/1 per sample, K = ceil((end - start) / step);
                                                     sample k is native frame start + k * step. Every behavior
                                                     being scored must be present.
  <behavior>_by_track       uint8/bool [2, K]        optional: actor per Etho track (row t = Etho track t)
  track_xy                  float [K, 2, 2]          optional: Etho track centroid (sample, track, x/y) in
                                                     NATIVE 144x144 pixels (divide out any upscaling); NaN
                                                     where the track is absent
  identity_unknown          bool [K]                 optional: Etho declared identity unknown at this sample

Per-sample values are held over native frames with the CalMS21 nearest-sample rule (EVAL_PLAN section 3):
window frame i takes sample min(round(i / step), K - 1), so at 10 fps sample k covers frames 3k-1 .. 3k+1 of
the window. The length must be exactly K: no silent padding.

Per-fly F1 needs `<behavior>_by_track` and `track_xy` in every movie. Etho track t is mapped to a GT fly by ONE
permutation per window: per sample, the 2x2 Hungarian assignment on centroid distance to the GT tracker
(`movieN_track.mat`, eval only); the window permutation is the majority assignment (ties -> identity). Identity
metrics from the same assignments: per-sample identity accuracy vs the window permutation (all evaluable
samples, and samples where the GT flies are >= 1 body length apart), assignment switches per minute (a
one-sample flip counts 2), error episodes per minute (a flip counts 1), % samples with identity unknown or a
missing track.

Splits: dev = movies 1-2, spare = 3-5, train = 1-5 (free use, logged), test = 6-10. Test refuses unless:
--confirm-test-run; --purpose test-1|test-2 (at most two flies test runs; same accounting as eval/common.py but
on split "flies-test"); --frozen-config whose every {"path", "sha256"} entry re-hashes equal and whose
"window" equals --window; manifest config.frozen_sha256 matching; the EVAL_PLAN fly section and the
criteria file no longer PROPOSED; eval/ and docs/EVAL_PLAN.md committed; the data files match
eval/flies_manifest.json; predictions for all five movies pass label-free validation. The predictions hash
is logged (status "started") before any label file is opened. Per-movie test metrics are sealed (written only
to <run_id>.per_movie.sealed.json, whose sha256 is logged).

Usage:
    uv run python -m eval.flies_score --split dev --window mid5 --pred-dir <dir> \
        [--criteria eval/flies_criteria_v1.json]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from eval import common
from eval import flies_extract as fx
from eval.score import _vec_prf, match_bouts, prf, step_from_fps

BOUT_THRESHOLDS = ("any", 0.3, 0.5)
ANY_OVERLAP_EPS = 1e-12  # IoU > 0  <=>  at least one shared frame
CRITERIA_FILE = Path("eval/flies_criteria_v1.json")
PLAN_FILE = Path("docs/EVAL_PLAN.md")
PROPOSED_MARKER = "FLIES-PLAN-STATUS: PROPOSED"
SEALED_SUFFIX = ".per_movie.sealed.json"
LOG_KIND = "flies-score"

__all__ = ["prf", "hold_samples", "bout_counts_any_overlap", "score_movies", "identity_metrics", "run"]


# --------------------------------------------------------------------------- predictions

def hold_samples(x: np.ndarray, n_frames: int, step: int) -> np.ndarray:
    """Nearest-sample hold of per-sample values onto n_frames native frames (eval/score.py rule)."""
    x = np.asarray(x)
    k_expected = math.ceil(n_frames / step)
    if x.shape[-1] != k_expected:
        raise ValueError(f"{x.shape[-1]} samples, expected exactly {k_expected} for {n_frames} frames at step {step}")
    idx = np.minimum(np.floor(np.arange(n_frames) / step + 0.5).astype(np.int64), k_expected - 1)
    return x[..., idx]


def _binary(a, name: str) -> np.ndarray:
    a = np.asarray(a)
    if a.dtype != bool:
        bad = set(np.unique(a).tolist()) - {0, 1}
        if bad:
            raise ValueError(f"{name}: values {sorted(bad)[:5]} are not 0/1")
    return a.astype(bool)


def load_prediction(path: Path, window: tuple[int, int], behaviors, manifest: dict | None = None) -> dict:
    """Validate one movie's npz (label-free) and hold it onto the window's native frames."""
    path = Path(path)
    name = path.name
    d = np.load(path)
    if "window" not in d.files:
        raise ValueError(f"{name}: missing 'window'")
    w = tuple(int(x) for x in np.asarray(d["window"]).ravel())
    if w != tuple(window):
        raise ValueError(f"{name}: window {w} != scored window {tuple(window)}")
    fps = float(d["sample_fps"]) if "sample_fps" in d.files else (manifest or {}).get("sample_fps")
    step = step_from_fps(fps)
    W = window[1] - window[0]
    K = math.ceil(W / step)
    out = {"sample_fps": fps, "step": step, "n_samples": K, "pair": {}, "by_track": {},
           "sample_frames": window[0] + step * np.arange(K)}
    for b in behaviors:
        if b not in d.files:
            raise ValueError(f"{name}: behavior {b!r} missing (keys {d.files})")
        a = _binary(d[b], f"{name}:{b}").ravel()
        if len(a) != K:
            raise ValueError(f"{name}:{b}: {len(a)} samples, expected exactly {K} (window {W} frames, step {step})")
        out["pair"][b] = hold_samples(a, W, step)
        key = f"{b}_by_track"
        if key in d.files:
            bt = _binary(d[key], f"{name}:{key}")
            if bt.shape != (2, K):
                raise ValueError(f"{name}:{key}: shape {bt.shape}, expected (2, {K})")
            out["by_track"][b] = hold_samples(bt, W, step)
    if "track_xy" in d.files:
        xy = np.asarray(d["track_xy"], dtype=np.float64)
        if xy.shape != (K, 2, 2):
            raise ValueError(f"{name}: track_xy shape {xy.shape}, expected ({K}, 2, 2)")
        out["track_xy"] = xy
    if "identity_unknown" in d.files:
        u = _binary(d["identity_unknown"], f"{name}:identity_unknown").ravel()
        if len(u) != K:
            raise ValueError(f"{name}: identity_unknown has {len(u)} samples, expected {K}")
        out["identity_unknown"] = u
    return out


# --------------------------------------------------------------------------- metrics

def bout_counts(gt: np.ndarray, pr: np.ndarray, thr) -> np.ndarray:
    """(n_gt, n_pred, matched) on maximal runs; thr 'any' = one-to-one any-overlap matching."""
    g, p = fx.runs(gt), fx.runs(pr)
    t = ANY_OVERLAP_EPS if thr == "any" else float(thr)
    return np.array([len(g), len(p), match_bouts(g, p, t)], dtype=np.int64)


def bout_counts_any_overlap(gt: np.ndarray, pr: np.ndarray) -> np.ndarray:
    return bout_counts(gt, pr, "any")


def _bout_prf(c) -> dict:
    n_gt, n_pr, m = (int(x) for x in c)
    p, r = (m / n_pr if n_pr else 0.0), (m / n_gt if n_gt else 0.0)
    return {"n_gt": n_gt, "n_pred": n_pr, "matched": m, "precision": p, "recall": r,
            "f1": 2 * p * r / (p + r) if p + r else 0.0}


def _block(c) -> dict:
    tp, fp, fn = (int(x) for x in c)
    p, r, f = prf(tp, fp, fn)
    return {"tp": tp, "fp": fp, "fn": fn, "gt_frames": tp + fn, "pred_frames": tp + fp,
            "precision": p, "recall": r, "f1": f}


def _pct(arr: np.ndarray) -> list | None:
    ok = ~np.isnan(arr)
    return [float(np.percentile(arr[ok], 2.5)), float(np.percentile(arr[ok], 97.5))] if ok.any() else None


def frame_bootstrap(per_movie: np.ndarray, reps: int, seed: int = common.SEED) -> dict:
    """per_movie [M, 3] (tp, fp, fn) -> movie-bootstrap percentile CIs for P/R/F1 (0/0 resamples dropped)."""
    w = common.bootstrap_weights(per_movie.shape[0], reps, seed)
    pooled = w @ per_movie
    p, r, f = _vec_prf(pooled)
    return {"precision": _pct(p), "recall": _pct(r), "f1": _pct(f),
            "f1_undefined_frac": float(np.isnan(f).mean()),
            "no_gt_resample_frac": float(((pooled[:, 0] + pooled[:, 2]) == 0).mean())}


def bout_bootstrap(per_movie: np.ndarray, reps: int, seed: int = common.SEED) -> dict:
    """per_movie [M, 3] (n_gt, n_pred, matched) -> CIs for bout recall / precision / F1."""
    w = common.bootstrap_weights(per_movie.shape[0], reps, seed)
    pooled = (w @ per_movie).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(pooled[:, 1] > 0, pooled[:, 2] / pooled[:, 1], np.nan)
        r = np.where(pooled[:, 0] > 0, pooled[:, 2] / pooled[:, 0], np.nan)
        f = np.where((pooled[:, 0] > 0) | (pooled[:, 1] > 0),
                     np.where(np.nan_to_num(p) + np.nan_to_num(r) > 0,
                              2 * np.nan_to_num(p) * np.nan_to_num(r) / (np.nan_to_num(p) + np.nan_to_num(r)), 0.0),
                     np.nan)
    return {"precision": _pct(p), "recall": _pct(r), "f1": _pct(f), "f1_undefined_frac": float(np.isnan(f).mean())}


def identity_metrics(sample_frames: np.ndarray, track_xy: np.ndarray, identity_unknown: np.ndarray | None,
                     gt: dict, window_minutes: float) -> dict:
    """Window identity metrics against the GT tracker. gt: output of flies_extract.load_gt_tracks."""
    K = len(sample_frames)
    g = np.stack([gt["x"][:, sample_frames].T, gt["y"][:, sample_frames].T], axis=2)  # [K, fly, xy]
    e = np.asarray(track_xy, dtype=np.float64)
    pres_e = ~np.isnan(e).any(axis=2)
    pres_g = ~np.isnan(g).any(axis=2)
    unk = np.zeros(K, bool) if identity_unknown is None else np.asarray(identity_unknown, bool)
    both_e = pres_e.all(axis=1)
    ev = both_e & pres_g.all(axis=1) & ~unk
    with np.errstate(invalid="ignore"):
        dist = np.linalg.norm(e[:, :, None, :] - g[:, None, :, :], axis=3)  # [K, track, fly]
        d_id = dist[:, 0, 0] + dist[:, 1, 1]
        d_sw = dist[:, 0, 1] + dist[:, 1, 0]
        gt_sep = np.linalg.norm(g[:, 0] - g[:, 1], axis=1)
        body = np.nanmean(gt["major_axis"][:, sample_frames], axis=0)
    swap = d_sw < d_id
    n_ev = int(ev.sum())
    n_sw = int(swap[ev].sum())
    perm_swap = bool(n_ev and n_sw * 2 > n_ev)
    correct = swap == perm_swap
    seq, cor = swap[ev], correct[ev]
    switches = int(np.sum(seq[1:] != seq[:-1])) if n_ev > 1 else 0
    episodes = int(len(fx.runs(~cor))) if n_ev else 0
    unamb = ev & (gt_sep >= body)
    matched_d = np.where(perm_swap, d_sw, d_id)[ev] / 2.0
    return {
        "n_samples": K, "n_evaluable": n_ev, "permutation": "swap" if perm_swap else "identity",
        "majority_tie": bool(n_ev and n_sw * 2 == n_ev),
        "identity_accuracy": float(cor.mean()) if n_ev else None,
        "identity_accuracy_unambiguous": float(correct[unamb].mean()) if unamb.any() else None,
        "n_unambiguous": int(unamb.sum()),
        "switches_per_min": switches / window_minutes, "error_episodes_per_min": episodes / window_minutes,
        "n_switches": switches, "n_error_episodes": episodes,
        "pct_unknown_or_missing": float(np.mean(unk | ~both_e)),
        "pct_declared_unknown": float(unk.mean()), "pct_track_missing": float(np.mean(~both_e)),
        "both_tracks_present_frac": float(both_e.mean()),
        "median_matched_centroid_dist_px": float(np.median(matched_d)) if n_ev else None,
        "_perm_swap": perm_swap,
    }


def score_movies(gt: dict, preds: dict, behaviors, reps: int = 10_000, gt_tracks: dict | None = None,
                 window_minutes: float | None = None, ref_reps: int = 200) -> dict:
    """Pure scoring. gt: {movie: window_labels(...)}; preds: {movie: load_prediction(...)}."""
    movies = list(gt)
    out = {"convention": "pair-level frame F1 at 30 fps, pooled over all scored frames of all movies; "
                         "frames where either fly is 'unknown' excluded", "movies": movies, "behaviors": {},
           "per_movie": {f"movie{m}": {} for m in movies}}
    keep = {m: ~gt[m]["excluded"] for m in movies}
    out["n_frames_scored"] = int(sum(k.sum() for k in keep.values()))
    out["n_frames_excluded"] = int(sum((~k).sum() for k in keep.values()))
    # identity first: per-fly scoring needs the window permutation
    ident, perm = {}, {}
    have_tracks = gt_tracks is not None and all("track_xy" in preds[m] for m in movies)
    if have_tracks:
        for m in movies:
            p = preds[m]
            ident[m] = identity_metrics(p["sample_frames"], p["track_xy"], p.get("identity_unknown"),
                                        gt_tracks[m], window_minutes or 1.0)
            perm[m] = ident[m].pop("_perm_swap")
        ev = sum(ident[m]["n_evaluable"] for m in movies)
        out["identity"] = {
            "per_movie": {f"movie{m}": ident[m] for m in movies},
            "pooled": {
                "identity_accuracy": (sum(ident[m]["identity_accuracy"] * ident[m]["n_evaluable"]
                                          for m in movies if ident[m]["n_evaluable"]) / ev) if ev else None,
                "switches_per_min": float(np.mean([ident[m]["switches_per_min"] for m in movies])),
                "error_episodes_per_min": float(np.mean([ident[m]["error_episodes_per_min"] for m in movies])),
                "pct_unknown_or_missing": float(np.mean([ident[m]["pct_unknown_or_missing"] for m in movies])),
                "both_tracks_present_frac": float(np.mean([ident[m]["both_tracks_present_frac"] for m in movies])),
                "note": "accuracy pooled over evaluable samples; rates are the mean over equal-length windows"}}
    else:
        out["identity"] = {"skipped": "track_xy missing in some movie (or GT tracks not loaded)"}
    for b in behaviors:
        pm = np.zeros((len(movies), 3), np.int64)
        bouts = {t: np.zeros((len(movies), 3), np.int64) for t in BOUT_THRESHOLDS}
        for i, m in enumerate(movies):
            g, pr, k = gt[m]["pair"][b], preds[m]["pair"][b], keep[m]
            gk, pk = g[k], pr[k]
            pm[i] = (np.sum(gk & pk), np.sum(~gk & pk), np.sum(gk & ~pk))
            for t in BOUT_THRESHOLDS:
                bouts[t][i] = bout_counts(g & k, pr & k, t)
            out["per_movie"][f"movie{m}"][b] = {"frame": _block(pm[i]), "bout_any": _bout_prf(bouts["any"][i])}
        blk = {"frame": _block(pm.sum(axis=0)), "frame_ci95": frame_bootstrap(pm, reps),
               "bouts": {str(t): _bout_prf(bouts[t].sum(axis=0)) for t in BOUT_THRESHOLDS},
               "bout_any_ci95": bout_bootstrap(bouts["any"], reps),
               "movies_with_behavior": [m for i, m in enumerate(movies) if pm[i, 0] + pm[i, 2] > 0]}
        blk["random_reference"] = fx.random_reference({m: gt[m]["pair"][b] for m in movies}, keep, ref_reps)
        # per-fly (actor) F1, secondary
        if have_tracks and all(b in preds[m]["by_track"] for m in movies):
            pf = np.zeros((len(movies), 3), np.int64)
            for i, m in enumerate(movies):
                bt = preds[m]["by_track"][b]
                fly_pred = bt[::-1] if perm[m] else bt  # row f = prediction for GT fly f
                gf, k = gt[m]["per_fly"][b], keep[m]
                gk, pk = gf[:, k], fly_pred[:, k]
                pf[i] = (np.sum(gk & pk), np.sum(~gk & pk), np.sum(gk & ~pk))
                out["per_movie"][f"movie{m}"][b]["per_fly_frame"] = _block(pf[i])
            blk["per_fly_frame"] = _block(pf.sum(axis=0))
            blk["per_fly_frame_ci95"] = frame_bootstrap(pf, reps)
            mism = sum(int(np.sum(preds[m]["by_track"][b].any(axis=0) != preds[m]["pair"][b])) for m in movies)
            blk["pair_vs_by_track_mismatch_frames"] = mism
        else:
            blk["per_fly_frame"] = None
        out["behaviors"][b] = blk
    return out


# --------------------------------------------------------------------------- pre-registered criteria

def _bar(ref: float, rule: dict) -> float:
    return max(rule["times_ref"] * ref, ref + rule["plus_ref"])


def _rel_check(point, ci_lo, ref, rule: dict) -> dict:
    bar = _bar(ref, rule["point_min"])
    lo_bar = ref if rule.get("ci_lower_gt") == "ref" else _bar(ref, rule["ci_lower_gt"])
    ok_point = point is not None and point >= bar
    ok_ci = ci_lo is not None and ci_lo > lo_bar
    return {"point": point, "point_bar": bar, "ci_lower": ci_lo, "ci_lower_bar": lo_bar, "reference": ref,
            "pass": bool(ok_point and ok_ci)}


def evidence(metrics: dict, b: str, rule: dict) -> dict:
    blk = metrics["behaviors"][b]
    n_b, n_m = blk["bouts"]["any"]["n_gt"], len(blk["movies_with_behavior"])
    ok = n_b >= rule["min_gt_bouts"] and n_m >= rule["min_movies_with_behavior"]
    return {"gt_bouts": n_b, "movies_with_behavior": n_m, "min_gt_bouts": rule["min_gt_bouts"],
            "min_movies_with_behavior": rule["min_movies_with_behavior"], "sufficient": ok}


def evaluate_go_no_go(metrics: dict, crit: dict, split: str, window: str, sample_fps: list,
                      partial: bool = False, now_utc: str | None = None) -> dict:
    """Mechanical go/no-go (EVAL_PLAN 14.6). Only on train-derived splits."""
    from datetime import datetime, timezone

    out = {}
    g = crit["go_no_go"]
    now = datetime.fromisoformat(now_utc) if now_utc else datetime.now(timezone.utc)
    if "wing_threat" in metrics["behaviors"]:
        r = g["wing_threat"]
        blk = metrics["behaviors"]["wing_threat"]
        ev = evidence(metrics, "wing_threat", r["evidence"])
        chk = _rel_check(blk["frame"]["f1"], (blk["frame_ci95"]["f1"] or [None])[0],
                         blk["random_reference"]["iid_expected_f1"], r)
        split_ok = split in r["splits_allowed"] and not (partial and r.get("require_full_split", True))
        on_time = now <= datetime.fromisoformat(r["deadline_et"])
        out["wing_threat"] = {"evidence": ev, "frame_f1": chk, "split_ok": split_ok, "partial": partial,
                              "before_deadline": on_time, "scored_at_utc": now.isoformat(timespec="seconds"),
                              "decision": "GO (F1 claim)" if (ev["sufficient"] and chk["pass"] and split_ok
                                                              and on_time)
                              else "NO-GO (qualitative clip, no F1 claim)"}
    if "lunge" in metrics["behaviors"]:
        r = g["lunge"]
        blk = metrics["behaviors"]["lunge"]
        ev = evidence(metrics, "lunge", r["evidence"])
        cov = (metrics.get("identity", {}).get("pooled") or {}).get("both_tracks_present_frac")
        b_any = blk["bouts"]["any"]
        bf1 = _rel_check(b_any["f1"], (blk["bout_any_ci95"]["f1"] or [None])[0],
                         blk["random_reference"]["bout_shuffle_mean_bout_f1"], r["bout_f1"])
        checks = {"window_ok": window == r["window"], "fps_ok": sample_fps == [str(float(r["sample_fps"]))],
                  "split_ok": split in r["splits_allowed"],
                  "both_tracks_present_frac": cov,
                  "coverage_ok": cov is not None and cov >= r["both_tracks_present_min"],
                  "bout_recall": b_any["recall"], "recall_ok": b_any["recall"] >= r["bout_recall_min"],
                  "bout_f1": bf1}
        go = all([ev["sufficient"], checks["window_ok"], checks["fps_ok"], checks["split_ok"], checks["coverage_ok"],
                  checks["recall_ok"], bf1["pass"]])
        out["lunge"] = {"evidence": ev, **checks, "decision": "GO (score lunge on test, mid5 @ 30 fps)" if go
                        else "NO-GO (wing_threat only, mid10 @ 10 fps)"}
    return out


def evaluate_test_targets(metrics: dict, crit: dict) -> dict:
    out = {}
    for b, tgt in crit["test_targets"].items():
        if b not in metrics["behaviors"]:
            continue
        blk = metrics["behaviors"][b]
        ev = evidence(metrics, b, tgt["evidence"])
        res = {"evidence": ev}
        for metric, levels in tgt["metrics"].items():
            if metric == "frame_f1":
                point, lo, ref = blk["frame"]["f1"], (blk["frame_ci95"]["f1"] or [None])[0], \
                    blk["random_reference"]["iid_expected_f1"]
            else:  # bout_any_f1
                point, lo, ref = blk["bouts"]["any"]["f1"], (blk["bout_any_ci95"]["f1"] or [None])[0], \
                    blk["random_reference"]["bout_shuffle_mean_bout_f1"]
            res[metric] = {lvl: _rel_check(point, lo, ref, rule) for lvl, rule in levels.items()}
            if not ev["sufficient"]:
                res[metric]["verdict"] = "NOT EVALUABLE (low evidence): report the number, make no claim"
            else:
                lv = [lvl for lvl in ("good", "credible") if res[metric][lvl]["pass"]]
                res[metric]["verdict"] = f"{lv[0].upper()} MET" if lv else "NOT MET"
        out[b] = res
    return out


# --------------------------------------------------------------------------- test guard

def prior_flies_test_runs(log_path: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    p = Path(log_path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("split") == "flies-test":
                out.setdefault(rec.get("purpose", "?"), []).append(rec)
    return out


def guard_flies_test(confirm: bool, purpose: str, log_path: Path, check_git: bool = True,
                     plan_file: Path = PLAN_FILE, criteria: dict | None = None) -> None:
    if not confirm:
        raise SystemExit("flies test split: pass --confirm-test-run (one of the 2 allowed flies test runs)")
    if purpose not in ("test-1", "test-2"):
        raise SystemExit("flies test split: --purpose must be test-1 or test-2")
    if Path(plan_file).exists() and PROPOSED_MARKER in Path(plan_file).read_text():
        raise SystemExit(f"flies test split: {plan_file} still says '{PROPOSED_MARKER}' (plan not frozen)")
    if criteria is None or criteria.get("status") != "FROZEN":
        raise SystemExit("flies test split: the criteria file must have status FROZEN")
    prior = prior_flies_test_runs(log_path)
    if purpose == "test-2" and "test-1" not in prior:
        raise SystemExit("flies test split: test-2 requested but no flies test-1 is logged")
    same = prior.get(purpose, [])
    finals = {r["run_id"]: r for r in same if r.get("status") != "started"}
    for r in same:
        if r.get("status") != "started":
            continue
        fin = finals.get(r["run_id"])
        if fin is None or fin.get("status") != "failed" or fin.get("labels_loaded", True):
            raise SystemExit(f"flies test split: {purpose} already executed (run {r['run_id']}); only a run that "
                             "failed before loading labels may be restarted")
    if check_git:
        st = subprocess.run(["git", "status", "--porcelain", "--", "eval", str(plan_file), ":(exclude)eval/results"],
                            capture_output=True, text=True).stdout.strip()
        if st:
            raise SystemExit(f"flies test split: uncommitted changes; commit first:\n{st}")


# --------------------------------------------------------------------------- run

def run(split: str, window: str, pred_dir: Path, behaviors=fx.SCORED, purpose: str | None = None,
        movies: list[int] | None = None, root: Path = fx.DATA_ROOT, log_path: Path = common.RUNS_LOG,
        out_dir: Path | None = Path("eval/results"), reps: int = 10_000, ref_reps: int = 200,
        criteria_path: Path | None = None, frozen_config: Path | None = None, confirm_test_run: bool = False,
        notes: str = "", check_git: bool = True, plan_file: Path = PLAN_FILE,
        data_manifest: Path = fx.MANIFEST) -> dict:
    all_movies = list(fx.split_movies(split))
    if window not in fx.WINDOWS:
        raise SystemExit(f"--window must be one of {sorted(fx.WINDOWS)}")
    w = fx.WINDOWS[window]
    behaviors = tuple(behaviors)
    if set(behaviors) - set(fx.SCORED):
        raise SystemExit(f"behaviors must be a subset of {fx.SCORED}")
    crit = json.loads(Path(criteria_path).read_text()) if criteria_path else None
    is_test = split == "test"
    frozen_info = None
    if is_test:
        if movies:
            raise SystemExit("flies test split: --movies is not allowed (all five test movies)")
        purpose = purpose or ""
        guard_flies_test(confirm_test_run, purpose, log_path, check_git, plan_file, crit)
        if frozen_config is None:
            raise SystemExit("flies test split: --frozen-config is required")
        from eval.score_arms import verify_frozen_config

        frozen_info, cfg = verify_frozen_config(frozen_config)
        if frozen_info["mismatches"]:
            raise SystemExit("flies test split: " + "; ".join(frozen_info["mismatches"]))
        if cfg.get("window") != window:
            raise SystemExit(f"flies test split: frozen config window {cfg.get('window')!r} != --window {window}")
        if Path(data_manifest).exists():
            bad = [b for b in fx.verify_manifest(data_manifest) if any(f"movie{m}/" in b for m in all_movies)
                   or "traintest" in b]
            if bad:
                raise SystemExit("flies test split: data files differ from the manifest: " + "; ".join(bad))
        else:
            raise SystemExit(f"flies test split: data manifest {data_manifest} missing")
    else:
        purpose = purpose or "tune"
        if movies:
            unknown = set(movies) - set(all_movies)
            if unknown:
                raise SystemExit(f"movies {sorted(unknown)} not in {split} {all_movies}")
            all_movies = [m for m in all_movies if m in set(movies)]
    movies = all_movies
    pred_dir = Path(pred_dir)
    man_p = pred_dir / "manifest.json"
    manifest = json.loads(man_p.read_text()) if man_p.exists() else {}
    files = {m: pred_dir / f"movie{m}.npz" for m in movies}
    missing = [m for m, p in files.items() if not p.exists()]
    if missing:
        raise SystemExit(f"no prediction file for movies {missing} in {pred_dir}")
    # label-free validation before anything is logged as started
    preds = {m: load_prediction(files[m], w, behaviors, manifest) for m in movies}
    fps = sorted({str(float(p["sample_fps"])) for p in preds.values()})
    if is_test:
        cfg_m = manifest.get("config", {})
        if cfg_m.get("frozen_sha256") != frozen_info["sha256"]:
            raise SystemExit("flies test split: manifest config.frozen_sha256 does not match --frozen-config")
    per_file = {f"movie{m}.npz": common.sha256_file(p) for m, p in files.items()}
    if man_p.exists():
        per_file["manifest.json"] = common.sha256_file(man_p)
    run_id = common.new_run_id(f"flies-{split}")
    config = manifest.get("config", {})
    base = {"run_id": run_id, "kind": LOG_KIND, "dataset": "flyvsfly", "split": f"flies-{split}",
            "purpose": purpose, **common.git_info(), "window": window, "window_frames": list(w),
            "behaviors": list(behaviors), "movies": movies, "partial": movies != list(fx.split_movies(split)),
            "pred_dir": str(pred_dir), "predictions_sha256": common.sha256_json(per_file),
            "predictions_files_sha256": per_file, "sample_fps": fps, "config": config,
            "config_sha256": common.sha256_json(config), "frozen_config": frozen_info,
            "criteria_file": str(criteria_path) if criteria_path else None,
            "criteria_sha256": common.sha256_file(Path(criteria_path)) if criteria_path else None,
            "scorer": {"reps": reps, "ref_reps": ref_reps, "seed": common.SEED, "bout_thresholds":
                       [str(t) for t in BOUT_THRESHOLDS]},
            "cost": manifest.get("cost"), "wall_clock_s": manifest.get("wall_clock_s"),
            "calls": manifest.get("calls"), "notes": notes or manifest.get("notes", "")}
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, log_path)
    t0 = time.time()
    labels_loaded = False
    try:
        if is_test:
            fx.verify_split(root)
        gt, tracks = {}, {}
        for m in movies:
            labels_loaded = True
            gt[m] = fx.load_window_labels(m, window, root, allow_test=is_test, behaviors=behaviors)
        if all("track_xy" in preds[m] for m in movies):
            tracks = {m: fx.load_gt_tracks(m, root, allow_test=is_test) for m in movies}
        minutes = (w[1] - w[0]) / fx.NATIVE_FPS / 60
        metrics = score_movies(gt, preds, behaviors, reps, tracks or None, minutes, ref_reps)
        if is_test:
            metrics["inter_annotator"] = inter_annotator(movies, window, behaviors, root, gt)
        verdicts = None
        if crit is not None:
            verdicts = (evaluate_test_targets(metrics, crit) if is_test
                        else evaluate_go_no_go(metrics, crit, split, window, fps, base["partial"]))
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
               "scorer_wall_clock_s": round(time.time() - t0, 3), "verdicts": verdicts}
        if is_test and out_dir is not None:
            sealed = {"per_movie": metrics.pop("per_movie"),
                      "identity_per_movie": metrics.get("identity", {}).pop("per_movie", None)}
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            sp = Path(out_dir) / f"{run_id}{SEALED_SUFFIX}"
            sp.write_text(json.dumps(sealed, indent=1, default=str))
            rec["sealed_per_movie"] = {"path": str(sp), "sha256": common.sha256_file(sp)}
        rec["metrics"] = metrics
    except Exception as e:  # failed runs are logged too
        common.append_run({**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
                           "labels_loaded": labels_loaded, "traceback": traceback.format_exc(limit=5)}, log_path)
        raise
    common.append_run(rec, log_path)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"{run_id}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def inter_annotator(movies, window, behaviors, root, gt) -> dict:
    """Second annotation (movie6_second_actions.mat) scored against the primary one, pair-level, same window."""
    out = {}
    for m in movies:
        if "second_actions" not in fx.movie_paths(m, root):
            continue
        try:
            sec = fx.load_window_labels(m, window, root, allow_test=True, behaviors=behaviors, second=True)
            k = ~(gt[m]["excluded"] | sec["excluded"])
            res = {}
            for b in behaviors:
                g, p = gt[m]["pair"][b][k], sec["pair"][b][k]
                res[b] = {"frame": _block((np.sum(g & p), np.sum(~g & p), np.sum(g & ~p))),
                          "bout_any": _bout_prf(bout_counts(gt[m]["pair"][b] & k, sec["pair"][b] & k, "any"))}
            out[f"movie{m}"] = {"reference": "primary annotation", "compared": "second annotation", **res}
        except Exception as e:  # never fail the test run over the reference
            out[f"movie{m}"] = {"error": repr(e)}
    return out


def format_report(rec: dict) -> str:
    m = rec["metrics"]
    L = [f"run {rec['run_id']} split={rec['split']} window={rec['window']} purpose={rec['purpose']}"
         + (" PARTIAL" if rec["partial"] else ""),
         f"movies {rec['movies']}; {m['n_frames_scored']} frames scored, {m['n_frames_excluded']} excluded (unknown)",
         "", f"{'behavior':<12}{'P':>7}{'R':>7}{'F1':>7}  {'F1 95% CI (movie bootstrap)':<28}{'rand':>7}"
             f"{'bout R':>8}{'bout P':>8}{'bout F1':>8}{'shuf F1':>8}"]
    for b, x in m["behaviors"].items():
        f, ci, rr, bt = x["frame"], x["frame_ci95"]["f1"], x["random_reference"], x["bouts"]["any"]
        cis = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "n/a"
        L.append(f"{b:<12}{f['precision']:>7.3f}{f['recall']:>7.3f}{f['f1']:>7.3f}  {cis:<28}"
                 f"{rr['iid_expected_f1']:>7.3f}{bt['recall']:>8.3f}{bt['precision']:>8.3f}{bt['f1']:>8.3f}"
                 f"{rr['bout_shuffle_mean_bout_f1']:>8.3f}")
        if x.get("per_fly_frame"):
            L.append(f"{'':<12}per-fly F1 {x['per_fly_frame']['f1']:.3f}")
    idp = m.get("identity", {}).get("pooled")
    if idp:
        L.append(f"identity: accuracy {idp['identity_accuracy']}, switches/min {idp['switches_per_min']:.2f}, "
                 f"episodes/min {idp['error_episodes_per_min']:.2f}, unknown/missing "
                 f"{idp['pct_unknown_or_missing']:.3f}, both present {idp['both_tracks_present_frac']:.3f}")
    if rec.get("verdicts"):
        L += ["", "verdicts: " + json.dumps(rec["verdicts"], indent=1, default=str)]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=sorted(fx.SPLITS))
    ap.add_argument("--window", required=True, choices=sorted(fx.WINDOWS))
    ap.add_argument("--pred-dir", required=True, type=Path)
    ap.add_argument("--behaviors", default=",".join(fx.SCORED))
    ap.add_argument("--purpose", help="tune (default), go-no-go, ablation:<name>; test: test-1|test-2")
    ap.add_argument("--movies", help="comma-separated subset (not on test)")
    ap.add_argument("--root", type=Path, default=fx.DATA_ROOT)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--out-dir", type=Path, default=Path("eval/results"))
    ap.add_argument("--reps", type=int, default=10_000)
    ap.add_argument("--criteria", type=Path, help=f"pre-registered criteria ({CRITERIA_FILE}); required on test")
    ap.add_argument("--frozen-config", type=Path)
    ap.add_argument("--confirm-test-run", action="store_true")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    rec = run(a.split, a.window, a.pred_dir, tuple(a.behaviors.split(",")), a.purpose,
              [int(x) for x in a.movies.split(",")] if a.movies else None, a.root, a.log, a.out_dir, a.reps,
              criteria_path=a.criteria or (CRITERIA_FILE if a.split == "test" else None),
              frozen_config=a.frozen_config, confirm_test_run=a.confirm_test_run, notes=a.notes)
    print(format_report(rec))
    print(f"\nlogged to {a.log}; full result {a.out_dir}/{rec['run_id']}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
