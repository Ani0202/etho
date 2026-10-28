"""Identity validation of a tracking output against the CalMS21 pose keypoints (EVAL_PLAN section 4.3).

The keypoints are MARS pose-estimator output, not human ground truth, so every number here is
agreement with a reference, not accuracy against truth.

Tracking input (`--tracks-dir`), one file per sequence, `<seq>.npz` (or `.parquet` if pandas+pyarrow
are installed), one row per (frame, track):
  frame     int    native 30 fps frame index
  track_id  int
  label     str    resident | intruder | unknown
  cx, cy    float  mask centroid, pixels (1024x570 frame)
  area      float  mask area, pixels
  present   bool   rows with present=False are ignored
Frames are evaluated on the sample grid 0, step, 2*step, ... (step = 3 for 10 fps). A grid frame with
no present row counts as "no track", so missing output lowers coverage instead of being skipped.

Matching, per grid frame, is **label-blind**: present tracks are matched one-to-one to the two keypoint
mice by position only (maximize the number of matches with distance <= gate, then minimize total
distance), and only then is each matched track's label compared with the mouse it sits on (mouse 0 =
resident/black, mouse 1 = intruder/white). A match is **ambiguous** when moving its track(s) to the
other mouse is also inside the gate and costs less than `margin_px` more; this happens when the
centroids nearly coincide (mount, tight contact). Error rates are reported on all matches and on
unambiguous matches.

Keypoint centroid: mean of the 7 CalMS21 keypoints (nose, ears, neck, hips, tail base) by default.
The tail base is a point on the body (the rump), not on the tail; the tail itself has no keypoint.
The 7-point mean sits about 46% of the way from nose to tail base, and dropping the tail base moves it
to about 37%, toward the head, while a mask's area centroid should sit near the middle of the body.
So we keep the tail base. The resident's implant and cable pull its mask centroid toward the head,
which may favor `no_tail` for the resident: `centroid_offsets()` measures this on dev and the choice
is recorded, not assumed. (The spike's "biased by tails" note concerned bounding-box centers, where
the tail does count.)

Usage: uv run python -m eval.identity --split dev --tracks-dir <dir> [--centroid all7] [--gate-px 60]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from eval import common
from eval.common import NATIVE_FPS, VOCAB

LABELS = ("resident", "intruder", "unknown")
ROLE_OF_MOUSE = ("resident", "intruder")  # CalMS21 keypoint mouse 0 is the black resident
BEHAVIOR_NAMES = {v: k for k, v in VOCAB.items()}
CENTROIDS = {
    "all7": list(range(7)),
    "no_tail": list(range(6)),
    "torso": [3, 4, 5, 6],  # neck, hips, tail base
}
DEFAULTS = {"gate_px": 60.0, "margin_px": 10.0, "min_kp_score": 0.5, "step": 3, "centroid": "all7",
            "lags": list(range(-10, 11)), "min_speed_px": 1.0}


# --------------------------------------------------------------------------- loading

def read_tracks(path: Path) -> dict:
    path = Path(path)
    if path.suffix == ".npz":
        d = np.load(path, allow_pickle=False)
        t = {k: np.asarray(d[k]) for k in ("frame", "track_id", "label", "cx", "cy", "area", "present")}
    elif path.suffix == ".parquet":
        try:
            import pandas as pd
        except ImportError as e:
            raise SystemExit("parquet tracks need pandas + pyarrow (not installed); write npz instead") from e
        df = pd.read_parquet(path)
        t = {k: df[k].to_numpy() for k in ("frame", "track_id", "label", "cx", "cy", "area", "present")}
    else:
        raise ValueError(f"unsupported tracks file {path}")
    t["label"] = t["label"].astype(str)
    bad = set(np.unique(t["label"])) - set(LABELS)
    if bad:
        raise ValueError(f"{path.name}: labels {sorted(bad)} not in {LABELS}")
    t["frame"] = t["frame"].astype(np.int64)
    t["track_id"] = t["track_id"].astype(np.int64)
    t["present"] = t["present"].astype(bool)
    for k in ("cx", "cy", "area"):
        t[k] = t[k].astype(np.float64)
    keep = t["present"] & np.isfinite(t["cx"]) & np.isfinite(t["cy"])
    return {k: v[keep] for k, v in t.items()}


def find_tracks_file(tracks_dir: Path, seq: str) -> Path | None:
    for stem in (seq, seq.split("_task1")[0]):
        for ext in (".npz", ".parquet"):
            p = Path(tracks_dir) / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def keypoint_centroids(kp: np.ndarray, scores: np.ndarray, definition: str = "all7",
                       min_score: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """kp [T, 2 mice, 2 (x, y), 7], scores [T, 2, 7] -> centroids [T, 2, 2], eligible [T, 2].

    A mouse is eligible on a frame if its mean keypoint score is >= min_score (EVAL_PLAN 4.3).
    """
    idx = CENTROIDS[definition]
    cen = kp[:, :, :, idx].mean(axis=3)
    elig = (scores.mean(axis=2) >= min_score) & np.all(np.isfinite(cen), axis=2)
    return cen, elig


# --------------------------------------------------------------------------- matching

def match_frame(tr_xy: np.ndarray, kp_xy: np.ndarray, kp_ok: np.ndarray, gate: float, margin: float):
    """One-to-one gated matching of tracks to the 2 keypoint mice, by position only.

    tr_xy [n, 2]; kp_xy [2, 2]; kp_ok [2] bool. Returns (assign [2] track index or -1, dist [2],
    ambiguous [2] bool). Maximizes the number of matches, then minimizes total distance.
    """
    n = len(tr_xy)
    assign, dist, amb = np.full(2, -1), np.full(2, np.nan), np.zeros(2, bool)
    if n == 0 or not kp_ok.any():
        return assign, dist, amb
    d = np.linalg.norm(tr_xy[:, None, :] - kp_xy[None, :, :], axis=2)  # [n, 2]
    d[:, ~kp_ok] = np.inf
    best, best_key = None, None
    for a0, a1 in itertools.product(range(-1, n), repeat=2):
        if a0 >= 0 and a0 == a1:
            continue
        pair = (a0, a1)
        if any(a >= 0 and d[a, m] > gate for m, a in enumerate(pair)):
            continue
        k = sum(a >= 0 for a in pair)
        cost = sum(d[a, m] for m, a in enumerate(pair) if a >= 0)
        key = (-k, cost)
        if best_key is None or key < best_key:
            best, best_key = pair, key
    if best is None or best_key[0] == 0:
        return assign, dist, amb
    assign = np.array(best)
    for m in range(2):
        if assign[m] >= 0:
            dist[m] = d[assign[m], m]
    # Ambiguity: the swapped assignment (each matched track moved to the other mouse) is also gated
    # and costs < margin more.
    swapped = (assign[1], assign[0])
    sw_cost, sw_ok = 0.0, True
    for m, a in enumerate(swapped):
        if a >= 0:
            if not kp_ok[m] or d[a, m] > gate:
                sw_ok = False
                break
            sw_cost += d[a, m]
    if sw_ok and sw_cost - best_key[1] < margin:
        amb[assign >= 0] = True
    return assign, dist, amb


# --------------------------------------------------------------------------- per sequence

def evaluate_sequence(tracks: dict, kp: np.ndarray, scores: np.ndarray, gt_labels: np.ndarray,
                      gate_px: float = 60.0, margin_px: float = 10.0, min_kp_score: float = 0.5,
                      step: int = 3, centroid: str = "all7") -> dict:
    """Per-sample identity arrays for one sequence (grid frames 0, step, ...)."""
    n_frames = len(gt_labels)
    grid = np.arange(0, n_frames, step)
    cen, elig = keypoint_centroids(kp, scores, centroid, min_kp_score)
    on_grid = (tracks["frame"] % step == 0) & (tracks["frame"] >= 0) & (tracks["frame"] < n_frames)
    order = np.argsort(tracks["frame"], kind="stable")
    fr = tracks["frame"][order]
    G = len(grid)
    out = {
        "frame": grid, "behavior": gt_labels[grid],
        "n_present": np.zeros(G, int), "has_res": np.zeros(G, bool), "has_int": np.zeros(G, bool),
        "n_unknown": np.zeros(G, int), "kp_ok": elig[grid],
        "match_label": np.full((G, 2), "", dtype="<U8"), "match_dist": np.full((G, 2), np.nan),
        "ambiguous": np.zeros((G, 2), bool), "match_track": np.full((G, 2), -1),
    }
    starts = np.searchsorted(fr, grid, "left")
    ends = np.searchsorted(fr, grid, "right")
    for gi, f in enumerate(grid):
        rows = order[starts[gi]:ends[gi]]
        if len(rows) == 0:
            continue
        lab = tracks["label"][rows]
        out["n_present"][gi] = len(rows)
        out["has_res"][gi] = np.any(lab == "resident")
        out["has_int"][gi] = np.any(lab == "intruder")
        out["n_unknown"][gi] = int(np.sum(lab == "unknown"))
        xy = np.stack([tracks["cx"][rows], tracks["cy"][rows]], axis=1)
        assign, dist, amb = match_frame(xy, cen[f], elig[f], gate_px, margin_px)
        for m in range(2):
            if assign[m] >= 0:
                out["match_label"][gi, m] = lab[assign[m]]
                out["match_track"][gi, m] = tracks["track_id"][rows[assign[m]]]
        out["match_dist"][gi] = dist
        out["ambiguous"][gi] = amb
    out["n_offgrid_rows"] = int(np.sum(~on_grid))
    out["n_frames"] = n_frames
    return out


def swap_events(match_label: np.ndarray, usable: np.ndarray) -> tuple[int, list[int]]:
    """Label changes for the same physical (keypoint) mouse over time.

    match_label [G, 2] labels of the track matched to each mouse; usable [G, 2] bool. Unknown and
    unmatched samples are skipped (a resident -> unknown -> intruder sequence is one change). Returns
    (events, per_mouse_changes), where an event is a grid sample at which at least one mouse's known
    label differs from that mouse's previous known label, so a full identity swap counts once.
    A single mislabeled sample in a long run produces 2 events (flip and flip back).
    """
    changed = np.zeros(len(match_label), bool)
    per_mouse = []
    for m in range(2):
        last, n = None, 0
        for gi in range(len(match_label)):
            lab = match_label[gi, m]
            if not usable[gi, m] or lab not in ("resident", "intruder"):
                continue
            if last is not None and lab != last:
                n += 1
                changed[gi] = True
            last = lab
        per_mouse.append(n)
    return int(changed.sum()), per_mouse


RETAINED_OUTPUT_NOTE = {
    "metrics": ["unknown_rate_tracks", "detections", "coverage_both_labeled"],
    "note": "Retained-output diagnostics (EVAL_PLAN 4.3, amendment A1): computed on the exported tracks table, "
            "which keeps only the resident/intruder assignments the pipeline retained. Unknown-labeled and extra "
            "raw SAM detections are not exported, so unknown_rate_tracks and detections['3+'] are zero by "
            "construction on that export, and coverage counts retained labels only. They describe the exported "
            "output, not raw SAM detection quality."}


def summarize(per: list[dict]) -> dict:
    """Pool per-sequence arrays into the reported metrics (overall and per GT behavior)."""
    cat = {k: np.concatenate([p[k] for p in per]) for k in
           ("behavior", "n_present", "has_res", "has_int", "n_unknown", "kp_ok", "match_label",
            "match_dist", "ambiguous")}
    minutes = sum(p["n_frames"] for p in per) / NATIVE_FPS / 60

    def block(sel: np.ndarray) -> dict:
        G = int(sel.sum())
        ml, amb, md = cat["match_label"][sel], cat["ambiguous"][sel], cat["match_dist"][sel]
        expected = np.array(ROLE_OF_MOUSE)[None, :]
        matched = ml != ""
        known = np.isin(ml, ("resident", "intruder"))
        wrong = known & (ml != expected)
        unamb = known & ~amb
        both_correct = np.all(known & ~wrong, axis=1)
        both_elig = np.all(cat["kp_ok"][sel], axis=1)
        n_tracks = int(cat["n_present"][sel].sum())
        r = {
            "n_samples": G,
            "coverage_both_labeled": float(np.mean(cat["has_res"][sel] & cat["has_int"][sel])) if G else None,
            # denominator: samples where both keypoint mice are eligible (else MARS confidence, not
            # tracking, would cap it: on dev the resident is ineligible on ~16% of frames)
            "coverage_both_matched_correct": float(both_correct[both_elig].mean()) if both_elig.any() else None,
            "kp_both_eligible_rate": float(both_elig.mean()) if G else None,
            "detections": {k: float(np.mean(v)) if G else None for k, v in
                           (("0", cat["n_present"][sel] == 0), ("1", cat["n_present"][sel] == 1),
                            ("2", cat["n_present"][sel] == 2), ("3+", cat["n_present"][sel] >= 3))},
            "unknown_rate_tracks": float(cat["n_unknown"][sel].sum() / n_tracks) if n_tracks else None,
            "kp_eligible_rate": [float(cat["kp_ok"][sel][:, m].mean()) if G else None for m in range(2)],
            "matched_mouse_samples": int(matched.sum()),
            "matched_unknown": int((matched & ~known).sum()),
            "identity_eval_samples": int(known.sum()),
            "identity_errors": int(wrong.sum()),
            "identity_error_rate": float(wrong.sum() / known.sum()) if known.sum() else None,
            "ambiguous_frac_of_evaluated": float((known & amb).sum() / known.sum()) if known.sum() else None,
            "identity_error_rate_unambiguous": float((wrong & ~amb).sum() / unamb.sum()) if unamb.sum() else None,
            "identity_errors_by_mouse": {ROLE_OF_MOUSE[m]: {
                "errors": int(wrong[:, m].sum()), "evaluated": int(known[:, m].sum())} for m in range(2)},
            "median_match_dist_px": {ROLE_OF_MOUSE[m]: (float(np.nanmedian(md[:, m])) if matched[:, m].any()
                                                        else None) for m in range(2)},
        }
        return r

    overall = block(np.ones(len(cat["behavior"]), bool))
    by_beh = {BEHAVIOR_NAMES[c]: block(cat["behavior"] == c) for c in range(4)}
    ev_all = [swap_events(p["match_label"], p["match_label"] != "") for p in per]
    ev_un = [swap_events(p["match_label"], ~p["ambiguous"]) for p in per]
    overall["swap_events"] = int(sum(e for e, _ in ev_all))
    overall["swap_events_per_min"] = overall["swap_events"] / minutes if minutes else None
    overall["swap_events_unambiguous"] = int(sum(e for e, _ in ev_un))
    overall["swap_events_unambiguous_per_min"] = overall["swap_events_unambiguous"] / minutes if minutes else None
    overall["video_minutes"] = minutes
    return {"overall": overall, "by_behavior": by_beh}


# --------------------------------------------------------------------------- alignment check

def alignment_check(tracks: dict, kp: np.ndarray, scores: np.ndarray, lags=range(-10, 11),
                    centroid: str = "all7", min_kp_score: float = 0.5, min_speed_px: float = 1.0) -> dict:
    """Lag L (source frames) that best aligns track centroids with keypoint centroids, per animal.

    For each lag, the distance between the track labeled `resident` (`intruder`) at video frame f and
    keypoint mouse 0 (1) at label frame f + L, median over frames where exactly one such track exists,
    the mouse is eligible, and the mouse moves >= min_speed_px per frame (so stationary stretches,
    which match at every lag, don't flatten the curve). Best lag L > 0 means label index = video
    index + L. `margin_px` = second-best median minus best median (small margin = weak evidence).
    """
    cen, elig = keypoint_centroids(kp, scores, centroid, min_kp_score)
    T = len(cen)
    speed = np.zeros((T, 2))
    speed[1:-1] = np.linalg.norm(cen[2:] - cen[:-2], axis=2) / 2
    lags = list(lags)
    out = {"lags": lags, "sign": "best lag L: track at video frame f matches keypoints at frame f+L"}
    for m, role in enumerate(ROLE_OF_MOUSE):
        sel = tracks["label"] == role
        fr, xs, ys = tracks["frame"][sel], tracks["cx"][sel], tracks["cy"][sel]
        uniq, cnt = np.unique(fr, return_counts=True)
        single = np.isin(fr, uniq[cnt == 1]) & (fr >= 0) & (fr < T)
        fr, xy = fr[single], np.stack([xs[single], ys[single]], axis=1)
        med, n_used = [], []
        for L in lags:
            g = fr + L
            ok = (g >= 0) & (g < T)
            ok[ok] &= elig[g[ok], m] & (speed[g[ok], m] >= min_speed_px)
            dd = np.linalg.norm(xy[ok] - cen[g[ok], m], axis=1)
            med.append(float(np.median(dd)) if len(dd) else None)
            n_used.append(int(ok.sum()))
        valid = [(v, L) for v, L in zip(med, lags, strict=True) if v is not None]
        if valid:
            srt = sorted(valid)
            best_v, best_L = srt[0]
            r = {"best_lag": best_L, "best_median_px": best_v,
                 "lag0_median_px": med[lags.index(0)] if 0 in lags else None,
                 "margin_px": (srt[1][0] - best_v) if len(srt) > 1 else None}
        else:
            r = {"best_lag": None}
        out[role] = {**r, "median_px_by_lag": med, "n_used_by_lag": n_used}
    return out


def centroid_offsets(tracks: dict, kp: np.ndarray, scores: np.ndarray, min_kp_score: float = 0.5) -> dict:
    """Median distance from each labeled track to its keypoint mouse, per centroid definition (dev only:
    used to choose the definition empirically). Frames with exactly one track of the role."""
    out = {}
    for name in CENTROIDS:
        cen, elig = keypoint_centroids(kp, scores, name, min_kp_score)
        out[name] = {}
        for m, role in enumerate(ROLE_OF_MOUSE):
            sel = tracks["label"] == role
            fr = tracks["frame"][sel]
            uniq, cnt = np.unique(fr, return_counts=True)
            ok = np.isin(fr, uniq[cnt == 1]) & (fr >= 0) & (fr < len(cen))
            ok[ok] &= elig[fr[ok], m]
            xy = np.stack([tracks["cx"][sel][ok], tracks["cy"][sel][ok]], axis=1)
            dd = np.linalg.norm(xy - cen[fr[ok], m], axis=1)
            out[name][role] = float(np.median(dd)) if len(dd) else None
    return out


# --------------------------------------------------------------------------- CLI

def run(split: str, tracks_dir: Path, purpose: str | None = None, labels_dir: Path = common.LABELS_DIR,
        split_file: Path = common.SPLIT_FILE, log_path: Path = common.RUNS_LOG, out_dir: Path | None = None,
        seqs: list[str] | None = None, confirm_test_run: bool = False, notes: str = "", **params) -> dict:
    split = common.canonical_split(split)
    p = {**DEFAULTS, **{k: v for k, v in params.items() if v is not None}}
    purpose = purpose or {"dev": "identity-check", "dev_holdout": "holdout-check"}.get(split)
    common.guard_test_run(split, confirm_test_run, purpose or "", "identity", log_path)
    if split == "test" and seqs:
        raise SystemExit("test split: --seqs not allowed")
    all_seqs, _ = common.load_split(split, split_file)
    if seqs:
        unknown = set(seqs) - set(all_seqs)
        if unknown:
            raise SystemExit(f"sequences not in {split}: {sorted(unknown)}")
    seqs = [s for s in all_seqs if not seqs or s in set(seqs)]
    files = {s: find_tracks_file(tracks_dir, s) for s in seqs}
    missing = [s for s, f in files.items() if f is None]
    hashes = {s: common.sha256_file(f) for s, f in files.items() if f is not None}
    run_id = common.new_run_id(f"identity-{split}")
    base = {"run_id": run_id, "kind": "identity", "split": split, "purpose": purpose, **common.git_info(),
            "split_file": str(split_file), "split_file_sha256": common.sha256_file(Path(split_file)),
            "sequences": seqs, "partial": seqs != all_seqs, "missing_tracks": missing,
            "tracks_dir": str(tracks_dir), "tracks_sha256": common.sha256_json(hashes),
            "tracks_files_sha256": hashes, "params": p, "notes": notes}
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, log_path)
    t0, labels_loaded = time.time(), False
    try:
        if missing:
            raise ValueError(f"no tracks file for {missing} (use --seqs to evaluate a subset)")
        per, align, offsets, per_seq = [], {}, {}, {}
        for s in seqs:
            tr = read_tracks(files[s])
            labels_loaded = True
            d = common.load_sequence(split, s, labels_dir)
            r = evaluate_sequence(tr, d["keypoints"], d["scores"], d["labels"], p["gate_px"], p["margin_px"],
                                  p["min_kp_score"], p["step"], p["centroid"])
            per.append(r)
            per_seq[s] = summarize([r])["overall"]
            per_seq[s]["n_offgrid_rows"] = r["n_offgrid_rows"]
            align[s] = alignment_check(tr, d["keypoints"], d["scores"], p["lags"], p["centroid"],
                                       p["min_kp_score"], p["min_speed_px"])
            if split != "test":
                offsets[s] = centroid_offsets(tr, d["keypoints"], d["scores"], p["min_kp_score"])
        metrics = {**summarize(per), "per_sequence": per_seq, "alignment": align,
                   "retained_output_diagnostics": RETAINED_OUTPUT_NOTE}
        if offsets:
            metrics["centroid_definition_median_px"] = offsets
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
               "metrics": metrics, "scorer_wall_clock_s": round(time.time() - t0, 3)}
    except Exception as e:
        rec = {**base, "time_utc": common.now_utc(), "status": "failed", "labels_loaded": labels_loaded,
               "error": repr(e), "traceback": traceback.format_exc(limit=5)}
        common.append_run(rec, log_path)
        raise
    common.append_run(rec, log_path)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"{run_id}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def _f(x, fmt=".4f"):
    return "n/a" if x is None else format(x, fmt)


def format_report(rec: dict) -> str:
    m = rec["metrics"]
    o = m["overall"]
    lines = [f"run {rec['run_id']} split={rec['split']}" + (" PARTIAL" if rec["partial"] else ""),
             f"{o['n_samples']} grid samples, {o['video_minutes']:.2f} video min; params {rec['params']}",
             "", f"{'':<14}{'id err':>8}{'id err unamb':>13}{'ambig':>7}{'cov both lab':>13}"
                 f"{'cov both ok':>12}{'unknown*':>9}{'n eval':>8}"]
    for name, b in [("overall", o)] + list(m["by_behavior"].items()):
        lines.append(f"{name:<14}{_f(b['identity_error_rate']):>8}{_f(b['identity_error_rate_unambiguous']):>13}"
                     f"{_f(b['ambiguous_frac_of_evaluated'], '.3f'):>7}{_f(b['coverage_both_labeled']):>13}"
                     f"{_f(b['coverage_both_matched_correct']):>12}{_f(b['unknown_rate_tracks']):>9}"
                     f"{b['identity_eval_samples']:>8}")
    lines.append("* 'cov both lab', 'unknown', and detections are retained-output diagnostics: the exported tracks "
                 "hold only retained resident/intruder labels, so unknown and 3+ detections are 0 by construction")
    lines.append(f"\nswap events: {o['swap_events']} ({_f(o['swap_events_per_min'], '.3f')}/min); "
                 f"unambiguous only: {o['swap_events_unambiguous']} "
                 f"({_f(o['swap_events_unambiguous_per_min'], '.3f')}/min)")
    lines.append("alignment (best lag, median px at best / at 0, margin):")
    for s, a in m["alignment"].items():
        cells = [f"{r}: L={a[r].get('best_lag')} {_f(a[r].get('best_median_px'), '.1f')}/"
                 f"{_f(a[r].get('lag0_median_px'), '.1f')} +{_f(a[r].get('margin_px'), '.1f')}"
                 for r in ROLE_OF_MOUSE]
        lines.append(f"  {s:<28}" + "   ".join(cells))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True)
    ap.add_argument("--tracks-dir", required=True, type=Path)
    ap.add_argument("--purpose")
    ap.add_argument("--centroid", choices=sorted(CENTROIDS))
    ap.add_argument("--gate-px", type=float)
    ap.add_argument("--margin-px", type=float)
    ap.add_argument("--min-kp-score", type=float)
    ap.add_argument("--step", type=int, help="grid step in source frames (3 = 10 fps)")
    ap.add_argument("--labels-dir", type=Path, default=common.LABELS_DIR)
    ap.add_argument("--split-file", type=Path, default=common.SPLIT_FILE)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--out-dir", type=Path, default=Path("eval/results"))
    ap.add_argument("--seqs")
    ap.add_argument("--confirm-test-run", action="store_true")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    rec = run(a.split, a.tracks_dir, a.purpose, a.labels_dir, a.split_file, a.log, a.out_dir,
              a.seqs.split(",") if a.seqs else None, a.confirm_test_run, a.notes, centroid=a.centroid,
              gate_px=a.gate_px, margin_px=a.margin_px, min_kp_score=a.min_kp_score, step=a.step)
    print(format_report(rec))
    print(f"\nlogged to {a.log}; full result {a.out_dir}/{rec['run_id']}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
