"""Representativeness check of the web-app showcase `data/showcase/calms21-mouse075-live/` (report only).

Applies the rule committed in docs/DECISIONS.md (2026-10-30 04:40 UTC) *before* these numbers were computed:
  (1) the showcase is "far below average" if its macro F1 over 0-300 s is below the 25th percentile of the 19
      per-video test-1 macro F1s (test1_report section 12: `zs_t1`, mean F1 over the behaviors present in the video);
  (2) if so, the proposed replacement is the test video with >= 1 expert attack bout whose test-1 macro F1 is
      nearest the median of the 19 (ties: more expert attack frames), and its window is the 300 s window starting
      on a whole minute with the most expert attack frames (ties: earliest).

Descriptive and presentation-only: no model calls, no new predictions, nothing in the pipeline or post-processing
is changed. Frame-level F1 is computed exactly as in test-1 (eval/score.py): 10 fps predictions upsampled to 30 fps
by nearest sample (`score.upsample_samples`), compared with the 30 fps CalMS21 annotator-1 labels; per-window macro
is `demo_clip.window_f1` (the unweighted mean over attack / investigation / mount). Logged in
eval/results/runs.jsonl as kind "analysis", split "test", purpose "showcase-representativeness": a "started" record
(input hashes) is written before any label file is opened.

    uv run python -m eval.showcase_check
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

import numpy as np

from eval import common, score
from eval.common import BEHAVIORS, NATIVE_FPS, VOCAB
from eval.demo_clip import bouts, window_f1

SHOWCASE = Path("data/showcase/calms21-mouse075-live")
EXPERT_10FPS = Path("data/demo/expert_mouse075_task1_annotator1.npz")
RUN_DIR = Path("data/runs/T1")
SCORE_RESULT = Path("eval/results/score-test-20261028T164333Z-473c64.json")
PER_SEQ = Path("eval/results/score-test-20261028T164333Z-473c64.per_sequence.sealed.json")
SEQ = "mouse075_task1_annotator1"
STEP = 3  # 30 fps / 10 fps
WIN_FRAMES = 300 * NATIVE_FPS
CODE = {v: k for k, v in VOCAB.items()}
ALLOW = frozenset({"investigation", "none"})  # etho.app.behaviors.tier1_allow for the CalMS21 trio
CONFIDENT = 0.6  # etho.adjudication.tier1.CONFIDENT


def prf_table(gt: np.ndarray, pred: np.ndarray) -> dict:
    out = {}
    for b in BEHAVIORS:
        c = VOCAB[b]
        tp = int(np.sum((gt == c) & (pred == c)))
        fp = int(np.sum((gt != c) & (pred == c)))
        fn = int(np.sum((gt == c) & (pred != c)))
        p, r, f = score.prf(tp, fp, fn)
        out[b] = {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f,
                  "gt_frames": int(np.sum(gt == c)), "pred_frames": int(np.sum(pred == c))}
    out["macro"] = window_f1(gt, pred)["macro"]
    return out


def present_macro(per_seq_entry: dict) -> float:
    """test1_report 12.1 last column: mean F1 over the behaviors present in the video's ground truth."""
    return float(np.mean([per_seq_entry[b]["f1"] for b in BEHAVIORS if per_seq_entry[b]["gt_frames"] > 0]))


def per_video_summary(pv: dict[str, float]) -> dict:
    """n, mean, median and the 25th percentile (numpy linear plus three alternatives) of per-video macro F1s."""
    vals = np.array(sorted(pv.values()))
    srt = sorted(pv.items(), key=lambda x: x[1])
    return {"n": len(vals), "mean": float(vals.mean()), "median": float(np.median(vals)),
            "p25_linear": float(np.percentile(vals, 25)),
            "p25_nearest_rank": float(vals[int(np.ceil(0.25 * len(vals))) - 1]),
            "p25_lower": float(np.percentile(vals, 25, method="lower")),
            "p25_higher": float(np.percentile(vals, 25, method="higher")),
            "sorted": srt}


def far_below_average(macro: float, per_video: dict) -> tuple[bool, bool]:
    """Rule (1): strictly below the linear p25 (equality is not "below"); and whether every p25 definition agrees."""
    far_below = bool(macro < per_video["p25_linear"])
    robust = all((macro < per_video[k]) == far_below for k in ("p25_linear", "p25_nearest_rank", "p25_lower",
                                                               "p25_higher"))
    return far_below, robust


def merged_count(bs: list[tuple[int, int]], max_gap: int) -> int:
    """Events left after merging consecutive bouts whose gap (next start - previous end) is < max_gap."""
    if not bs:
        return 0
    n = 1
    for (_, e0), (s1, _) in zip(bs[:-1], bs[1:], strict=True):
        if s1 - e0 >= max_gap:
            n += 1
    return n


def decide(v: dict) -> str:
    """Mirror of etho.app.progress.decide (read, not imported): how the app's tier-1 `apply` treated a verdict."""
    lab, conf = v.get("label"), float(v.get("confidence") or 0.0)
    if lab == "unsure" or conf < CONFIDENT:
        return "set_aside_low_confidence_or_unsure"
    if lab not in ALLOW and lab == v.get("rule_label"):
        return "agreed_with_rule"
    if lab not in ALLOW:
        return "set_aside_by_restriction"
    return "applied"


def bout_category(decisions: list[str]) -> str:
    if not decisions:
        return "rule-only (no tier-1 window touches it)"
    s = set(decisions)
    if s == {"agreed_with_rule"}:
        return "confirmed (tier 1 also said mount)"
    if s <= {"set_aside_by_restriction", "set_aside_low_confidence_or_unsure"}:
        return "set aside (tier-1 verdict not applied)"
    return "other (mixed: " + ", ".join(sorted(s)) + ")"


def mix(gt: np.ndarray) -> dict:
    n = len(gt)
    return {k: (float(np.sum(gt == c)) / n if n else 0.0) for k, c in VOCAB.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("eval/results/showcase_mouse075_check.json"))
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    a = ap.parse_args(argv)
    inputs = {str(p): common.sha256_file(p) for p in
              [SHOWCASE / "labels.npz", SHOWCASE / "labels_rules.npz", SHOWCASE / "verdicts.jsonl",
               SHOWCASE / "result.json", SHOWCASE / "detectors.json", RUN_DIR / "zs_t1" / f"{SEQ}.npz",
               RUN_DIR / "zs_rules" / f"{SEQ}.npz", RUN_DIR / "verdicts_test_zs_t1.jsonl", PER_SEQ, SCORE_RESULT]}
    run_id = common.new_run_id("analysis-test")
    base = {"run_id": run_id, "kind": "analysis", "split": "test", "purpose": "showcase-representativeness",
            **common.git_info(), "inputs_sha256": inputs, "cost_usd": 0.0, "model_calls": 0,
            "rule": "docs/DECISIONS.md 2026-10-30 04:40 UTC",
            "notes": "presentation-only check of the web-app showcase against test-1; not a test result"}
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, a.log)
    try:
        out = {"run_id": run_id, **run()}
        a.out.write_text(json.dumps(out, indent=1, sort_keys=True))
    except Exception as e:
        common.append_run({**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
                           "traceback": traceback.format_exc(limit=5)}, a.log)
        raise
    common.append_run({**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
                       "output": str(a.out), "output_sha256": common.sha256_file(a.out),
                       "metrics": {"showcase_final_macro": out["showcase"]["final"]["macro"],
                                   "showcase_rules_macro": out["showcase"]["rules_only"]["macro"],
                                   "p25_per_video_macro": out["test_averages"]["per_video_macro"]["p25_linear"],
                                   "far_below_average": out["rule"]["far_below_average"]}}, a.log)
    print(json.dumps({k: out[k] for k in ("run_id", "rule")}, indent=1))
    return 0


def run() -> dict:
    res = json.loads((SHOWCASE / "result.json").read_text())
    if float(res["fps"]) != 10.0 or int(res["n_samples"]) != 3000:
        raise ValueError("showcase is not 3000 samples at 10 fps")
    final10 = np.load(SHOWCASE / "labels.npz")["labels"].astype(np.int8)
    rules10 = np.load(SHOWCASE / "labels_rules.npz")["labels"].astype(np.int8)
    if res["behaviors"]["codes"] != VOCAB:
        raise ValueError("showcase codes differ from CalMS21 vocab")

    # ---- labels (first label read happens here, after the started record)
    gt_full = common.load_sequence("test", SEQ)["labels"]
    gt = gt_full[:WIN_FRAMES]
    exp10 = np.load(EXPERT_10FPS)["labels"].astype(np.int8)
    checks = {"expert_10fps_equals_gt_every_3rd_frame": bool(np.array_equal(exp10, gt_full[::STEP])),
              "expert_10fps_len": int(len(exp10)), "gt_full_frames": int(len(gt_full))}
    exp10w = exp10[:3000]

    # ---- 1. showcase F1 (30 fps, test-1 definition) and a 10 fps cross-check
    up = {k: score.upsample_samples(v, WIN_FRAMES, STEP) for k, v in (("final", final10), ("rules_only", rules10))}
    showcase = {k: prf_table(gt, v) for k, v in up.items()}
    showcase_10fps = {k: prf_table(exp10w, v) for k, v in (("final", final10), ("rules_only", rules10))}

    # ---- 2. test-1 mouse075: window and whole video
    t1 = {}
    for arm in ("zs_t1", "zs_rules"):
        man = score.read_manifest(RUN_DIR / arm)
        pred, _, _ = score.load_prediction(RUN_DIR / arm / f"{SEQ}.npz", len(gt_full), man)
        raw10 = np.load(RUN_DIR / arm / f"{SEQ}.npz")["labels"].astype(np.int8)
        t1[arm] = {"window_0_300": prf_table(gt, pred[:WIN_FRAMES]), "whole_video": prf_table(gt_full, pred),
                   "_raw10": raw10}
    per_seq = json.loads(PER_SEQ.read_text())["arms"]
    ws = per_seq["zs_t1"]["per_sequence"][SEQ]
    for b in BEHAVIORS:  # recomputed whole-video numbers must equal the logged test-1 ones
        if t1["zs_t1"]["whole_video"][b]["tp"] != ws[b]["tp"] or t1["zs_t1"]["whole_video"][b]["fp"] != ws[b]["fp"]:
            raise ValueError(f"recomputed test-1 {b} counts differ from the logged per-sequence result")
    checks["test1_whole_video_recompute_matches_logged"] = True

    # ---- 3. test averages
    arms_m = json.loads(SCORE_RESULT.read_text())["metrics"]["arms"]["zs_t1"]
    pooled = {"macro": arms_m["macro_f1"], **{b: arms_m["behaviors"][b]["f1"] for b in BEHAVIORS}}
    pv = {s: present_macro(e) for s, e in per_seq["zs_t1"]["per_sequence"].items()}
    per_video = per_video_summary(pv)

    # ---- 4. rule
    p25 = per_video["p25_linear"]
    sc_macro = showcase["final"]["macro"]
    far_below, robust = far_below_average(sc_macro, per_video)
    rule = {"metric": "showcase final labels, macro F1 0-300 s, 30 fps (displayed result)",
            "showcase_macro": sc_macro, "threshold_p25": p25, "threshold_definition": "numpy linear (default)",
            "far_below_average": far_below, "same_outcome_under_all_p25_definitions": robust,
            "rules_only_macro_vs_p25": bool(showcase["rules_only"]["macro"] < p25)}
    # Replacement computed either way (descriptive); the rule only proposes it if far_below is true.
    med = per_video["median"]
    att_seqs = []
    for s in per_seq["zs_t1"]["per_sequence"]:
        g = common.load_sequence("test", s)["labels"]
        if bouts(g, VOCAB["attack"]):
            att_seqs.append({"seq": s, "macro": pv[s], "dist_to_median": abs(pv[s] - med),
                             "attack_frames": int(np.sum(g == VOCAB["attack"])), "_gt": g})
    att_seqs.sort(key=lambda c: (c["dist_to_median"], -c["attack_frames"]))
    gts = {c["seq"]: c.pop("_gt") for c in att_seqs}
    pick = att_seqs[0]
    g = gts[pick["seq"]]
    wins = []
    for f0 in range(0, len(g) - WIN_FRAMES + 1, 60 * NATIVE_FPS):
        n_att = int(np.sum(g[f0:f0 + WIN_FRAMES] == VOCAB["attack"]))
        wins.append({"start_s": f0 // NATIVE_FPS, "attack_frames": n_att})
    best = sorted(wins, key=lambda w: (-w["attack_frames"], w["start_s"]))[0]
    f0 = best["start_s"] * NATIVE_FPS
    man = score.read_manifest(RUN_DIR / "zs_t1")
    pr, _, _ = score.load_prediction(RUN_DIR / "zs_t1" / f"{pick['seq']}.npz", len(g), man)
    gw = g[f0:f0 + WIN_FRAMES]
    repl = {"seq": pick["seq"], "whole_video_present_macro": pick["macro"],
            "whole_video_f1": {b: per_seq["zs_t1"]["per_sequence"][pick["seq"]][b]["f1"] for b in BEHAVIORS},
            "whole_video_gt_frames": {b: per_seq["zs_t1"]["per_sequence"][pick["seq"]][b]["gt_frames"]
                                      for b in BEHAVIORS},
            "window_s": [best["start_s"], best["start_s"] + 300], "window_frames": [f0, f0 + WIN_FRAMES],
            "window_f1": prf_table(gw, pr[f0:f0 + WIN_FRAMES]),
            "window_present_macro": float(np.mean([window_f1(gw, pr[f0:f0 + WIN_FRAMES])[b] for b in BEHAVIORS
                                                   if np.any(gw == VOCAB[b])])),
            "window_expert_bouts_all": {b: len(bouts(gw, VOCAB[b])) for b in BEHAVIORS},
            "window_expert_bouts_min_0p5s": {b: len(bouts(gw, VOCAB[b], 15)) for b in BEHAVIORS},
            "window_expert_frames": {b: int(np.sum(gw == VOCAB[b])) for b in BEHAVIORS},
            "candidate_windows": wins,
            "candidates": [{k: v for k, v in c.items()} for c in att_seqs]}
    rule["replacement_if_far_below"] = repl
    rule["replacement_proposed"] = far_below

    # ---- 5. mount fragments (10 fps samples, the app's own resolution)
    verd = [json.loads(x) for x in (SHOWCASE / "verdicts.jsonl").read_text().splitlines() if x.strip()]
    for v in verd:
        v["decision"] = decide(v)
    mb = bouts(final10, VOCAB["mount"])
    rows = []
    for s0, s1 in mb:
        touch = [v for v in verd if v["s0"] < s1 and s0 < v["s1"]]
        rule_cover = rules10[s0:s1]
        rows.append({"s0": s0, "s1": s1, "start_s": s0 / 10, "end_s": s1 / 10, "dur_s": (s1 - s0) / 10,
                     "expert_mix": mix(exp10w[s0:s1]),
                     "rules_only_all_mount": bool(np.all(rule_cover == VOCAB["mount"])),
                     "tier1_windows": [{k: v[k] for k in ("s0", "s1", "rule_label", "source", "label", "confidence",
                                                          "decision")} for v in touch],
                     "tier1_category": bout_category([v["decision"] for v in touch])})
    gaps = [(b[0] - a[1]) / 10 for a, b in zip(mb[:-1], mb[1:], strict=True)]
    rule_mb = bouts(rules10, VOCAB["mount"])
    t1_final_mb = bouts(t1["zs_t1"]["_raw10"][:3000], VOCAB["mount"])
    t1_rules_mb = bouts(t1["zs_rules"]["_raw10"][:3000], VOCAB["mount"])
    t1_verd = [json.loads(x) for x in (RUN_DIR / "verdicts_test_zs_t1.jsonl").read_text().splitlines() if x.strip()]
    t1_verd = [v for v in t1_verd if v["seq"] == SEQ and v["s0"] < 3000]
    for v in t1_verd:
        v["decision"] = decide(v)
    sc_keys = {(v["s0"], v["s1"]): v for v in verd}
    t1_keys = {(v["s0"], v["s1"]): v for v in t1_verd}
    common_w = sorted(set(sc_keys) & set(t1_keys))

    def dcount(vs):
        out = {}
        for v in vs:
            k = f"{v['rule_label']}->{v['label']}:{v['decision']}"
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))

    fragments = {
        "showcase_final_mount_bouts": rows, "n_showcase_final_mount_bouts": len(mb), "gaps_s": gaps,
        "merged_counts": {f"gap_lt_{t}s": merged_count(mb, int(round(t * 10))) for t in (0.5, 1, 2)},
        "expert_mount_bouts_10fps": bouts(exp10w, VOCAB["mount"]),
        "showcase_rules_only_mount_bouts": len(rule_mb),
        "test1_zs_t1_mount_bouts_0_300": len(t1_final_mb), "test1_zs_t1_mount_bouts_list": t1_final_mb,
        "test1_zs_rules_mount_bouts_0_300": len(t1_rules_mb),
        "test1_zs_t1_merged_counts": {f"gap_lt_{t}s": merged_count(t1_final_mb, int(round(t * 10)))
                                      for t in (0.5, 1, 2)},
        "differences": {
            "rules_only_sample_agreement_showcase_vs_test1": float(np.mean(rules10 == t1["zs_rules"]["_raw10"][:3000])),
            "final_sample_agreement_showcase_vs_test1": float(np.mean(final10 == t1["zs_t1"]["_raw10"][:3000])),
            "mount_samples": {"showcase_rules": int(np.sum(rules10 == 2)), "showcase_final": int(np.sum(final10 == 2)),
                              "test1_rules": int(np.sum(t1["zs_rules"]["_raw10"][:3000] == 2)),
                              "test1_final": int(np.sum(t1["zs_t1"]["_raw10"][:3000] == 2))},
            "tier1_windows": {"showcase": len(verd), "test1_in_0_300": len(t1_verd),
                              "identical_s0_s1": len(common_w),
                              "same_label_on_identical_windows": sum(sc_keys[k]["label"] == t1_keys[k]["label"]
                                                                     for k in common_w)},
            "tier1_decisions_showcase": dcount(verd), "tier1_decisions_test1_0_300": dcount(t1_verd),
        },
    }
    for arm in t1:
        t1[arm].pop("_raw10")
    return {"checks": checks, "showcase": showcase, "showcase_10fps_crosscheck": showcase_10fps,
            "test1_mouse075": t1, "test_averages": {"pooled_zs_t1": pooled, "per_video_macro": per_video},
            "rule": rule, "mount_fragments": fragments}


if __name__ == "__main__":
    raise SystemExit(main())
