"""Score several prediction arms in ONE logged execution (docs/EVAL_PLAN.md section 7, test-run protocol).

Test runs must use this entry point (`eval.score` refuses the test split): every arm of a test run is hashed
and written to the run log in a single `started` record before any label file is opened, then all arms are
scored against the same labels, with paired sequence-bootstrap differences between arms.

    uv run python -m eval.score_arms --split test --purpose test-1 --confirm-test-run \
        --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
        --arm zs_t1=<dir> --arm zs_rules=<dir> --arm fs_t1=<dir> --arm fs_rules=<dir> \
        --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1

Each `--arm name=dir` is a prediction directory in the `eval.score` format. `--compare a:b` reports b minus a.

On the test split:
- `--frozen-config` is required; every {"path", "sha256"} pair in it is re-hashed and must match, or the run
  stops before the `started` record (nothing is logged as a test run).
- Per-sequence metrics are **sealed**: they are written only to `<run_id>.per_sequence.sealed.json` (its
  sha256 is in the log), not to the run log, the result JSON, or the console. EVAL_PLAN section 7 says when
  it may be opened.
- Targets (`--targets`) are evaluated mechanically for the primary arm and printed as MET / MET ROBUSTLY /
  NOT MET.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from eval import common, score
from eval.common import BEHAVIORS, NATIVE_FPS

SEALED_SUFFIX = ".per_sequence.sealed.json"


def parse_arm(spec: str) -> tuple[str, Path]:
    name, sep, d = spec.partition("=")
    if not sep or not name or not d:
        raise SystemExit(f"--arm must be name=dir, got {spec!r}")
    return name, Path(d)


def verify_frozen_config(path: Path) -> dict:
    """Re-hash every {"path": ..., "sha256": ...} entry in the frozen config; refuse on any mismatch."""
    cfg = json.loads(Path(path).read_text())
    checked, bad = {}, []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("path"), str) and isinstance(o.get("sha256"), str):
                p = Path(o["path"])
                actual = common.sha256_file(p) if p.exists() else None
                checked[o["path"]] = actual
                if actual != o["sha256"]:
                    bad.append(f"{o['path']}: frozen {o['sha256'][:12]}, now {actual and actual[:12]}")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(cfg)
    if bad:
        raise SystemExit("frozen config mismatch (refusing to start):\n  " + "\n  ".join(bad))
    return {"path": str(path), "sha256": common.sha256_file(Path(path)), "files_checked": checked,
            "name": cfg.get("name")}


def per_seq_counts(metrics: dict, seqs: list[str]) -> np.ndarray:
    """[S, 3 behaviors, (tp, fp, fn)] back from score_split's per_sequence blocks."""
    ps = metrics["per_sequence"]
    return np.array([[[ps[s][b]["tp"], ps[s][b]["fp"], ps[s][b]["fn"]] for b in BEHAVIORS] for s in seqs],
                    dtype=np.int64)


def paired_diff(base: np.ndarray, arm: np.ndarray, reps: int = 10_000, seed: int = common.SEED) -> dict:
    """arm minus base, per behavior F1 and macro F1, pooled; CI from the SAME sequence resamples for both."""
    def f1(c):
        return score._vec_prf(c)[2]

    point_b, point_a = f1(base.sum(axis=0)), f1(arm.sum(axis=0))
    w = common.bootstrap_weights(base.shape[0], reps, seed)
    fb = f1(np.einsum("rs,sbk->rbk", w, base))
    fa = f1(np.einsum("rs,sbk->rbk", w, arm))
    out = {"statistic": "arm minus base, pooled frame F1", "reps": reps, "seed": seed, "unit": "sequence",
           "behaviors": {}}
    for i, b in enumerate(BEHAVIORS):
        d = fa[:, i] - fb[:, i]
        ok = ~np.isnan(d)
        out["behaviors"][b] = {
            "diff": float(point_a[i] - point_b[i]),
            "ci95": [float(np.percentile(d[ok], 2.5)), float(np.percentile(d[ok], 97.5))] if ok.any() else None,
            "frac_resamples_positive": float((d[ok] > 0).mean()) if ok.any() else None,
            "undefined_frac": float(1 - ok.mean())}
    d = fa.mean(axis=1) - fb.mean(axis=1)
    ok = ~np.isnan(d)
    out["macro_f1"] = {
        "diff": float(np.mean(point_a) - np.mean(point_b)),
        "ci95": [float(np.percentile(d[ok], 2.5)), float(np.percentile(d[ok], 97.5))] if ok.any() else None,
        "frac_resamples_positive": float((d[ok] > 0).mean()) if ok.any() else None,
        "undefined_frac": float(1 - ok.mean())}
    return out


def duration_agreement(metrics: dict, seqs: list[str]) -> dict:
    """Per behavior, total scored duration per video (s) vs ground truth: MAE, mean signed error, Pearson r."""
    out = {}
    for b in BEHAVIORS:
        g = np.array([metrics["per_sequence"][s][b]["gt_frames"] for s in seqs], float) / NATIVE_FPS
        p = np.array([metrics["per_sequence"][s][b]["pred_frames"] for s in seqs], float) / NATIVE_FPS
        r = float(np.corrcoef(g, p)[0, 1]) if len(seqs) > 1 and g.std() > 0 and p.std() > 0 else None
        out[b] = {"n_videos": len(seqs), "mae_s": float(np.mean(np.abs(p - g))),
                  "mean_signed_error_s": float(np.mean(p - g)), "pearson_r": r,
                  "gt_total_s": float(g.sum()), "pred_total_s": float(p.sum())}
    return out


def _verdict(point: float, lo: float | None, thr: float) -> str:
    if point < thr:
        return "NOT MET"
    return "MET ROBUSTLY" if lo is not None and lo >= thr else "MET"


def check_targets(targets: dict, arms_metrics: dict, diffs: dict) -> dict:
    out = {"rule": targets.get("rule"), "f1": {}, "tier1_lift_macro": None}
    m = arms_metrics.get(targets["primary_arm"])
    if m is None:
        out["error"] = f"primary arm {targets['primary_arm']!r} not scored"
        return out
    for level, thr in targets["f1"].items():
        res = {}
        for k, t in thr.items():
            if k == "macro":
                pt, ci = m["macro_f1"], m["macro_f1_ci95"]
            else:
                pt, ci = m["behaviors"][k]["f1"], m["behaviors"][k]["ci95"]["f1"]
            res[k] = {"threshold": t, "point": pt, "ci95": ci, "verdict": _verdict(pt, ci and ci[0], t)}
        out["f1"][level] = res
    lift = targets.get("tier1_lift_macro")
    if lift:
        key = f"{lift['baseline_arm']}:{lift['arm']}"
        d = diffs.get(key)
        if d is None:
            out["tier1_lift_macro"] = {"error": f"comparison {key} not requested"}
        else:
            md = d["macro_f1"]
            out["tier1_lift_macro"] = {
                level: {"threshold": lift[level], "point": md["diff"], "ci95": md["ci95"],
                        "verdict": _verdict(md["diff"], md["ci95"] and md["ci95"][0], lift[level])}
                for level in ("minimum_credible", "good")}
    return out


def run_arms(split: str, arms: dict, primary: str, purpose: str | None = None,
             compares: list[tuple[str, str]] | None = None, frozen_config: Path | None = None,
             targets_path: Path | None = None, priority=score.DEFAULT_PRIORITY,
             labels_dir: Path = common.LABELS_DIR, split_file: Path = common.SPLIT_FILE,
             log_path: Path = common.RUNS_LOG, out_dir: Path | None = None, reps: int = 10_000,
             confirm_test_run: bool = False, notes: str = "", seal: bool | None = None) -> dict:
    split = common.canonical_split(split)
    purpose = purpose or {"dev": "tune", "dev_holdout": "holdout-check"}.get(split)
    if not arms or primary not in arms:
        raise SystemExit(f"--primary {primary!r} must be one of the arms {sorted(arms)}")
    compares = list(compares if compares is not None else [(primary, a) for a in arms if a != primary])
    for a, b in compares:
        if a not in arms or b not in arms:
            raise SystemExit(f"--compare {a}:{b}: unknown arm")
    is_test = split == "test"
    seal = is_test if seal is None else (seal or is_test)
    if is_test and frozen_config is None:
        raise SystemExit("test split: --frozen-config is required")
    common.guard_test_run(split, confirm_test_run, purpose or "", "score", log_path)
    frozen = verify_frozen_config(frozen_config) if frozen_config else None
    targets = json.loads(Path(targets_path).read_text()) if targets_path else None
    seqs, _ = common.load_split(split, split_file)

    arm_info, files_by_arm, manifests = {}, {}, {}
    for name, d in arms.items():
        d = Path(d)
        manifests[name] = score.read_manifest(d)
        files = {s: score.find_pred_file(d, s) for s in seqs}
        files_by_arm[name] = files
        h, per_file = score._hash_predictions(d, files)
        arm_info[name] = {"pred_dir": str(d), "predictions_sha256": h, "predictions_files_sha256": per_file,
                          "missing_predictions": [s for s, p in files.items() if p is None],
                          "manifest": manifests[name],
                          "config_sha256": common.sha256_json(manifests[name].get("config", {}))}
    t0 = time.time()
    run_id = common.new_run_id(f"score-{split}")
    base = {
        "run_id": run_id, "kind": "score", "split": split, "purpose": purpose, "multi_arm": True,
        **common.git_info(),
        "split_file": str(split_file), "split_file_sha256": common.sha256_file(Path(split_file)),
        "sequences": seqs, "partial": False, "primary_arm": primary, "compares": [f"{a}:{b}" for a, b in compares],
        "predictions_sha256": common.sha256_json({n: a["predictions_sha256"] for n, a in arm_info.items()}),
        "arms": arm_info, "frozen_config": frozen,
        "targets": {"path": str(targets_path), "sha256": common.sha256_file(Path(targets_path))}
        if targets_path else None,
        "scorer": {"priority": list(priority), "reps": reps, "seed": common.SEED,
                   "bout_iou_thresholds": list(score.BOUT_IOU_THRESHOLDS), "allow_missing": False,
                   "per_sequence_sealed": seal},
        "notes": notes,
    }
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, log_path)  # before labels
    warnings: list[str] = []
    labels_loaded = False
    try:
        missing = {n: a["missing_predictions"] for n, a in arm_info.items() if a["missing_predictions"]}
        if missing:
            raise ValueError(f"missing prediction files (every arm needs all {len(seqs)} sequences): {missing}")
        gt_by_seq = {}
        for s in seqs:
            labels_loaded = True
            gt_by_seq[s] = common.load_sequence(split, s, labels_dir)["labels"]
        arms_metrics, counts_by_arm, sample_fps = {}, {}, {}
        for name in arms:
            pred_by_seq, fps = {}, set()
            for s in seqs:
                w: list[str] = []
                excl, ovr, meta = score.load_prediction(files_by_arm[name][s], len(gt_by_seq[s]), manifests[name],
                                                        priority, w)
                warnings += [f"{name}: {x}" for x in w]
                pred_by_seq[s] = (excl, ovr)
                fps.add(str(meta.get("sample_fps")))
            m = score.score_split(gt_by_seq, pred_by_seq, reps=reps)
            m["duration_agreement"] = duration_agreement(m, seqs)
            counts_by_arm[name] = per_seq_counts(m, seqs)
            arms_metrics[name], sample_fps[name] = m, sorted(fps)
        diffs = {f"{a}:{b}": paired_diff(counts_by_arm[a], counts_by_arm[b], reps) for a, b in compares}
        target_check = check_targets(targets, arms_metrics, diffs) if targets else None
        sealed_info = None
        if seal:
            sealed = {n: {"per_sequence": m.pop("per_sequence")} for n, m in arms_metrics.items()}
            if out_dir is None:
                raise ValueError("sealing per-sequence metrics needs out_dir")
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            sp = Path(out_dir) / f"{run_id}{SEALED_SUFFIX}"
            sp.write_text(json.dumps({"run_id": run_id, "arms": sealed}, indent=1, default=str))
            sealed_info = {"path": str(sp), "sha256": common.sha256_file(sp)}
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
               "metrics": {"arms": arms_metrics, "paired_differences": diffs,
                           "random_baseline": score.random_baseline(gt_by_seq)},
               "targets_check": target_check, "per_sequence_sealed": sealed_info, "sample_fps": sample_fps,
               "warnings": warnings, "scorer_wall_clock_s": round(time.time() - t0, 3)}
    except Exception as e:  # logged, then re-raised
        rec = {**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
               "labels_loaded": labels_loaded, "traceback": traceback.format_exc(limit=5), "warnings": warnings}
        common.append_run(rec, log_path)
        raise
    common.append_run(rec, log_path)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"{run_id}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def format_arms_report(rec: dict) -> str:
    lines = [f"run {rec['run_id']} split={rec['split']} purpose={rec['purpose']} primary={rec['primary_arm']}"]
    for name, m in rec["metrics"]["arms"].items():
        mm = copy.copy(m)
        mm.setdefault("per_sequence", {})  # sealed on test: nothing printed per sequence
        lines += ["", f"=== arm {name} ({rec['arms'][name]['pred_dir']})", score.format_report(mm, "")]
    lines += ["", "paired differences (b minus a; CI from the same sequence resamples):"]
    for key, d in rec["metrics"]["paired_differences"].items():
        md = d["macro_f1"]
        ci = md["ci95"]
        per = ", ".join(f"{b} {d['behaviors'][b]['diff']:+.4f}" for b in BEHAVIORS)
        lines.append(f"  {key}: macro {md['diff']:+.4f} "
                     f"[{ci[0]:+.4f}, {ci[1]:+.4f}] ({per})" if ci else f"  {key}: macro {md['diff']:+.4f}")
    tc = rec.get("targets_check")
    if tc:
        lines += ["", f"targets ({tc.get('rule')}):"]
        for level, res in tc.get("f1", {}).items():
            lines.append(f"  {level}: " + "; ".join(f"{k} {v['point']:.4f} vs {v['threshold']} {v['verdict']}"
                                                   for k, v in res.items()))
        lift = tc.get("tier1_lift_macro") or {}
        for level, v in lift.items():
            if isinstance(v, dict):
                lines.append(f"  tier-1 lift {level}: {v['point']:+.4f} vs +{v['threshold']} {v['verdict']}")
    if rec.get("per_sequence_sealed"):
        lines += ["", f"per-sequence metrics sealed in {rec['per_sequence_sealed']['path']} "
                      "(do not open before the test-2 decision is committed; EVAL_PLAN section 7)"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, help="dev | dev-holdout | test")
    ap.add_argument("--arm", action="append", required=True, help="name=pred_dir (repeat)")
    ap.add_argument("--primary", required=True)
    ap.add_argument("--compare", action="append", help="a:b, reports b minus a (repeat); default primary:each")
    ap.add_argument("--purpose")
    ap.add_argument("--frozen-config", type=Path)
    ap.add_argument("--targets", type=Path)
    ap.add_argument("--priority", default=",".join(score.DEFAULT_PRIORITY))
    ap.add_argument("--labels-dir", type=Path, default=common.LABELS_DIR)
    ap.add_argument("--split-file", type=Path, default=common.SPLIT_FILE)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--out-dir", type=Path, default=Path("eval/results"))
    ap.add_argument("--reps", type=int, default=10_000)
    ap.add_argument("--seal", action="store_true", help="seal per-sequence metrics (always on for test)")
    ap.add_argument("--confirm-test-run", action="store_true")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    arms = dict(parse_arm(s) for s in a.arm)
    if len(arms) != len(a.arm):
        raise SystemExit("duplicate arm name")
    compares = [tuple(c.split(":", 1)) for c in a.compare] if a.compare else None
    rec = run_arms(a.split, arms, a.primary, a.purpose, compares, a.frozen_config, a.targets,
                   tuple(a.priority.split(",")), a.labels_dir, a.split_file, a.log, a.out_dir, a.reps,
                   a.confirm_test_run, a.notes, a.seal or None)
    print(format_arms_report(rec))
    for w in rec["warnings"]:
        print("warning:", w, file=sys.stderr)
    print(f"\nlogged to {a.log}; full result {a.out_dir}/{rec['run_id']}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
