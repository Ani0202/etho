"""Demo-clip selection on CalMS21 test, after the no-test-2 decision (docs/EVAL_PLAN.md, amendment A3).

This is a descriptive, presentation-only analysis of test-1's *existing* `zs_t1` predictions. It makes no
predictions, no model calls, and changes nothing in the pipeline. Its output is a demo-clip choice, never
a test result. It is logged in eval/results/runs.jsonl as kind "analysis", purpose "demo-clip-selection":
a "started" record (prediction hashes) is written before any label file is opened.

Selection rule (fixed here, before it runs):
  - windows of 120, 180, 240, 300 s, starting on multiples of 10 s, fully inside the sequence;
  - ground truth in the window must contain >= 3 attack bouts and >= 3 mount bouts of >= 15 frames (0.5 s)
    each (the default `--min-bouts attack=3,mount=3`; on CalMS21 test no window qualifies, because attack and
    mount almost never occur in the same test video, so relaxed rules are run as separate, logged executions); bouts are maximal runs of one label, clipped to the window;
  - among qualifying windows of a sequence, the one with the highest zs_t1 macro F1 inside the window
    (ties: more investigation bouts, then the earlier start) represents that sequence;
  - sequences are ranked by that window's macro F1. Because Etho's own F1 is a ranking criterion, the
    chosen clip is favorable to Etho by construction and must never be presented as representative.

Outputs (`--out-dir`, default data/demo/, git-ignored): per candidate, an npz and a json with aligned
per-frame (30 fps) arrays `gt`, `zs_t1`, `zs_rules` in CalMS21 coding (attack 0, investigation 1,
mount 2, other 3). Also the largest "predicted attack on expert investigation" error run in the top
candidate, with the tier-1 windows (data/runs/T1/verdicts_test_zs_t1.jsonl) that overlap it.

    uv run python -m eval.demo_clip --run-dir data/runs/T1 --out-dir data/demo
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

import numpy as np

from eval import common, score
from eval.common import BEHAVIORS, NATIVE_FPS, VOCAB

WINDOWS_S = (120, 180, 240, 300)
STEP_S = 10
MIN_BOUT_FRAMES = 15
MIN_BOUTS = {"attack": 3, "mount": 3}
CODE = {v: k for k, v in VOCAB.items()}


def bouts(labels: np.ndarray, code: int, min_len: int = 1) -> list[tuple[int, int]]:
    """Maximal runs [start, end) of `code` with length >= min_len."""
    m = np.concatenate([[False], labels == code, [False]])
    d = np.flatnonzero(np.diff(m.astype(np.int8)))
    return [(int(a), int(b)) for a, b in zip(d[::2], d[1::2]) if b - a >= min_len]


def window_f1(gt: np.ndarray, pred: np.ndarray) -> dict:
    out = {}
    for b in BEHAVIORS:
        c = VOCAB[b]
        tp = int(np.sum((gt == c) & (pred == c)))
        fp = int(np.sum((gt != c) & (pred == c)))
        fn = int(np.sum((gt == c) & (pred != c)))
        out[b] = score.prf(tp, fp, fn)[2]
    out["macro"] = float(np.mean([out[b] for b in BEHAVIORS]))
    return out


def best_window(gt: np.ndarray, pred: np.ndarray, fps: int = NATIVE_FPS, min_bouts: dict | None = None) -> dict | None:
    """Best qualifying window of one sequence under the module's selection rule, or None."""
    min_bouts = MIN_BOUTS if min_bouts is None else min_bouts
    best = None
    for w in WINDOWS_S:
        n = w * fps
        for f0 in range(0, len(gt) - n + 1, STEP_S * fps):
            g = gt[f0:f0 + n]
            nb = {b: len(bouts(g, VOCAB[b], MIN_BOUT_FRAMES)) for b in BEHAVIORS}
            if any(nb[b] < k for b, k in min_bouts.items()):
                continue
            f1 = window_f1(g, pred[f0:f0 + n])
            key = (f1["macro"], nb["investigation"], -f0)
            if best is None or key > best["_key"]:
                best = {"_key": key, "f0": f0, "f1": f0 + n, "len_s": w, "bouts_min15": nb,
                        "bouts_all": {b: len(bouts(g, VOCAB[b])) for b in BEHAVIORS}, "f1_window": f1}
    if best:
        best.pop("_key")
    return best


def biggest_error(gt: np.ndarray, pred: np.ndarray, pred_code: int, gt_code: int, f_off: int = 0):
    """Largest run where pred == pred_code and gt == gt_code; frames are absolute (offset f_off)."""
    runs = bouts(((pred == pred_code) & (gt == gt_code)).astype(np.int8), 1)
    if not runs:
        return None
    a, b = max(runs, key=lambda r: r[1] - r[0])
    return {"f0": a + f_off, "f1": b + f_off, "n_frames": b - a}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, default=Path("data/runs/T1"))
    ap.add_argument("--out-dir", type=Path, default=Path("data/demo"))
    ap.add_argument("--n-candidates", type=int, default=3)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--min-bouts", default="attack=3,mount=3",
                    help="behavior=k[,behavior=k]: minimum >=0.5 s GT bouts in the window (default: the strict rule)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    min_bouts = {k: int(v) for k, v in (x.split("=") for x in a.min_bouts.split(",") if x)}
    seqs, _ = common.load_split("test")
    arms = {k: a.run_dir / k for k in ("zs_t1", "zs_rules")}
    files = {k: {s: score.find_pred_file(d, s) for s in seqs} for k, d in arms.items()}
    hashes = {k: score._hash_predictions(arms[k], files[k])[0] for k in arms}
    run_id = common.new_run_id("analysis-test")
    base = {"run_id": run_id, "kind": "analysis", "split": "test", "purpose": "demo-clip-selection",
            **common.git_info(), "predictions_sha256": hashes, "sequences": seqs,
            "notes": "presentation-only analysis of existing test-1 predictions after the no-test-2 decision "
                     "(EVAL_PLAN A3); not a test result",
            "rule": {"windows_s": WINDOWS_S, "step_s": STEP_S, "min_bout_frames": MIN_BOUT_FRAMES,
                     "min_bouts": min_bouts}, "user_notes": a.notes}
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, a.log)
    try:
        man = {k: score.read_manifest(d) for k, d in arms.items()}
        cands, data = [], {}
        for s in seqs:
            gt = common.load_sequence("test", s)["labels"]
            pz = {k: score.load_prediction(files[k][s], len(gt), man[k])[0] for k in arms}
            data[s] = (gt, pz)
            bw = best_window(gt, pz["zs_t1"], min_bouts=min_bouts)
            if bw:
                cands.append({"seq": s, **bw})
        cands.sort(key=lambda c: (-c["f1_window"]["macro"], c["seq"]))
        top = cands[: a.n_candidates]
        a.out_dir.mkdir(parents=True, exist_ok=True)
        exported = []
        for c in top:
            gt, pz = data[c["seq"]]
            sl = slice(c["f0"], c["f1"])
            stem = f"{c['seq']}_{c['f0']}_{c['f1']}"
            arrs = {"gt": gt[sl].astype(np.int8), "zs_t1": pz["zs_t1"][sl].astype(np.int8),
                    "zs_rules": pz["zs_rules"][sl].astype(np.int8)}
            np.savez_compressed(a.out_dir / f"{stem}.npz", frame_start=c["f0"], fps=NATIVE_FPS,
                                vocab=json.dumps(VOCAB), **arrs)
            (a.out_dir / f"{stem}.json").write_text(json.dumps(
                {"seq": c["seq"], "frame_start": c["f0"], "frame_end_exclusive": c["f1"], "fps": NATIVE_FPS,
                 "vocab": VOCAB, "source": "CalMS21 Task 1 test annotations (annotator1), CC BY 4.0, "
                 "Sun et al. 2021; Etho zs_t1/zs_rules predictions from test-1 (run T1)",
                 **{k: v.tolist() for k, v in arrs.items()}}))
            exported.append({"stem": stem, "npz_sha256": common.sha256_file(a.out_dir / f"{stem}.npz"),
                             "json_sha256": common.sha256_file(a.out_dir / f"{stem}.json")})
        err = None
        if top:
            c = top[0]
            gt, pz = data[c["seq"]]
            sl = slice(c["f0"], c["f1"])
            err = biggest_error(gt[sl], pz["zs_t1"][sl], VOCAB["attack"], VOCAB["investigation"], c["f0"])
            if err:
                e_rules = pz["zs_rules"][err["f0"]:err["f1"]]
                err["zs_rules_labels_in_run"] = {CODE[k]: int(v) for k, v in
                                                 zip(*np.unique(e_rules, return_counts=True))}
                vs = [json.loads(x) for x in (a.run_dir / "verdicts_test_zs_t1.jsonl").read_text().splitlines()
                      if x.strip()]
                step = NATIVE_FPS // 10
                err["tier1_windows"] = [
                    {k: v.get(k) for k in ("s0", "s1", "rule_label", "label", "confidence", "reason", "source",
                                           "cache_hit")} | {"frames_30fps": [v["s0"] * step, v["s1"] * step]}
                    for v in vs if v["seq"] == c["seq"] and v["s0"] * step < err["f1"]
                    and v["s1"] * step > err["f0"]]
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
               "n_qualifying_sequences": len(cands), "candidates": cands, "exported": exported,
               "out_dir": str(a.out_dir), "error_example": err}
    except Exception as e:
        common.append_run({**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
                           "traceback": traceback.format_exc(limit=5)}, a.log)
        raise
    common.append_run(rec, a.log)
    print(json.dumps({k: rec[k] for k in ("run_id", "n_qualifying_sequences", "exported")}, indent=1))
    for c in cands:
        print(c["seq"], c["f0"] / NATIVE_FPS, c["f1"] / NATIVE_FPS, c["bouts_min15"], c["bouts_all"],
              {k: round(v, 4) for k, v in c["f1_window"].items()})
    print(json.dumps(err, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
