"""Scorer for Fly vs. Fly (Aggression) predictions (docs/EVAL_PLAN.md section 14, amended and frozen in 14.12).

Conventions follow eval/score.py: frame-level precision / recall / F1 per behavior at native 30 fps,
**pooled over all scored frames of all movies** of the split; 95% percentile CIs from a bootstrap over whole
movies (10,000 resamples, seed 20261028). Fly-specific rules:
- Primary unit is the **pair**: a frame is positive if either fly does the behavior (GT = union of both flies'
  bouts). Frames where either fly is annotated `unknown` are excluded from every count.
- Bout-level metrics use **censored, maximum-cardinality one-to-one matching** (`fly_bout_counts`): bouts are
  the maximal runs of the UNEXCLUDED masks (so an `unknown` interval never splits a GT bout into two events);
  a bout with no kept frame is dropped; overlap and IoU are counted over kept frames only; a GT/pred pair is
  eligible if it shares >= 1 kept frame ("any") or its kept-frame IoU >= the threshold (0.3, 0.5); among
  eligible pairs the matching maximizes the number of matches, then the total IoU. (eval/score.py's greedy
  matcher for CalMS21 is unchanged.)
- References and baselines (report-only unless a criterion names them): iid prevalence-matched (the floor r),
  always-positive, dev-prior iid, oracle bout placement (r_b), a circular-shift test of the actual predictions,
  leave-one-movie-out F1, false-positive bouts per minute, per-fly baselines (persistent actor, copy-to-both).
- Per-fly (actor) F1 and identity metrics when the predictions carry tracks (below).

Prediction directory (`--pred-dir`): one `movieN.npz` per movie, plus `manifest.json` ({"config", "cost",
"wall_clock_s", "calls", "notes"}; on gate and test runs `config.frozen_sha256` must equal the sha256 of
`--frozen-config` and `config.run_commit` must name the commit the pipeline ran at).

  key                       dtype / shape            meaning
  window                    int [2]                  absolute native frames [start, end): mid5 = [22500, 31500),
                                                     mid10 = [18000, 36000)
  sample_fps                scalar                   30 or 10; step = 30 / sample_fps
  wing_threat, lunge        uint8/bool [K]           pair-level 0/1 per sample, K = ceil((end - start) / step);
                                                     sample k is native frame start + k * step
  <behavior>_by_track       uint8/bool [2, K]        optional actor per Etho track. Must satisfy
                                                     by_track[t, k] = 1  =>  pair[k] = 1 (else rejected);
                                                     pair = 1 with no track set = "actor unassigned" (allowed,
                                                     counted, and a per-fly miss)
  track_xy                  float [K, 2, 2]          optional Etho centroid (sample, track, x/y) in NATIVE
                                                     144x144 px; NaN where absent
  identity_unknown          bool [K]                 optional: Etho declared identity unknown at this sample
  id_epoch                  float/int [K]            optional: Etho identity epoch (diagnostic: accuracy with
                                                     one permutation per epoch; per-fly F1 still uses one
                                                     permutation per window)

Per-sample values are held over native frames by the nearest-sample rule: window frame i takes sample
min(floor(i / step + 0.5), K - 1). Lengths must be exact.

Identity: per sample, the 2x2 Hungarian assignment of Etho tracks to GT flies (GT tracker, eval only) on
centroid distance; the window permutation is the majority assignment (ties -> identity). A sample is
"localized" when both Etho tracks are finite and each matched distance is <= 0.5 GT body length.

Decision tree (frozen, EVAL_PLAN 14.12): lunge gate (purpose gate-lunge, split dev = movies 1-2, mid5, 30 fps,
one execution) -> `decide --stage lunge` writes eval/flies_decision.json, committed -> wing gate (purpose
gate-wing, split train = 1-5, the branch's window/fps, one execution, by the Oct 31 ET deadline) -> `decide
--stage wing` -> test only if the wing gate is GO, with the branch's window/fps/behaviors and the same frozen
config. Every check that can be mechanical is enforced here before any label file is opened.

Usage:
    uv run python -m eval.flies_score --split dev --window mid5 --pred-dir <dir>          # free tuning run
    uv run python -m eval.flies_score --split dev --window mid5 --pred-dir <dir> --purpose gate-lunge \\
        --frozen-config configs/frozen_flies_v1.json
    uv run python -m eval.flies_score decide --stage lunge --run-id <run_id>
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from eval import common
from eval import flies_extract as fx
from eval.score import _vec_prf, prf, step_from_fps

BOUT_THRESHOLDS = ("any", 0.3, 0.5)
CRITERIA_FILE = Path("eval/flies_criteria_v1.json")
DECISION_FILE = Path("eval/flies_decision.json")
PLAN_FILE = Path("docs/EVAL_PLAN.md")
PROPOSED_MARKER = "FLIES-PLAN-STATUS: PROPOSED"
SEALED_SUFFIX = ".per_movie.sealed.json"
LOG_KIND = "flies-score"
CI_UNDEFINED_MAX = 0.05  # a CI with more than 5% undefined (0/0) resamples is "uninformative" and fails
LOC_GATE_BODY = 0.5
SHIFT_REPS = 1000
BRANCHES = {"GO": {"window": "mid5", "sample_fps": 30, "behaviors": ["wing_threat", "lunge"]},
            "NO-GO": {"window": "mid10", "sample_fps": 10, "behaviors": ["wing_threat"]}}
LUNGE_GATE = {"split": "dev", "movies": [1, 2], "window": "mid5", "sample_fps": 30,
              "behaviors": ["wing_threat", "lunge"]}
GATE_STAGE = {"gate-lunge": "lunge", "gate-wing": "wing"}
REQUIRED_ASSET_ROLES = ("definitions", "detectors", "tracking", "runner")
DEFINITIONS_PATH = "configs/flies_definitions.json"
FROZEN_TREES = ("etho/", ":!etho/app", "configs/")


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
    out = {"sample_fps": fps, "step": step, "n_samples": K, "pair": {}, "by_track": {}, "unassigned_samples": {},
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
            bad = bt.any(axis=0) & ~a
            if bad.any():
                raise ValueError(f"{name}:{key}: {int(bad.sum())} samples name an actor where the pair label is 0")
            out["unassigned_samples"][b] = int((a & ~bt.any(axis=0)).sum())
            out["by_track"][b] = hold_samples(bt, W, step)
    if "track_xy" in d.files:
        xy = np.asarray(d["track_xy"], dtype=np.float64)
        if xy.shape != (K, 2, 2):
            raise ValueError(f"{name}: track_xy shape {xy.shape}, expected ({K}, 2, 2)")
        if np.isinf(xy).any():
            raise ValueError(f"{name}: track_xy has infinite values (use NaN for absent)")
        out["track_xy"] = xy
    if "id_epoch" in d.files:
        ep = np.asarray(d["id_epoch"], dtype=np.float64).ravel()
        if len(ep) != K:
            raise ValueError(f"{name}: id_epoch has {len(ep)} samples, expected {K}")
        out["id_epoch"] = ep
    if "identity_unknown" in d.files:
        u = _binary(d["identity_unknown"], f"{name}:identity_unknown").ravel()
        if len(u) != K:
            raise ValueError(f"{name}: identity_unknown has {len(u)} samples, expected {K}")
        out["identity_unknown"] = u
    return out


# --------------------------------------------------------------------------- bout matching (fly-specific)

def _kept_runs(mask: np.ndarray, csum: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = fx.runs(mask)
    if not len(r):
        return r, np.zeros(0, np.int64)
    kl = csum[r[:, 1]] - csum[r[:, 0]]
    ok = kl > 0
    return r[ok], kl[ok]


def max_card_match(eligible: np.ndarray, iou: np.ndarray) -> int:
    """Maximum-cardinality one-to-one matching on a boolean eligibility matrix; ties broken by total IoU."""
    if eligible.size == 0 or not eligible.any():
        return 0
    rows = np.flatnonzero(eligible.any(axis=1))
    cols = np.flatnonzero(eligible.any(axis=0))
    e, u = eligible[np.ix_(rows, cols)], iou[np.ix_(rows, cols)]
    from scipy.optimize import linear_sum_assignment

    n = max(e.shape)
    weight = np.where(e, 1.0 + u / (n + 1.0), 0.0)  # cardinality dominates; IoU only breaks ties
    ri, ci = linear_sum_assignment(weight, maximize=True)
    return int(e[ri, ci].sum())


def fly_bout_counts(gt: np.ndarray, pr: np.ndarray, keep: np.ndarray, thr) -> np.ndarray:
    """(n_gt, n_pred, matched) with censoring at excluded frames (see module docstring)."""
    keep = np.asarray(keep, bool)
    csum = np.concatenate([[0], np.cumsum(keep)])
    g, gl = _kept_runs(np.asarray(gt, bool), csum)
    p, pl = _kept_runs(np.asarray(pr, bool), csum)
    if not len(g) or not len(p):
        return np.array([len(g), len(p), 0], dtype=np.int64)
    lo = np.maximum(g[:, None, 0], p[None, :, 0])
    hi = np.minimum(g[:, None, 1], p[None, :, 1])
    inter = np.where(hi > lo, csum[np.maximum(hi, lo)] - csum[lo], 0)
    union = gl[:, None] + pl[None, :] - inter
    iou = inter / union
    eligible = inter >= 1 if thr == "any" else iou >= float(thr)
    return np.array([len(g), len(p), max_card_match(eligible, iou)], dtype=np.int64)


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
    und = float(np.isnan(f).mean())
    return {"precision": _pct(p), "recall": _pct(r), "f1": _pct(f), "f1_undefined_frac": und,
            "informative": und <= CI_UNDEFINED_MAX,
            "no_gt_resample_frac": float(((pooled[:, 0] + pooled[:, 2]) == 0).mean())}


def bout_bootstrap(per_movie: np.ndarray, reps: int, seed: int = common.SEED) -> dict:
    """per_movie [M, 3] (n_gt, n_pred, matched) -> CIs for bout recall / precision / F1."""
    w = common.bootstrap_weights(per_movie.shape[0], reps, seed)
    pooled = (w @ per_movie).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(pooled[:, 1] > 0, pooled[:, 2] / pooled[:, 1], np.nan)
        r = np.where(pooled[:, 0] > 0, pooled[:, 2] / pooled[:, 0], np.nan)
        pp, rr = np.nan_to_num(p), np.nan_to_num(r)
        f = np.where((pooled[:, 0] > 0) | (pooled[:, 1] > 0),
                     np.where(pp + rr > 0, 2 * pp * rr / np.where(pp + rr > 0, pp + rr, 1), 0.0), np.nan)
    und = float(np.isnan(f).mean())
    return {"precision": _pct(p), "recall": _pct(r), "f1": _pct(f), "f1_undefined_frac": und,
            "informative": und <= CI_UNDEFINED_MAX}


# --------------------------------------------------------------------------- identity

def identity_metrics(sample_frames: np.ndarray, track_xy: np.ndarray, identity_unknown: np.ndarray | None,
                     gt: dict, window_minutes: float, behavior_at_samples: dict | None = None,
                     id_epoch: np.ndarray | None = None) -> dict:
    """Window identity and localization metrics against the GT tracker (load_gt_tracks output)."""
    K = len(sample_frames)
    g = np.stack([gt["x"][:, sample_frames].T, gt["y"][:, sample_frames].T], axis=2)  # [K, fly, xy]
    e = np.asarray(track_xy, dtype=np.float64)
    pres_e = np.isfinite(e).all(axis=2)
    pres_g = np.isfinite(g).all(axis=2)
    unk = np.zeros(K, bool) if identity_unknown is None else np.asarray(identity_unknown, bool)
    both_e = pres_e.all(axis=1)
    both_g = pres_g.all(axis=1)
    ev = both_e & both_g & ~unk
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
    with np.errstate(invalid="ignore"):
        close = both_g & (gt_sep < body)
        unamb = ev & ~close
        best = np.where(swap, np.maximum(dist[:, 0, 1], dist[:, 1, 0]), np.maximum(dist[:, 0, 0], dist[:, 1, 1]))
        localized = both_e & both_g & (best <= LOC_GATE_BODY * body)
    matched_d = np.where(perm_swap, d_sw, d_id)[ev] / 2.0
    out = {
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
        "both_localized_frac": float(localized.mean()),
        "both_localized_frac_close_contact": float(localized[close].mean()) if close.any() else None,
        "n_close_contact_samples": int(close.sum()),
        "median_matched_centroid_dist_px": float(np.median(matched_d)) if n_ev else None,
        "_perm_swap": perm_swap,
    }
    for b, m in (behavior_at_samples or {}).items():
        out[f"both_localized_frac_during_{b}"] = float(localized[m].mean()) if m.any() else None
    if id_epoch is not None:  # diagnostic only: one majority permutation per declared epoch (per-fly F1 ignores it)
        ok_e = ev & np.isfinite(id_epoch)
        n_ok = 0
        for u in np.unique(id_epoch[ok_e]):
            sel = ok_e & (id_epoch == u)
            k_sw = int(swap[sel].sum())
            n_ok += max(k_sw, int(sel.sum()) - k_sw)
        out["identity_accuracy_per_epoch_permutation"] = n_ok / int(ok_e.sum()) if ok_e.any() else None
        out["n_epochs"] = int(len(np.unique(id_epoch[np.isfinite(id_epoch)])))
    return out


# --------------------------------------------------------------------------- scoring

def circular_shift_test(gt: dict, preds: dict, keep: dict, b: str, observed: float, reps: int = SHIFT_REPS,
                        seed: int = common.SEED) -> dict:
    """Shift each movie's predicted pair mask circularly by a random offset in [W/10, 9W/10] (keeps the
    predictor's positive rate and temporal structure, breaks alignment) and re-score pooled frame F1."""
    rng = np.random.default_rng(seed)
    movies = list(gt)
    f1s = np.zeros(reps)
    for i in range(reps):
        c = np.zeros(3, np.int64)
        for m in movies:
            pr, g, k = preds[m]["pair"][b], gt[m]["pair"][b], keep[m]
            W = len(pr)
            s = int(rng.integers(W // 10, 9 * W // 10 + 1))
            ps = np.roll(pr, s)
            c += (np.sum(g[k] & ps[k]), np.sum(~g[k] & ps[k]), np.sum(g[k] & ~ps[k]))
        f1s[i] = prf(*c)[2]
    return {"reps": reps, "seed": seed, "shift_range": "[W/10, 9W/10] per movie", "mean_f1": float(f1s.mean()),
            "p95_f1": float(np.percentile(f1s, 95)),
            "p_value": float((1 + np.sum(f1s >= observed)) / (1 + reps))}


def score_movies(gt: dict, preds: dict, behaviors, reps: int = 10_000, gt_tracks: dict | None = None,
                 window_minutes: float | None = None, ref_reps: int = 200, dev_prior: dict | None = None,
                 shift_reps: int = SHIFT_REPS) -> dict:
    """Pure scoring. gt: {movie: window_labels(...)}; preds: {movie: load_prediction(...)}."""
    movies = list(gt)
    minutes = window_minutes or 1.0
    out = {"convention": "pair-level frame F1 at 30 fps, pooled over all scored frames of all movies; "
                         "frames where either fly is 'unknown' excluded", "movies": movies, "behaviors": {},
           "per_movie": {f"movie{m}": {} for m in movies}}
    keep = {m: ~gt[m]["excluded"] for m in movies}
    out["n_frames_scored"] = int(sum(k.sum() for k in keep.values()))
    out["n_frames_excluded"] = int(sum((~k).sum() for k in keep.values()))
    scored_min = out["n_frames_scored"] / fx.NATIVE_FPS / 60
    ident, perm = {}, {}
    have_tracks = gt_tracks is not None and all("track_xy" in preds[m] for m in movies)
    if have_tracks:
        for m in movies:
            p = preds[m]
            idx = p["sample_frames"] - p["sample_frames"][0]
            beh = {b: gt[m]["pair"][b][idx] for b in behaviors}
            ident[m] = identity_metrics(p["sample_frames"], p["track_xy"], p.get("identity_unknown"),
                                        gt_tracks[m], minutes, beh, p.get("id_epoch"))
            perm[m] = ident[m].pop("_perm_swap")
        ev = sum(ident[m]["n_evaluable"] for m in movies)

        def mean_of(k):
            v = [ident[m][k] for m in movies if ident[m].get(k) is not None]
            return float(np.mean(v)) if v else None
        out["identity"] = {
            "per_movie": {f"movie{m}": ident[m] for m in movies},
            "pooled": {
                "identity_accuracy": (sum(ident[m]["identity_accuracy"] * ident[m]["n_evaluable"]
                                          for m in movies if ident[m]["n_evaluable"]) / ev) if ev else None,
                **{k: mean_of(k) for k in ("switches_per_min", "error_episodes_per_min", "pct_unknown_or_missing",
                                           "identity_accuracy_per_epoch_permutation",
                                           "both_tracks_present_frac", "both_localized_frac",
                                           "both_localized_frac_close_contact",
                                           *[f"both_localized_frac_during_{b}" for b in behaviors])},
                "note": "accuracy pooled over evaluable samples; other entries are means over equal-length windows"}}
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
                bouts[t][i] = fly_bout_counts(g, pr, k, t)
            out["per_movie"][f"movie{m}"][b] = {"frame": _block(pm[i]), "bout_any": _bout_prf(bouts["any"][i]),
                                                "bout_iou0.3": _bout_prf(bouts[0.3][i])}
        f1 = prf(*pm.sum(axis=0))[2]
        neg = [i for i in range(len(movies)) if bouts["any"][i, 0] == 0]
        neg_min = sum(keep[movies[i]].sum() for i in neg) / fx.NATIVE_FPS / 60
        blk = {"frame": _block(pm.sum(axis=0)), "frame_ci95": frame_bootstrap(pm, reps),
               "bouts": {str(t): _bout_prf(bouts[t].sum(axis=0)) for t in BOUT_THRESHOLDS},
               "bout_any_ci95": bout_bootstrap(bouts["any"], reps),
               "bout_iou0.3_ci95": bout_bootstrap(bouts[0.3], reps),
               "movies_with_behavior": [m for i, m in enumerate(movies) if bouts["any"][i, 0] > 0],
               "false_positive_bouts_per_min": float((bouts["any"][:, 1] - bouts["any"][:, 2]).sum() / scored_min)
               if scored_min else None,
               "negative_movies": [movies[i] for i in neg],
               "pred_bouts_per_min_on_negative_movies": float(bouts["any"][neg, 1].sum() / neg_min)
               if neg_min else None,
               "leave_one_movie_out_f1": {f"movie{m}": prf(*np.delete(pm, i, axis=0).sum(axis=0))[2]
                                          for i, m in enumerate(movies)},
               "circular_shift_test": circular_shift_test(gt, preds, keep, b, f1, shift_reps) if shift_reps else None,
               "actor_unassigned_samples": sum(preds[m].get("unassigned_samples", {}).get(b, 0) for m in movies)}
        blk["random_reference"] = fx.random_reference({m: gt[m]["pair"][b] for m in movies}, keep, ref_reps,
                                                      dev_prior=(dev_prior or {}).get(b))
        if have_tracks:
            pf = np.zeros((len(movies), 3), np.int64)
            c2b = np.zeros_like(pf)
            pers = np.zeros_like(pf)
            has_bt = all(b in preds[m]["by_track"] for m in movies)
            for i, m in enumerate(movies):
                gf, k = gt[m]["per_fly"][b], keep[m]
                gk = gf[:, k]
                pair = preds[m]["pair"][b]

                def cnt(fp_, k=k, gk=gk):
                    pk = fp_[:, k]
                    return np.sum(gk & pk), np.sum(~gk & pk), np.sum(gk & ~pk)
                c2b[i] = cnt(np.stack([pair, pair]))
                if has_bt:
                    bt = preds[m]["by_track"][b]
                    fly_pred = bt[::-1] if perm[m] else bt  # row f = prediction for GT fly f
                    pf[i] = cnt(fly_pred)
                    t_star = int(bt[1].sum() > bt[0].sum())  # the Etho track named actor most often
                    f_star = (1 - t_star) if perm[m] else t_star
                    pp = np.zeros_like(fly_pred)
                    pp[f_star] = pair
                    pers[i] = cnt(pp)
                    out["per_movie"][f"movie{m}"][b]["per_fly_frame"] = _block(pf[i])
            blk["per_fly_baseline_copy_to_both"] = _block(c2b.sum(axis=0))
            if has_bt:
                blk["per_fly_frame"] = _block(pf.sum(axis=0))
                blk["per_fly_frame_ci95"] = frame_bootstrap(pf, reps)
                blk["per_fly_baseline_persistent_actor"] = _block(pers.sum(axis=0))
            else:
                blk["per_fly_frame"] = None
        else:
            blk["per_fly_frame"] = None
        out["behaviors"][b] = blk
    return out


def paired_diff(main_pm: dict, base_pm: dict, b: str, reps: int, seed: int = common.SEED) -> dict:
    """Pooled frame F1 (main) minus pooled frame F1 (baseline), with a paired movie bootstrap (same resamples)."""
    keys = list(main_pm)
    cm = np.array([[main_pm[k][b]["frame"][x] for x in ("tp", "fp", "fn")] for k in keys], dtype=np.int64)
    cb = np.array([[base_pm[k][b]["frame"][x] for x in ("tp", "fp", "fn")] for k in keys], dtype=np.int64)
    w = common.bootstrap_weights(len(keys), reps, seed)
    fm, fb = _vec_prf(w @ cm)[2], _vec_prf(w @ cb)[2]
    d = fm - fb
    return {"point": prf(*cm.sum(0))[2] - prf(*cb.sum(0))[2], "ci95": _pct(d),
            "undefined_frac": float(np.isnan(d).mean()), "reps": reps, "unit": "movie (paired)"}


# --------------------------------------------------------------------------- pre-registered criteria

def _bar(ref: float, rule: dict) -> float:
    return max(rule["times_ref"] * ref, ref + rule["plus_ref"])


def _rel_check(point, ci: dict, ref, rule: dict) -> dict:
    """point >= bar AND CI lower bound > lower bar (strict) AND the CI is informative."""
    bar = _bar(ref, rule["point_min"])
    lo_bar = ref if rule.get("ci_lower_gt") == "ref" else _bar(ref, rule["ci_lower_gt"])
    lo = (ci.get("f1") or [None])[0]
    ok_point = point is not None and point >= bar
    ok_ci = lo is not None and lo > lo_bar
    return {"point": point, "point_bar": bar, "ci_lower": lo, "ci_lower_bar": lo_bar, "reference": ref,
            "ci_undefined_frac": ci.get("f1_undefined_frac"), "ci_informative": bool(ci.get("informative")),
            "pass": bool(ok_point and ok_ci and ci.get("informative"))}


def evidence(metrics: dict, b: str, rule: dict) -> dict:
    """Observable original GT bouts (an `unknown` interval does not split a bout; fully excluded bouts dropped)."""
    blk = metrics["behaviors"][b]
    n_b, n_m = blk["bouts"]["any"]["n_gt"], len(blk["movies_with_behavior"])
    ok = n_b >= rule["min_gt_bouts"] and n_m >= rule["min_movies_with_behavior"]
    return {"gt_bouts_observable": n_b, "movies_with_behavior": n_m, "min_gt_bouts": rule["min_gt_bouts"],
            "min_movies_with_behavior": rule["min_movies_with_behavior"], "sufficient": ok}


def evaluate_lunge_gate(metrics: dict, crit: dict, run_ctx: dict) -> dict:
    r = crit["go_no_go"]["lunge"]
    blk = metrics["behaviors"]["lunge"]
    cov = (metrics.get("identity", {}).get("pooled") or {}).get("both_localized_frac")
    ref_f, ref_b = blk["random_reference"]["iid_expected_f1"], blk["random_reference"][
        "oracle_bout_placement_mean_bout_f1"]
    checks = {
        "setup_ok": (run_ctx["split"] == r["split"] and run_ctx["movies"] == r["movies"] and not run_ctx["partial"]
                     and run_ctx["window"] == r["window"] and run_ctx["sample_fps"] == [str(float(r["sample_fps"]))]),
        "evidence": evidence(metrics, "lunge", r["evidence"]),
        "both_localized_frac": cov, "coverage_ok": cov is not None and cov >= r["both_localized_min"],
        "bout_recall": blk["bouts"]["any"]["recall"],
        "recall_ok": blk["bouts"]["any"]["recall"] >= r["bout_recall_min"],
        "bout_f1": _rel_check(blk["bouts"]["any"]["f1"], blk["bout_any_ci95"], ref_b, r["bout_f1"]),
        "frame_f1": _rel_check(blk["frame"]["f1"], blk["frame_ci95"], ref_f, r["frame_f1"]),
        "false_positive_bouts_per_min": blk["false_positive_bouts_per_min"],
    }
    go = (checks["setup_ok"] and checks["evidence"]["sufficient"] and checks["coverage_ok"] and checks["recall_ok"]
          and checks["bout_f1"]["pass"] and checks["frame_f1"]["pass"])
    return {**checks, "decision": "GO" if go else "NO-GO"}


def evaluate_wing_gate(metrics: dict, crit: dict, run_ctx: dict, branch: str, now_utc: str | None = None) -> dict:
    r = crit["go_no_go"]["wing_threat"]
    blk = metrics["behaviors"]["wing_threat"]
    br = BRANCHES[branch]
    now = datetime.fromisoformat(now_utc) if now_utc else datetime.now(timezone.utc)
    checks = {
        "branch": branch,
        "setup_ok": (run_ctx["split"] == r["split"] and not run_ctx["partial"] and run_ctx["window"] == br["window"]
                     and run_ctx["sample_fps"] == [str(float(br["sample_fps"]))]),
        "evidence": evidence(metrics, "wing_threat", r["evidence"]),
        "frame_f1": _rel_check(blk["frame"]["f1"], blk["frame_ci95"], blk["random_reference"]["iid_expected_f1"], r),
        "scored_at_utc": now.isoformat(timespec="seconds"),
        "before_deadline": now <= datetime.fromisoformat(r["deadline_et"]),
    }
    go = checks["setup_ok"] and checks["evidence"]["sufficient"] and checks["frame_f1"]["pass"] and \
        checks["before_deadline"]
    return {**checks, "decision": "GO" if go else "NO-GO"}


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
                point, ci, ref = blk["frame"]["f1"], blk["frame_ci95"], blk["random_reference"]["iid_expected_f1"]
            else:  # bout_any_f1
                point, ci, ref = (blk["bouts"]["any"]["f1"], blk["bout_any_ci95"],
                                  blk["random_reference"]["oracle_bout_placement_mean_bout_f1"])
            res[metric] = {lvl: _rel_check(point, ci, ref, rule) for lvl, rule in levels.items()}
            if not ev["sufficient"]:
                res[metric]["verdict"] = "NOT EVALUABLE (low evidence): report the number, make no claim"
            else:
                lv = [lvl for lvl in ("good", "credible") if res[metric][lvl]["pass"]]
                res[metric]["verdict"] = f"{lv[0].upper()} MET" if lv else "NOT MET"
        shift = blk.get("circular_shift_test") or {}
        res["above_chance_wording_allowed"] = bool(shift.get("p_value") is not None and shift["p_value"] < 0.05)
        out[b] = res
    return out


# --------------------------------------------------------------------------- frozen config, decision record

def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def verify_frozen_flies_config(path: Path, manifest_config: dict, check_git: bool = True) -> dict:
    """Schema + hashes + code freeze. Returns info; raises SystemExit on any problem."""
    from eval.score_arms import verify_frozen_config

    info, cfg = verify_frozen_config(path)
    probs = list(info["mismatches"])
    for k in ("name", "frozen_at_commit", "branches", "assets"):
        if k not in cfg:
            probs.append(f"frozen config lacks {k!r}")
    if cfg.get("branches") != BRANCHES:
        probs.append(f"frozen config 'branches' must equal the frozen decision tree {BRANCHES}")
    assets = cfg.get("assets") or {}
    for role in REQUIRED_ASSET_ROLES:
        items = assets.get(role)
        if not isinstance(items, list) or not items or not all(
                isinstance(x, dict) and isinstance(x.get("path"), str) and isinstance(x.get("sha256"), str)
                for x in items):
            probs.append(f"frozen config assets.{role} must be a non-empty list of {{path, sha256}}")
    if not any(x.get("path") == DEFINITIONS_PATH for x in assets.get("definitions") or [] if isinstance(x, dict)):
        probs.append(f"frozen config assets.definitions must include {DEFINITIONS_PATH}")
    if manifest_config.get("frozen_sha256") != info["sha256"]:
        probs.append("prediction manifest config.frozen_sha256 does not match the frozen config")
    run_commit = manifest_config.get("run_commit")
    if not run_commit:
        probs.append("prediction manifest config.run_commit missing")
    dirty = [p for p in manifest_config.get("run_dirty_paths", []) or []
             if (p.startswith("etho/") and not p.startswith("etho/app/")) or p.startswith("configs/")]
    if dirty:
        probs.append(f"pipeline ran with uncommitted changes in frozen paths: {dirty[:5]}")
    if check_git and run_commit and cfg.get("frozen_at_commit"):
        rc = _git("diff", "--quiet", cfg["frozen_at_commit"], run_commit, "--", *FROZEN_TREES,
                  f":!{Path(path).as_posix()}").returncode
        if rc != 0:
            probs.append(f"git diff {cfg['frozen_at_commit'][:10]} {run_commit[:10]} -- etho/ configs/ is not "
                         "empty (or a commit is unknown)")
    if probs:
        raise SystemExit("frozen config: " + "; ".join(probs))
    return info


def load_decision(path: Path = DECISION_FILE, check_git: bool = True) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    if check_git:
        if _git("ls-files", "--error-unmatch", str(p)).returncode != 0 or \
                _git("status", "--porcelain", "--", str(p)).stdout.strip():
            raise SystemExit(f"{p} must be committed (tracked and clean) before it can be used")
    return json.loads(p.read_text())


def decide(stage: str, run_id: str, log_path: Path = common.RUNS_LOG, out: Path = DECISION_FILE) -> dict:
    """Write the decision record for a gate from its logged result (the record is then committed)."""
    recs = [json.loads(x) for x in Path(log_path).read_text().splitlines() if x.strip()]
    ok = [r for r in recs if r.get("run_id") == run_id and r.get("status") == "ok"]
    if not ok:
        raise SystemExit(f"no successful run {run_id} in {log_path}")
    r = ok[-1]
    want = {"lunge": "gate-lunge", "wing": "gate-wing"}[stage]
    if r.get("purpose") != want or r.get("kind") != LOG_KIND:
        raise SystemExit(f"run {run_id} is purpose {r.get('purpose')!r}, not {want}")
    v = (r.get("verdicts") or {}).get(stage)
    if not v:
        raise SystemExit(f"run {run_id} has no {stage} verdict")
    doc = json.loads(Path(out).read_text()) if Path(out).exists() else {"version": 1}
    entry = {"run_id": run_id, "decision": v["decision"], "verdict": v, "predictions_sha256": r["predictions_sha256"],
             "frozen_config": {"path": r["frozen_config"]["path"], "sha256": r["frozen_config"]["sha256"]},
             "criteria_sha256": r["criteria_sha256"], "git_commit": r.get("git_commit"),
             "decided_utc": common.now_utc()}
    if stage == "lunge":
        if "lunge" in doc:
            raise SystemExit("the lunge decision is already recorded")
        branch = v["decision"]
        doc.update({"lunge": entry, "branch": branch, **BRANCHES[branch], "frozen_config": entry["frozen_config"],
                    "criteria_sha256": entry["criteria_sha256"]})
    else:
        if "lunge" not in doc:
            raise SystemExit("record the lunge decision first")
        if "wing" in doc:
            raise SystemExit("the wing-threat decision is already recorded")
        if entry["frozen_config"]["sha256"] != doc["frozen_config"]["sha256"]:
            raise SystemExit("wing gate used a different frozen config than the lunge gate")
        doc["wing"] = entry
        doc["test_allowed"] = v["decision"] == "GO"
    Path(out).write_text(json.dumps(doc, indent=1) + "\n")
    return doc


# --------------------------------------------------------------------------- run accounting and guards

def prior_executions(log_path: Path, split: str, purpose: str) -> list[dict]:
    p = Path(log_path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("split") == split and rec.get("purpose") == purpose and rec.get("kind", LOG_KIND) == LOG_KIND:
                out.append(rec)
    return out


def prior_flies_test_runs(log_path: Path) -> dict[str, list[dict]]:
    return {pp: prior_executions(log_path, "flies-test", pp) for pp in ("test-1", "test-2")
            if prior_executions(log_path, "flies-test", pp)}


def _refuse_reexecution(same: list[dict], what: str) -> None:
    finals = {r["run_id"]: r for r in same if r.get("status") != "started"}
    for r in same:
        if r.get("status") != "started":
            continue
        fin = finals.get(r["run_id"])
        if fin is None or fin.get("status") != "failed" or fin.get("labels_loaded", True):
            raise SystemExit(f"{what} already executed (run {r['run_id']}); only a run that failed before loading "
                             "labels may be restarted")


def _check_plan_frozen(plan_file: Path, crit: dict | None, what: str) -> None:
    if Path(plan_file).exists() and PROPOSED_MARKER in Path(plan_file).read_text():
        raise SystemExit(f"{what}: {plan_file} still says '{PROPOSED_MARKER}' (plan not frozen)")
    if crit is None or crit.get("status") != "FROZEN":
        raise SystemExit(f"{what}: the criteria file must have status FROZEN")


def _check_clean(plan_file: Path, what: str) -> None:
    st = _git("status", "--porcelain", "--", "eval", str(plan_file), ":(exclude)eval/results").stdout.strip()
    if st:
        raise SystemExit(f"{what}: uncommitted changes; commit first:\n{st}")


def guard_flies_test(confirm: bool, purpose: str, log_path: Path, check_git: bool = True,
                     plan_file: Path = PLAN_FILE, criteria: dict | None = None) -> None:
    if not confirm:
        raise SystemExit("flies test split: pass --confirm-test-run (one of the 2 allowed flies test runs)")
    if purpose not in ("test-1", "test-2"):
        raise SystemExit("flies test split: --purpose must be test-1 or test-2")
    _check_plan_frozen(plan_file, criteria, "flies test split")
    prior = prior_flies_test_runs(log_path)
    if purpose == "test-2" and "test-1" not in prior:
        raise SystemExit("flies test split: test-2 requested but no flies test-1 is logged")
    _refuse_reexecution(prior.get(purpose, []), f"flies test split: {purpose}")
    if check_git:
        _check_clean(plan_file, "flies test split")


# --------------------------------------------------------------------------- run

def run(split: str, window: str, pred_dir: Path, behaviors=fx.SCORED, purpose: str | None = None,
        movies: list[int] | None = None, root: Path = fx.DATA_ROOT, log_path: Path = common.RUNS_LOG,
        out_dir: Path | None = Path("eval/results"), reps: int = 10_000, ref_reps: int = 200,
        criteria_path: Path | None = None, frozen_config: Path | None = None, confirm_test_run: bool = False,
        notes: str = "", check_git: bool = True, plan_file: Path = PLAN_FILE,
        data_manifest: Path = fx.MANIFEST, decision_path: Path = DECISION_FILE, shift_reps: int = SHIFT_REPS,
        now_utc: str | None = None, baselines: dict | None = None) -> dict:
    all_movies = list(fx.split_movies(split))
    if window not in fx.WINDOWS:
        raise SystemExit(f"--window must be one of {sorted(fx.WINDOWS)}")
    w = fx.WINDOWS[window]
    behaviors = tuple(behaviors)
    if set(behaviors) - set(fx.SCORED):
        raise SystemExit(f"behaviors must be a subset of {fx.SCORED}")
    crit = json.loads(Path(criteria_path).read_text()) if criteria_path else None
    is_test = split == "test"
    stage = GATE_STAGE.get(purpose or "")
    controlled = is_test or stage is not None
    log_split = f"flies-{split}"
    decision = None
    if movies:
        if controlled:
            raise SystemExit("--movies is not allowed on gate or test runs (the whole split)")
        unknown = set(movies) - set(all_movies)
        if unknown:
            raise SystemExit(f"movies {sorted(unknown)} not in {split} {all_movies}")
        all_movies = [m for m in all_movies if m in set(movies)]
    movies = all_movies
    if is_test:
        purpose = purpose or ""
        guard_flies_test(confirm_test_run, purpose, log_path, check_git, plan_file, crit)
    elif stage is not None:
        _check_plan_frozen(plan_file, crit, f"{purpose}")
        _refuse_reexecution(prior_executions(log_path, log_split, purpose), purpose)
        if check_git:
            _check_clean(plan_file, purpose)
    else:
        purpose = purpose or "tune"
        if purpose.startswith("gate") or purpose.startswith("test"):
            raise SystemExit(f"unknown controlled purpose {purpose!r}: use gate-lunge, gate-wing, test-1, test-2")
    if controlled:
        decision = load_decision(decision_path, check_git)
        if stage == "lunge":
            if decision and "lunge" in decision:
                raise SystemExit("gate-lunge: a lunge decision is already recorded")
            g = LUNGE_GATE
            if split != g["split"] or window != g["window"] or list(behaviors) != g["behaviors"]:
                raise SystemExit(f"gate-lunge must be --split dev --window mid5 with behaviors {g['behaviors']}")
        else:
            if not decision or "lunge" not in decision:
                raise SystemExit(f"{purpose}: no committed lunge decision in {decision_path}")
            br = BRANCHES[decision["branch"]]
            if window != br["window"] or list(behaviors) != br["behaviors"]:
                raise SystemExit(f"{purpose}: branch {decision['branch']} requires --window {br['window']} and "
                                 f"behaviors {br['behaviors']}")
            if stage == "wing":
                if split != "train":
                    raise SystemExit("gate-wing must be --split train")
                if "wing" in decision:
                    raise SystemExit("gate-wing: a wing-threat decision is already recorded")
            else:
                if "wing" not in decision:
                    raise SystemExit("flies test split: no committed wing-threat decision")
                if not decision.get("test_allowed"):
                    raise SystemExit("flies test split: the wing-threat gate was NO-GO, so there is no fly F1 claim "
                                     "and no fly test run (EVAL_PLAN 14.12)")
        if frozen_config is None:
            raise SystemExit(f"{purpose}: --frozen-config is required")
        if is_test:
            if Path(data_manifest).exists():
                bad = [b for b in fx.verify_manifest(data_manifest)
                       if any(f"movie{m}/" in b for m in all_movies) or "traintest" in b]
                if bad:
                    raise SystemExit("flies test split: data files differ from the manifest: " + "; ".join(bad))
            else:
                raise SystemExit(f"flies test split: data manifest {data_manifest} missing")
    pred_dir = Path(pred_dir)
    man_p = pred_dir / "manifest.json"
    manifest = json.loads(man_p.read_text()) if man_p.exists() else {}
    files = {m: pred_dir / f"movie{m}.npz" for m in movies}
    missing = [m for m, p in files.items() if not p.exists()]
    if missing:
        raise SystemExit(f"no prediction file for movies {missing} in {pred_dir}")
    preds = {m: load_prediction(files[m], w, behaviors, manifest) for m in movies}  # label-free validation
    fps = sorted({str(float(p["sample_fps"])) for p in preds.values()})
    frozen_info = None
    if controlled:
        frozen_info = verify_frozen_flies_config(frozen_config, manifest.get("config", {}), check_git)
        want_fps = LUNGE_GATE["sample_fps"] if stage == "lunge" else BRANCHES[decision["branch"]]["sample_fps"]
        if fps != [str(float(want_fps))]:
            raise SystemExit(f"{purpose}: predictions at {fps} fps, the decision tree requires {want_fps}")
        if decision and decision["frozen_config"]["sha256"] != frozen_info["sha256"]:
            raise SystemExit(f"{purpose}: frozen config differs from the one recorded at the lunge gate")
    per_file = {f"movie{m}.npz": common.sha256_file(p) for m, p in files.items()}
    if man_p.exists():
        per_file["manifest.json"] = common.sha256_file(man_p)
    base_preds, base_hash = {}, {}
    for name, d in (baselines or {}).items():  # report-only arms, validated and hashed before any label is read
        bfiles = {m: Path(d) / f"movie{m}.npz" for m in movies}
        miss = [m for m, q in bfiles.items() if not q.exists()]
        if miss:
            raise SystemExit(f"baseline {name}: no prediction file for movies {miss} in {d}")
        base_preds[name] = {m: load_prediction(bfiles[m], w, behaviors) for m in movies}
        base_hash[name] = {"dir": str(d), "files_sha256": {f"movie{m}.npz": common.sha256_file(q)
                                                           for m, q in bfiles.items()}}
    run_id = common.new_run_id(log_split)
    config = manifest.get("config", {})
    base = {"run_id": run_id, "kind": LOG_KIND, "dataset": "flyvsfly", "split": log_split,
            "purpose": purpose, **common.git_info(), "window": window, "window_frames": list(w),
            "behaviors": list(behaviors), "movies": movies, "partial": movies != list(fx.split_movies(split)),
            "pred_dir": str(pred_dir), "predictions_sha256": common.sha256_json(per_file),
            "predictions_files_sha256": per_file, "sample_fps": fps, "config": config,
            "config_sha256": common.sha256_json(config), "frozen_config": frozen_info,
            "decision_record_sha256": common.sha256_file(Path(decision_path)) if decision else None,
            "baselines": base_hash or None,
            "criteria_file": str(criteria_path) if criteria_path else None,
            "criteria_sha256": common.sha256_file(Path(criteria_path)) if criteria_path else None,
            "scorer": {"reps": reps, "ref_reps": ref_reps, "shift_reps": shift_reps, "seed": common.SEED,
                       "bout_thresholds": [str(t) for t in BOUT_THRESHOLDS], "bout_matching":
                       "censored maximum-cardinality one-to-one, IoU tie-break"},
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
        dev_lab = {m: (gt[m] if m in gt else fx.load_window_labels(m, window, root, behaviors=behaviors))
                   for m in fx.SPLITS["dev"]}
        dev_prior = {b: fx.dev_prevalence(dev_lab, b) for b in behaviors}
        if all("track_xy" in preds[m] for m in movies):
            tracks = {m: fx.load_gt_tracks(m, root, allow_test=is_test) for m in movies}
        minutes = (w[1] - w[0]) / fx.NATIVE_FPS / 60
        metrics = score_movies(gt, preds, behaviors, reps, tracks or None, minutes, ref_reps, dev_prior, shift_reps)
        if base_preds:
            metrics["baselines"] = {}
            for name, bp in base_preds.items():
                bm = score_movies(gt, bp, behaviors, reps, None, minutes, 5, dev_prior, 0)
                metrics["baselines"][name] = {b: {k: bm["behaviors"][b][k] for k in (
                    "frame", "frame_ci95", "bouts", "bout_any_ci95", "false_positive_bouts_per_min",
                    "pred_bouts_per_min_on_negative_movies")} for b in behaviors}
                for b in behaviors:
                    metrics["baselines"][name][b]["paired_diff_frame_f1_main_minus_baseline"] = paired_diff(
                        metrics["per_movie"], bm["per_movie"], b, reps)
        if is_test:
            metrics["inter_annotator"] = inter_annotator(movies, window, behaviors, root, gt)
        ctx = {"split": split, "movies": movies, "partial": base["partial"], "window": window, "sample_fps": fps}
        verdicts = None
        if crit is not None:
            if is_test:
                verdicts = evaluate_test_targets(metrics, crit)
            elif stage == "lunge" or (stage is None and "lunge" in behaviors and split == "dev"):
                verdicts = {"lunge": evaluate_lunge_gate(metrics, crit, ctx)}
                if stage is None:
                    verdicts["note"] = "informational: not a gate execution"
            elif stage == "wing" or (stage is None and split == "train"):
                branch = decision["branch"] if decision else ("GO" if window == "mid5" else "NO-GO")
                verdicts = {"wing": evaluate_wing_gate(metrics, crit, ctx, branch, now_utc)}
                if stage is None:
                    verdicts["note"] = "informational: not a gate execution"
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
               "scorer_wall_clock_s": round(time.time() - t0, 3), "verdicts": verdicts}
        if is_test and out_dir is not None:
            sealed = {"per_movie": metrics.pop("per_movie"),
                      "identity_per_movie": metrics.get("identity", {}).pop("per_movie", None),
                      "leave_one_movie_out_f1": {b: metrics["behaviors"][b].pop("leave_one_movie_out_f1")
                                                 for b in behaviors}}
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
    """Second annotation (movie6_second_actions.mat) scored against the primary one, pair-level, same window.
    The primary annotation (`movieN_actions.mat`) is the reference, fixed in advance and never switched."""
    out = {}
    for m in movies:
        if "second_actions" not in fx.movie_paths(m, root):
            continue
        try:
            sec = fx.load_window_labels(m, window, root, allow_test=True, behaviors=behaviors, second=True)
            k = ~(gt[m]["excluded"] | sec["excluded"])
            res = {"coverage": {"second_annotation_positive_frames": {b: int(sec["pair"][b].sum())
                                                                      for b in behaviors}}}
            for b in behaviors:
                g, p = gt[m]["pair"][b][k], sec["pair"][b][k]
                res[b] = {"frame": _block((np.sum(g & p), np.sum(~g & p), np.sum(g & ~p))),
                          "bout_any": _bout_prf(fly_bout_counts(gt[m]["pair"][b], sec["pair"][b], k, "any"))}
            out[f"movie{m}"] = {"reference": "primary annotation", "compared": "second annotation", **res}
        except Exception as e:  # never fail the test run over the reference
            out[f"movie{m}"] = {"error": repr(e)}
    return out


def format_report(rec: dict) -> str:
    m = rec["metrics"]
    L = [f"run {rec['run_id']} split={rec['split']} window={rec['window']} purpose={rec['purpose']}"
         + (" PARTIAL" if rec["partial"] else ""),
         f"movies {rec['movies']}; {m['n_frames_scored']} frames scored, {m['n_frames_excluded']} excluded (unknown)",
         "", f"{'behavior':<12}{'P':>7}{'R':>7}{'F1':>7}  {'F1 95% CI (movie bootstrap)':<28}{'r(iid)':>7}"
             f"{'allpos':>7}{'bout R':>8}{'bout P':>8}{'bout F1':>8}{'r_b':>7}{'FP/min':>8}"]
    for b, x in m["behaviors"].items():
        f, ci, rr, bt = x["frame"], x["frame_ci95"]["f1"], x["random_reference"], x["bouts"]["any"]
        cis = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "n/a"
        L.append(f"{b:<12}{f['precision']:>7.3f}{f['recall']:>7.3f}{f['f1']:>7.3f}  {cis:<28}"
                 f"{rr['iid_expected_f1']:>7.3f}{rr['always_positive_f1']:>7.3f}{bt['recall']:>8.3f}"
                 f"{bt['precision']:>8.3f}{bt['f1']:>8.3f}{rr['oracle_bout_placement_mean_bout_f1']:>7.3f}"
                 f"{x['false_positive_bouts_per_min']:>8.2f}")
        cs = x.get("circular_shift_test")
        if cs:
            L.append(f"{'':<12}circular-shift mean F1 {cs['mean_f1']:.3f}, p = {cs['p_value']:.4f}; "
                     f"CI undefined frac {x['frame_ci95']['f1_undefined_frac']:.3f}")
        if x.get("per_fly_frame"):
            L.append(f"{'':<12}per-fly F1 {x['per_fly_frame']['f1']:.3f} (copy-to-both "
                     f"{x['per_fly_baseline_copy_to_both']['f1']:.3f}, persistent actor "
                     f"{x['per_fly_baseline_persistent_actor']['f1']:.3f})")
    idp = m.get("identity", {}).get("pooled")
    if idp:
        L.append(f"identity: accuracy {idp['identity_accuracy']}, switches/min {idp['switches_per_min']:.2f}, "
                 f"episodes/min {idp['error_episodes_per_min']:.2f}, unknown/missing "
                 f"{idp['pct_unknown_or_missing']:.3f}, both localized {idp['both_localized_frac']:.3f}")
    if rec.get("verdicts"):
        L += ["", "verdicts: " + json.dumps(rec["verdicts"], indent=1, default=str)]
    return "\n".join(L)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "decide":
        ap = argparse.ArgumentParser(prog="flies_score decide")
        ap.add_argument("--stage", required=True, choices=("lunge", "wing"))
        ap.add_argument("--run-id", required=True)
        ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
        ap.add_argument("--out", type=Path, default=DECISION_FILE)
        a = ap.parse_args(argv[1:])
        doc = decide(a.stage, a.run_id, a.log, a.out)
        print(json.dumps({k: v for k, v in doc.items() if k not in ("lunge", "wing")}, indent=1))
        print(f"wrote {a.out}: commit it before the next gate / test run")
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=sorted(fx.SPLITS))
    ap.add_argument("--window", required=True, choices=sorted(fx.WINDOWS))
    ap.add_argument("--pred-dir", required=True, type=Path)
    ap.add_argument("--behaviors", default=",".join(fx.SCORED))
    ap.add_argument("--purpose", help="tune (default), ablation:<name>, baseline:<name>; gate-lunge, gate-wing; "
                                      "test-1|test-2")
    ap.add_argument("--movies", help="comma-separated subset (free runs only)")
    ap.add_argument("--root", type=Path, default=fx.DATA_ROOT)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--out-dir", type=Path, default=Path("eval/results"))
    ap.add_argument("--reps", type=int, default=10_000)
    ap.add_argument("--criteria", type=Path, default=CRITERIA_FILE)
    ap.add_argument("--frozen-config", type=Path)
    ap.add_argument("--confirm-test-run", action="store_true")
    ap.add_argument("--notes", default="")
    ap.add_argument("--baseline", action="append", default=[], help="name=dir, report-only arm (repeatable)")
    a = ap.parse_args(argv)
    bl = dict(x.split("=", 1) for x in a.baseline)
    rec = run(a.split, a.window, a.pred_dir, tuple(a.behaviors.split(",")), a.purpose,
              [int(x) for x in a.movies.split(",")] if a.movies else None, a.root, a.log, a.out_dir, a.reps,
              criteria_path=a.criteria, frozen_config=a.frozen_config, confirm_test_run=a.confirm_test_run,
              notes=a.notes, baselines=bl or None)
    print(format_report(rec))
    print(f"\nlogged to {a.log}; full result {a.out_dir}/{rec['run_id']}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
