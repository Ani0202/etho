"""Frame-level scorer for CalMS21 Task 1 (docs/EVAL_PLAN.md sections 3, 4.1, 4.2, 9).

Convention (CalMS21 paper, arXiv:2104.02710 v4, Section E): per-behavior frame-level precision /
recall / F1 one-vs-rest at 30 fps, **pooled over all frames of all sequences of the split** ("computed
on the entire test set with all the videos concatenated into one sequence"), and macro F1 = unweighted
mean over attack, investigation, mount ("omitting the 'other' category").

Prediction directory (`--pred-dir`):
  manifest.json (optional)   {"sample_fps": 10, "config": {...}, "cost": {...}, "wall_clock_s": {...},
                              "calls": {...}, "notes": "..."}; copied into the run log.
  <seq>.jsonl                one bout per line: {"behavior": "attack", "start_frame": 120, "end_frame": 171}
                             native 30 fps frame indices, end exclusive. An optional line
                             {"meta": {"sample_fps": 10}} gives the sample rate (else manifest.json).
  <seq>.npz                  alternative: "labels" int array in CalMS21 coding (attack 0, investigation 1,
                             mount 2, other 3), either per 30 fps frame or per sample, plus "sample_fps"
                             (in the npz or the manifest). Per-sample labels are upsampled to 30 fps by
                             nearest sample: frame f takes sample round(f/step), step = 30 / sample_fps.
`<seq>` is the full CalMS21 name (mouse008_task1_annotator1) or its short prefix (mouse008).

Overlapping bouts are resolved by a priority order (default mount > attack > investigation). Frames
with no bout, and frames past the end of the predictions, are "other"; nothing leaves the denominator.

Exclusion (`--exclude-corrections <corrections.json>`, EVAL_PLAN section 6a, dev only): the 30 fps frames in
each correction's `frames_30fps` are dropped from both ground truth and predictions before frame counts, the
confusion matrix, the bootstrap, and the random baseline. For bout metrics the excluded frames are set to
"other" in both (so a bout crossing a corrected window is cut there rather than joined across it).

Integrity (EVAL_PLAN section 7): the predictions' sha256 is appended to the run log (status "started")
*before* any label file is opened; the result is appended as a second record with the same run_id.

Usage: uv run python -m eval.score --split dev --pred-dir <dir> [--purpose tune] [--notes "..."]
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

from eval import common
from eval.common import BEHAVIORS, NATIVE_FPS, VOCAB

DEFAULT_PRIORITY = ("mount", "attack", "investigation")
OTHER_CODE = VOCAB["other"]
BOUT_IOU_THRESHOLDS = (0.3, 0.5)
LABEL_ORDER = ("attack", "investigation", "mount", "other")


# --------------------------------------------------------------------------- prediction loading

def find_pred_file(pred_dir: Path, seq: str) -> Path | None:
    short = seq.split("_task1")[0]
    for stem in (seq, short):
        for ext in (".jsonl", ".npz"):
            p = Path(pred_dir) / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def read_manifest(pred_dir: Path) -> dict:
    p = Path(pred_dir) / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def read_bouts_jsonl(path: Path) -> tuple[list[dict], dict]:
    bouts, meta = [], {}
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        if "meta" in rec:
            meta.update(rec["meta"])
            continue
        missing = {"behavior", "start_frame", "end_frame"} - rec.keys()
        if missing:
            raise ValueError(f"{path}:{i}: bout missing {sorted(missing)}")
        bouts.append(rec)
    return bouts, meta


def step_from_fps(sample_fps) -> int:
    if sample_fps is None:
        raise ValueError("sample_fps missing: give it in the file's meta line / npz or in manifest.json")
    step = NATIVE_FPS / float(sample_fps)
    if abs(step - round(step)) > 1e-9 or round(step) < 1:
        raise ValueError(f"sample_fps {sample_fps} does not divide {NATIVE_FPS}")
    return int(round(step))


def rasterize_bouts(bouts: list[dict], n_frames: int, priority=DEFAULT_PRIORITY,
                    warnings: list | None = None, name: str = "") -> tuple[np.ndarray, dict]:
    """Return (exclusive int8[T] in VOCAB coding, {behavior: bool[T]} without exclusivity)."""
    warnings = warnings if warnings is not None else []
    if sorted(priority) != sorted(BEHAVIORS):
        raise ValueError(f"priority must be a permutation of {BEHAVIORS}, got {priority}")
    ovr = {b: np.zeros(n_frames, dtype=bool) for b in BEHAVIORS}
    for bt in bouts:
        b = bt["behavior"]
        if b == "other":
            continue
        if b not in BEHAVIORS:
            raise ValueError(f"{name}: unknown behavior {b!r}")
        s, e = int(bt["start_frame"]), int(bt["end_frame"])
        if s < 0 or e <= s:
            raise ValueError(f"{name}: invalid bout [{s}, {e}) (need 0 <= start < end, end exclusive)")
        if s >= n_frames:
            warnings.append(f"{name}: bout [{s},{e}) starts past the last frame {n_frames - 1}; dropped")
            continue
        if e > n_frames:
            warnings.append(f"{name}: bout [{s},{e}) clipped to {n_frames}")
            e = n_frames
        ovr[b][s:e] = True
    excl = np.full(n_frames, OTHER_CODE, dtype=np.int8)
    for b in reversed(tuple(priority)):  # highest priority painted last
        excl[ovr[b]] = VOCAB[b]
    return excl, ovr


def upsample_samples(sample_labels: np.ndarray, n_frames: int, step: int,
                     warnings: list | None = None, name: str = "") -> np.ndarray:
    """Nearest-sample upsampling: frame f -> sample round(f / step) (3k+1 -> k, 3k+2 -> k+1 at step 3).

    The index is clamped to the last sample the video can have (ceil(T/step) - 1). Frames whose
    sample is missing because the predictions are short count as "other".
    """
    warnings = warnings if warnings is not None else []
    k_expected = math.ceil(n_frames / step)
    k = len(sample_labels)
    if k > k_expected:
        raise ValueError(f"{name}: {k} samples > {k_expected} possible for {n_frames} frames at step {step}")
    if k < k_expected:
        warnings.append(f"{name}: {k} samples < {k_expected} expected; missing tail scored as other")
    f = np.arange(n_frames)
    idx = np.minimum(np.floor(f / step + 0.5).astype(np.int64), k_expected - 1)
    out = np.full(n_frames, OTHER_CODE, dtype=np.int8)
    ok = idx < k
    out[ok] = sample_labels[idx[ok]]
    return out


def load_prediction(path: Path, n_frames: int, manifest: dict, priority=DEFAULT_PRIORITY,
                    warnings: list | None = None) -> tuple[np.ndarray, dict, dict]:
    """Return (exclusive labels int8[T], one-vs-rest masks, meta)."""
    warnings = warnings if warnings is not None else []
    path = Path(path)
    name = path.stem
    if path.suffix == ".jsonl":
        bouts, meta = read_bouts_jsonl(path)
        fps = meta.get("sample_fps", manifest.get("sample_fps"))
        step_from_fps(fps)  # validates that the sample rate was declared and is sane
        excl, ovr = rasterize_bouts(bouts, n_frames, priority, warnings, name)
        return excl, ovr, {"format": "bouts", "sample_fps": fps, "n_bouts": len(bouts)}
    if path.suffix == ".npz":
        d = np.load(path)
        lab = np.asarray(d["labels"]).astype(np.int64).ravel()
        fps = float(d["sample_fps"]) if "sample_fps" in d.files else manifest.get("sample_fps")
        step = step_from_fps(fps)
        if "vocab" in d.files and json.loads(str(d["vocab"])) != VOCAB:
            raise ValueError(f"{name}: npz vocab must equal CalMS21 {VOCAB}")
        bad = set(np.unique(lab)) - set(VOCAB.values())
        if bad:
            raise ValueError(f"{name}: label codes {sorted(bad)} not in {VOCAB}")
        if step == 1:
            if len(lab) > n_frames:
                raise ValueError(f"{name}: {len(lab)} labels > {n_frames} frames")
            if len(lab) < n_frames:
                warnings.append(f"{name}: {len(lab)} labels < {n_frames} frames; tail scored as other")
            excl = np.full(n_frames, OTHER_CODE, dtype=np.int8)
            excl[: len(lab)] = lab
        else:
            excl = upsample_samples(lab.astype(np.int8), n_frames, step, warnings, name)
        ovr = {b: excl == VOCAB[b] for b in BEHAVIORS}
        return excl, ovr, {"format": "frames", "sample_fps": fps, "n_labels": int(len(lab))}
    raise ValueError(f"unsupported prediction file {path}")


# --------------------------------------------------------------------------- metrics

def prf(tp: float, fp: float, fn: float) -> tuple[float, float, float]:
    """Precision, recall, F1; a zero denominator gives 0.0 (sklearn zero_division=0 behavior)."""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return p, r, f


def counts(gt: np.ndarray, pred_masks: dict) -> np.ndarray:
    """[3 behaviors, (tp, fp, fn)] for one sequence. pred_masks: {behavior: bool[T]}."""
    out = np.zeros((len(BEHAVIORS), 3), dtype=np.int64)
    for i, b in enumerate(BEHAVIORS):
        g, p = gt == VOCAB[b], pred_masks[b]
        out[i] = (np.sum(g & p), np.sum(~g & p), np.sum(g & ~p))
    return out


def confusion(gt: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """4x4, rows = ground truth, cols = prediction, order LABEL_ORDER (= VOCAB codes 0..3)."""
    return np.bincount(gt.astype(np.int64) * 4 + pred.astype(np.int64), minlength=16).reshape(4, 4)


def runs(labels: np.ndarray, code: int) -> np.ndarray:
    """Maximal runs of `code` as [n, 2] (start, end exclusive)."""
    m = np.concatenate([[False], labels == code, [False]]).astype(np.int8)
    d = np.diff(m)
    return np.stack([np.flatnonzero(d == 1), np.flatnonzero(d == -1)], axis=1)


def match_bouts(gt_b: np.ndarray, pr_b: np.ndarray, thr: float) -> int:
    """One-to-one greedy matching by temporal IoU (highest first); returns number of matched pairs."""
    if len(gt_b) == 0 or len(pr_b) == 0:
        return 0
    inter = np.clip(np.minimum(gt_b[:, None, 1], pr_b[None, :, 1]) -
                    np.maximum(gt_b[:, None, 0], pr_b[None, :, 0]), 0, None)
    union = (gt_b[:, 1] - gt_b[:, 0])[:, None] + (pr_b[:, 1] - pr_b[:, 0])[None, :] - inter
    iou = inter / union
    gi, pi = np.nonzero(iou >= thr)
    order = np.argsort(-iou[gi, pi], kind="stable")
    used_g, used_p, n = set(), set(), 0
    for j in order:
        g, p = int(gi[j]), int(pi[j])
        if g not in used_g and p not in used_p:
            used_g.add(g)
            used_p.add(p)
            n += 1
    return n


def bout_counts(gt: np.ndarray, pred: np.ndarray, thresholds=BOUT_IOU_THRESHOLDS) -> dict:
    """{thr: [3, (n_gt, n_pred, matched)]} from the exclusive prediction's maximal runs."""
    out = {}
    for thr in thresholds:
        c = np.zeros((len(BEHAVIORS), 3), dtype=np.int64)
        for i, b in enumerate(BEHAVIORS):
            g, p = runs(gt, VOCAB[b]), runs(pred, VOCAB[b])
            c[i] = (len(g), len(p), match_bouts(g, p, thr))
        out[thr] = c
    return out


def _metric_block(c: np.ndarray) -> dict:
    tp, fp, fn = (int(x) for x in c)
    p, r, f = prf(tp, fp, fn)
    return {"tp": tp, "fp": fp, "fn": fn, "gt_frames": tp + fn, "pred_frames": tp + fp,
            "precision": p, "recall": r, "f1": f}


def _vec_prf(c: np.ndarray):
    """c [..., 3] -> precision, recall, f1 arrays; zero denominators -> nan (flagged as degenerate)."""
    tp, fp, fn = c[..., 0].astype(float), c[..., 1].astype(float), c[..., 2].astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(tp + fp > 0, tp / (tp + fp), np.nan)
        r = np.where(tp + fn > 0, tp / (tp + fn), np.nan)
        f = np.where(2 * tp + fp + fn > 0, 2 * tp / (2 * tp + fp + fn), np.nan)
    return p, r, f


def bootstrap_ci(per_seq: np.ndarray, reps: int = 10_000, seed: int = common.SEED) -> dict:
    """95% percentile CIs by resampling whole sequences with replacement.

    per_seq: [S, 3 behaviors, 3 (tp, fp, fn)]. Each resample pools the drawn sequences' counts.
    Resamples where a quantity is undefined (e.g. no ground-truth frames of a behavior, so recall is
    0/0) are dropped for that quantity and counted in `undefined_frac`. A resample with no ground truth
    but some false positives has F1 = 0 (defined), which is what pooling implies.
    """
    w = common.bootstrap_weights(per_seq.shape[0], reps, seed)
    pooled = np.einsum("rs,sbk->rbk", w, per_seq)  # [R, 3, 3]
    p, r, f = _vec_prf(pooled)
    no_gt = (pooled[..., 0] + pooled[..., 2]) == 0  # [R, 3]
    out = {"reps": reps, "seed": seed, "unit": "sequence", "method": "percentile 2.5/97.5", "behaviors": {}}
    for i, b in enumerate(BEHAVIORS):
        blk = {}
        for nm, arr in (("precision", p[:, i]), ("recall", r[:, i]), ("f1", f[:, i])):
            ok = ~np.isnan(arr)
            blk[nm] = ([float(np.percentile(arr[ok], 2.5)), float(np.percentile(arr[ok], 97.5))]
                       if ok.any() else None)
            blk[f"{nm}_undefined_frac"] = float(1 - ok.mean())
        blk["no_gt_frac"] = float(no_gt[:, i].mean())
        out["behaviors"][b] = blk
    macro = f.mean(axis=1)  # nan if any behavior's F1 is undefined in that resample
    ok = ~np.isnan(macro)
    out["macro_f1"] = [float(np.percentile(macro[ok], 2.5)), float(np.percentile(macro[ok], 97.5))] \
        if ok.any() else None
    out["macro_f1_undefined_frac"] = float(1 - ok.mean())
    return out


def score_split(gt_by_seq: dict, pred_by_seq: dict, reps: int = 10_000, seed: int = common.SEED,
                bout_thresholds=BOUT_IOU_THRESHOLDS, exclude: dict | None = None) -> dict:
    """gt_by_seq: {seq: int8[T]}; pred_by_seq: {seq: (exclusive int8[T], ovr masks)}. Pure function.

    exclude: optional {seq: bool[T]}, True = drop the frame (see module docstring, "Exclusion")."""
    seqs = list(gt_by_seq)
    per_seq = np.zeros((len(seqs), len(BEHAVIORS), 3), dtype=np.int64)
    per_seq_ovr = np.zeros_like(per_seq)
    conf = np.zeros((4, 4), dtype=np.int64)
    bouts = {thr: np.zeros((len(BEHAVIORS), 3), dtype=np.int64) for thr in bout_thresholds}
    per_sequence = {}
    for si, s in enumerate(seqs):
        gt = gt_by_seq[s]
        excl, ovr = pred_by_seq[s]
        if len(excl) != len(gt):
            raise ValueError(f"{s}: prediction length {len(excl)} != labels {len(gt)}")
        drop = exclude.get(s) if exclude else None
        n_excluded = 0
        if drop is not None and drop.any():
            if len(drop) != len(gt):
                raise ValueError(f"{s}: exclusion mask length {len(drop)} != labels {len(gt)}")
            n_excluded = int(drop.sum())
            gt_b, excl_b = gt.copy(), excl.copy()
            gt_b[drop], excl_b[drop] = OTHER_CODE, OTHER_CODE
            keep = ~drop
            gt, excl, ovr = gt[keep], excl[keep], {b: m[keep] for b, m in ovr.items()}
        else:
            gt_b, excl_b = gt, excl
        per_seq[si] = counts(gt, {b: excl == VOCAB[b] for b in BEHAVIORS})
        per_seq_ovr[si] = counts(gt, ovr)
        conf += confusion(gt, excl)
        bc = bout_counts(gt_b, excl_b, bout_thresholds)
        for thr in bout_thresholds:
            bouts[thr] += bc[thr]
        per_sequence[s] = {"n_frames": int(len(gt)), "n_excluded": n_excluded,
                           **{b: _metric_block(per_seq[si, i]) for i, b in enumerate(BEHAVIORS)}}
    pooled = per_seq.sum(axis=0)
    pooled_ovr = per_seq_ovr.sum(axis=0)
    ci = bootstrap_ci(per_seq, reps, seed)
    beh = {}
    for i, b in enumerate(BEHAVIORS):
        blk = _metric_block(pooled[i])
        blk["ci95"] = {k: ci["behaviors"][b][k] for k in ("precision", "recall", "f1")}
        blk["ci95_no_gt_resample_frac"] = ci["behaviors"][b]["no_gt_frac"]
        beh[b] = blk
    macro = float(np.mean([beh[b]["f1"] for b in BEHAVIORS]))
    seq_summary = {}
    for i, b in enumerate(BEHAVIORS):
        f1s = [prf(*per_seq[si, i])[2] for si in range(len(seqs)) if per_seq[si, i, 0] + per_seq[si, i, 2] > 0]
        seq_summary[b] = {"n_seqs_with_behavior": len(f1s),
                          "mean_f1": float(np.mean(f1s)) if f1s else None,
                          "median_f1": float(np.median(f1s)) if f1s else None}
    bout_out = {}
    for thr in bout_thresholds:
        d = {}
        for i, b in enumerate(BEHAVIORS):
            n_gt, n_pred, m = (int(x) for x in bouts[thr][i])
            p, r = (m / n_pred if n_pred else 0.0), (m / n_gt if n_gt else 0.0)
            d[b] = {"n_gt": n_gt, "n_pred": n_pred, "matched": m, "precision": p, "recall": r,
                    "f1": 2 * p * r / (p + r) if p + r else 0.0}
        bout_out[str(thr)] = d
    ovr_out = {b: _metric_block(pooled_ovr[i]) for i, b in enumerate(BEHAVIORS)}
    return {
        "convention": "frame-level, 30 fps, pooled over all frames of all sequences (CalMS21 Sec. E); "
                      "macro = unweighted mean of attack/investigation/mount F1",
        "n_sequences": len(seqs),
        "n_frames": int(sum(per_sequence[s]["n_frames"] for s in seqs)),
        "n_frames_excluded": int(sum(per_sequence[s]["n_excluded"] for s in seqs)),
        "behaviors": beh,
        "macro_f1": macro,
        "macro_f1_ci95": ci["macro_f1"],
        "bootstrap": {k: v for k, v in ci.items() if k != "behaviors"},
        "per_sequence": per_sequence,
        "per_sequence_summary": seq_summary,
        "confusion": {"rows_gt_cols_pred": list(LABEL_ORDER), "matrix": conf.tolist()},
        "ovr_no_exclusivity": {"behaviors": ovr_out,
                               "macro_f1": float(np.mean([ovr_out[b]["f1"] for b in BEHAVIORS]))},
        "bouts": {"matching": "one-to-one greedy by temporal IoU >= threshold, on maximal runs of the "
                              "exclusive prediction", "by_threshold": bout_out},
    }


# --------------------------------------------------------------------------- baselines

def random_baseline(gt_by_seq: dict, simulate_reps: int = 0, seed: int = common.SEED) -> dict:
    """Frequency-matched random predictor: each frame independently gets label b with probability
    equal to b's pooled prevalence in this split's ground truth (so it uses the labels' class
    frequencies; it is a floor, not a fair competitor).

    Expected precision = recall = F1 = prevalence (analytic, large-N limit). With simulate_reps > 0,
    also draws that many seeded random label sequences and scores them with the same pooled scorer.
    """
    all_gt = np.concatenate([gt_by_seq[s] for s in gt_by_seq])
    n = len(all_gt)
    prev = {lab: float(np.mean(all_gt == VOCAB[lab])) for lab in LABEL_ORDER}
    out = {"method": "frequency-matched random (i.i.d. per frame, pooled prevalence)",
           "n_frames": n, "prevalence": prev,
           "expected_f1": {b: prev[b] for b in BEHAVIORS},
           "expected_macro_f1": float(np.mean([prev[b] for b in BEHAVIORS]))}
    if simulate_reps:
        rng = np.random.default_rng(seed)
        probs = np.array([prev[lab] for lab in LABEL_ORDER])
        f1s = []
        for _ in range(simulate_reps):
            pred = rng.choice(4, size=n, p=probs / probs.sum()).astype(np.int8)
            c = counts(all_gt, {b: pred == VOCAB[b] for b in BEHAVIORS})
            f1s.append([prf(*c[i])[2] for i in range(len(BEHAVIORS))])
        f1s = np.array(f1s)
        out["simulated"] = {"reps": simulate_reps, "seed": seed,
                            "mean_f1": {b: float(f1s[:, i].mean()) for i, b in enumerate(BEHAVIORS)},
                            "mean_macro_f1": float(f1s.mean(axis=1).mean())}
    return out


# --------------------------------------------------------------------------- report and CLI

def format_report(m: dict, title: str) -> str:
    exc = (f" ({m['n_frames_excluded']} corrected-window frames excluded)"
           if m.get("n_frames_excluded") else "")
    lines = [title, f"{m['n_sequences']} sequences, {m['n_frames']} frames @30fps{exc}; {m['convention']}", "",
             f"{'behavior':<14}{'P':>8}{'R':>8}{'F1':>8}   {'F1 95% CI (seq bootstrap)':<26}"
             f"{'GT fr':>8}{'pred fr':>9}"]
    for b in BEHAVIORS:
        x = m["behaviors"][b]
        ci = x["ci95"]["f1"]
        cis = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "n/a"
        lines.append(f"{b:<14}{x['precision']:>8.3f}{x['recall']:>8.3f}{x['f1']:>8.3f}   {cis:<26}"
                     f"{x['gt_frames']:>8}{x['pred_frames']:>9}")
    mci = m["macro_f1_ci95"]
    lines.append(f"{'macro F1':<30}{m['macro_f1']:>8.3f}   "
                 f"{(f'[{mci[0]:.3f}, {mci[1]:.3f}]' if mci else 'n/a'):<26}")
    lines += ["", "bout-level (IoU match): " + "; ".join(
        f"IoU>={t}: " + ", ".join(f"{b} R={d[b]['recall']:.3f} P={d[b]['precision']:.3f}" for b in BEHAVIORS)
        for t, d in m["bouts"]["by_threshold"].items())]
    rb = m.get("random_baseline")
    if rb:
        lines.append("frequency-matched random (expected F1): " + ", ".join(
            f"{b} {rb['expected_f1'][b]:.3f}" for b in BEHAVIORS) + f", macro {rb['expected_macro_f1']:.3f}")
    lines += ["", "per sequence F1 (attack / investigation / mount; '-' = behavior absent in GT):"]
    for s, d in m["per_sequence"].items():
        cells = [f"{d[b]['f1']:.3f}" if d[b]["gt_frames"] else "-" for b in BEHAVIORS]
        lines.append(f"  {s:<28}{d['n_frames']:>7}  " + "  ".join(f"{c:>6}" for c in cells))
    return "\n".join(lines)


def load_exclusions(path: Path, split: str, seqs: list[str]) -> tuple[dict, dict]:
    """Read a corrections file (eval/simulate_corrections.py). Returns ({seq: [(f0, f1), ...]}, log info).

    Only dev corrections can be excluded, and only when scoring dev: on dev-holdout the corrected windows
    don't exist (exclusion would silently do nothing) and on test it is not part of the protocol."""
    path = Path(path)
    doc = json.loads(path.read_text())
    if split != "dev" or doc.get("split") != "dev":
        raise SystemExit(f"--exclude-corrections: only dev corrections on the dev split "
                         f"(scoring {split}, corrections from {doc.get('split')!r})")
    ranges: dict = {}
    for c in doc["corrections"]:
        f0, f1 = (int(x) for x in c["frames_30fps"])
        if not 0 <= f0 < f1:
            raise ValueError(f"{path}: correction {c.get('id')} has invalid frames_30fps {c['frames_30fps']}")
        ranges.setdefault(c["seq"], []).append((f0, f1))
    unknown = sorted(set(ranges) - set(seqs))  # e.g. scoring a --seqs subset; logged, not an error
    info = {"file": str(path), "sha256": common.sha256_file(path), "corrections_run_id": doc.get("run_id"),
            "n_corrections": len(doc["corrections"]), "seqs_not_scored": unknown}
    return ranges, info


def exclusion_masks(ranges: dict, n_frames_by_seq: dict) -> dict:
    out = {}
    for s, n in n_frames_by_seq.items():
        m = np.zeros(n, dtype=bool)
        for f0, f1 in ranges.get(s, []):
            if f1 > n:
                raise ValueError(f"{s}: excluded frames [{f0},{f1}) beyond {n} frames")
            m[f0:f1] = True
        out[s] = m
    return out


def _hash_predictions(pred_dir: Path, files: dict) -> tuple[str, dict]:
    per_file = {s: common.sha256_file(p) for s, p in files.items() if p is not None}
    man = Path(pred_dir) / "manifest.json"
    if man.exists():
        per_file["manifest.json"] = common.sha256_file(man)
    return common.sha256_json(per_file), per_file


def run(split: str, pred_dir: Path, purpose: str | None = None, priority=DEFAULT_PRIORITY,
        labels_dir: Path = common.LABELS_DIR, split_file: Path = common.SPLIT_FILE,
        log_path: Path = common.RUNS_LOG, out_dir: Path | None = None, seqs: list[str] | None = None,
        allow_missing: bool = False, reps: int = 10_000, confirm_test_run: bool = False,
        notes: str = "", exclude_corrections: Path | None = None) -> dict:
    split = common.canonical_split(split)
    purpose = purpose or {"dev": "tune", "dev_holdout": "holdout-check"}.get(split)
    if exclude_corrections and split != "dev":  # checked before the test guard: never a test "run"
        raise SystemExit(f"--exclude-corrections: only dev corrections on the dev split (scoring {split})")
    if split == "test":  # EVAL_PLAN v1-final section 7: all arms of a test run are hashed and scored together
        raise SystemExit("test split: use `python -m eval.score_arms` (all arms in one logged execution)")
    common.guard_test_run(split, confirm_test_run, purpose or "", "score", log_path)
    if split == "test" and (seqs or allow_missing):
        raise SystemExit("test split: --seqs and --allow-missing are not allowed (all 19 sequences)")
    all_seqs, _ = common.load_split(split, split_file)
    if seqs:
        unknown = set(seqs) - set(all_seqs)
        if unknown:
            raise SystemExit(f"sequences not in {split}: {sorted(unknown)}")
        seqs = [s for s in all_seqs if s in set(seqs)]
    else:
        seqs = all_seqs
    ranges, excl_info = (load_exclusions(exclude_corrections, split, seqs) if exclude_corrections
                         else ({}, None))
    pred_dir = Path(pred_dir)
    manifest = read_manifest(pred_dir)
    files = {s: find_pred_file(pred_dir, s) for s in seqs}
    missing = [s for s, p in files.items() if p is None]
    t0 = time.time()
    run_id = common.new_run_id(f"score-{split}")
    pred_hash, per_file_hash = _hash_predictions(pred_dir, files)
    config = manifest.get("config", {})
    base = {
        "run_id": run_id, "kind": "score", "split": split, "purpose": purpose,
        **common.git_info(),
        "split_file": str(split_file), "split_file_sha256": common.sha256_file(Path(split_file)),
        "sequences": seqs, "partial": seqs != all_seqs, "missing_predictions": missing,
        "pred_dir": str(pred_dir), "predictions_sha256": pred_hash, "predictions_files_sha256": per_file_hash,
        "config": config, "config_sha256": common.sha256_json(config),
        "scorer": {"priority": list(priority), "reps": reps, "seed": common.SEED,
                   "bout_iou_thresholds": list(BOUT_IOU_THRESHOLDS), "allow_missing": allow_missing},
        "cost": manifest.get("cost"), "wall_clock_s": manifest.get("wall_clock_s"),
        "calls": manifest.get("calls"), "notes": notes or manifest.get("notes", ""),
        "exclude_corrections": excl_info,
    }
    # Pre-registration: predictions hash is on disk before any label file is opened.
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, log_path)
    warnings: list[str] = []
    labels_loaded = False
    try:
        if missing and not allow_missing:
            raise ValueError(f"no prediction file for {missing} (use --allow-missing to score them as all-other)")
        gt_by_seq, pred_by_seq, pred_meta = {}, {}, {}
        for s in seqs:
            labels_loaded = True
            gt = common.load_sequence(split, s, labels_dir)["labels"]
            gt_by_seq[s] = gt
            if files[s] is None:
                warnings.append(f"{s}: no prediction file; scored as all-other")
                excl = np.full(len(gt), OTHER_CODE, dtype=np.int8)
                pred_by_seq[s] = (excl, {b: np.zeros(len(gt), bool) for b in BEHAVIORS})
                continue
            excl, ovr, meta = load_prediction(files[s], len(gt), manifest, priority, warnings)
            pred_by_seq[s], pred_meta[s] = (excl, ovr), meta
        masks = exclusion_masks(ranges, {s: len(g) for s, g in gt_by_seq.items()}) if excl_info else None
        metrics = score_split(gt_by_seq, pred_by_seq, reps=reps, exclude=masks)
        metrics["random_baseline"] = random_baseline(
            {s: g[~masks[s]] for s, g in gt_by_seq.items()} if masks else gt_by_seq)
        if excl_info:
            metrics["excluded_frames_by_seq"] = {s: int(m.sum()) for s, m in masks.items() if m.any()}
        fps = sorted({str(m.get("sample_fps")) for m in pred_meta.values()})
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True, "metrics": metrics,
               "sample_fps": fps, "warnings": warnings, "scorer_wall_clock_s": round(time.time() - t0, 3)}
    except Exception as e:  # logged, then re-raised: failed runs are recorded too
        rec = {**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
               "labels_loaded": labels_loaded,
               "traceback": traceback.format_exc(limit=5), "warnings": warnings}
        common.append_run(rec, log_path)
        raise
    common.append_run(rec, log_path)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"{run_id}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, help="dev | dev-holdout | test")
    ap.add_argument("--pred-dir", required=True, type=Path)
    ap.add_argument("--purpose", help="tune (dev default), holdout-check (dev-holdout default), "
                                      "test-1|test-2, ablation:<name>")
    ap.add_argument("--priority", default=",".join(DEFAULT_PRIORITY),
                    help="overlap priority, highest first (default mount,attack,investigation)")
    ap.add_argument("--labels-dir", type=Path, default=common.LABELS_DIR)
    ap.add_argument("--split-file", type=Path, default=common.SPLIT_FILE)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--out-dir", type=Path, default=Path("eval/results"),
                    help="full result JSON is written here as <run_id>.json")
    ap.add_argument("--seqs", help="comma-separated subset (dev / dev-holdout only; logged as partial)")
    ap.add_argument("--allow-missing", action="store_true", help="score missing prediction files as all-other")
    ap.add_argument("--reps", type=int, default=10_000, help="bootstrap resamples")
    ap.add_argument("--confirm-test-run", action="store_true")
    ap.add_argument("--notes", default="")
    ap.add_argument("--exclude-corrections", type=Path,
                    help="corrections.json from eval/simulate_corrections.py: drop its frames_30fps (dev only)")
    a = ap.parse_args(argv)
    rec = run(a.split, a.pred_dir, a.purpose, tuple(a.priority.split(",")), a.labels_dir, a.split_file,
              a.log, a.out_dir, a.seqs.split(",") if a.seqs else None, a.allow_missing, a.reps,
              a.confirm_test_run, a.notes, a.exclude_corrections)
    print(format_report(rec["metrics"], f"run {rec['run_id']} split={rec['split']} purpose={rec['purpose']}"
                        + (" PARTIAL" if rec["partial"] else "")))
    for w in rec["warnings"]:
        print("warning:", w, file=sys.stderr)
    print(f"\nlogged to {a.log}; full result {a.out_dir}/{rec['run_id']}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
