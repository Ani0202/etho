import json
from pathlib import Path

import numpy as np
import pytest

from eval.common import BEHAVIORS, VOCAB
from eval.showcase_check import (
    bout_category,
    decide,
    far_below_average,
    merged_count,
    per_video_summary,
    present_macro,
    prf_table,
)


def test_merged_count_merges_only_gaps_strictly_below_threshold():
    bs = [(0, 10), (14, 20), (25, 30), (60, 70)]  # gaps 4, 5, 30 samples
    assert merged_count(bs, 5) == 3   # only the gap of 4 merges
    assert merged_count(bs, 6) == 2
    assert merged_count(bs, 31) == 1
    assert merged_count([], 5) == 0


def test_decide_mirrors_app_restriction():
    assert decide({"label": "mount", "rule_label": "mount", "confidence": 0.9}) == "agreed_with_rule"
    assert decide({"label": "attack", "rule_label": "mount", "confidence": 0.9}) == "set_aside_by_restriction"
    assert decide({"label": "investigation", "rule_label": "mount", "confidence": 0.9}) == "applied"
    low = "set_aside_low_confidence_or_unsure"
    assert decide({"label": "none", "rule_label": "mount", "confidence": 0.59}) == low
    assert decide({"label": "unsure", "rule_label": "mount", "confidence": 0.99}) == low


def test_bout_category():
    assert bout_category([]).startswith("rule-only")
    assert bout_category(["agreed_with_rule"]).startswith("confirmed")
    assert bout_category(["set_aside_by_restriction"]).startswith("set aside")
    assert bout_category(["agreed_with_rule", "set_aside_by_restriction"]).startswith("other")


# ---- regression tests for the metric path (Codex review 2026-10-31 P2). Synthetic arrays and committed numbers
# only: no label files, no network, no model calls.
ATK, INV, MNT, OTH = VOCAB["attack"], VOCAB["investigation"], VOCAB["mount"], VOCAB["other"]
ROOT = Path(__file__).resolve().parents[2]
PER_SEQ = ROOT / "eval/results/score-test-20261028T164333Z-473c64.per_sequence.sealed.json"
RECORDED = ROOT / "eval/results/showcase_mouse075_check.json"


def _synthetic():
    # frame:        0    1    2    3    4    5    6    7    8    9
    gt = np.array([ATK, ATK, ATK, INV, INV, INV, OTH, OTH, OTH, OTH], np.int8)
    pred = np.array([ATK, ATK, INV, INV, OTH, ATK, OTH, OTH, MNT, OTH], np.int8)
    return gt, pred


def test_prf_table_hand_counts():
    t = prf_table(*_synthetic())
    # attack: TP {0,1}, FP {5}, FN {2}
    assert (t["attack"]["tp"], t["attack"]["fp"], t["attack"]["fn"]) == (2, 1, 1)
    assert t["attack"]["precision"] == pytest.approx(2 / 3)
    assert t["attack"]["recall"] == pytest.approx(2 / 3)
    assert t["attack"]["f1"] == pytest.approx(2 / 3)  # 2*2 / (2*2 + 1 + 1)
    # investigation: TP {3}, FP {2}, FN {4,5}
    assert (t["investigation"]["tp"], t["investigation"]["fp"], t["investigation"]["fn"]) == (1, 1, 2)
    assert t["investigation"]["precision"] == pytest.approx(0.5)
    assert t["investigation"]["recall"] == pytest.approx(1 / 3)
    assert t["investigation"]["f1"] == pytest.approx(0.4)  # 2 / (2 + 1 + 2)
    # mount: absent from gt, one false positive {8}
    assert (t["mount"]["tp"], t["mount"]["fp"], t["mount"]["fn"]) == (0, 1, 0)
    assert (t["mount"]["precision"], t["mount"]["recall"], t["mount"]["f1"]) == (0.0, 0.0, 0.0)
    assert {b: t[b]["gt_frames"] for b in BEHAVIORS} == {"attack": 3, "investigation": 3, "mount": 0}
    assert {b: t[b]["pred_frames"] for b in BEHAVIORS} == {"attack": 3, "investigation": 2, "mount": 1}
    # the window macro (demo_clip.window_f1) is unweighted over all three behaviors, absent ones included
    assert t["macro"] == pytest.approx((2 / 3 + 0.4 + 0.0) / 3)


def test_prf_table_absent_and_unpredicted_behavior_scores_zero():
    gt = np.array([ATK, ATK, OTH, OTH], np.int8)
    t = prf_table(gt, gt.copy())
    assert t["attack"]["f1"] == 1.0
    for b in ("investigation", "mount"):  # zero denominators give 0.0, as in score.prf
        assert (t[b]["tp"], t[b]["fp"], t[b]["fn"], t[b]["f1"]) == (0, 0, 0, 0.0)
    assert t["macro"] == pytest.approx(1 / 3)


def test_present_macro_averages_only_behaviors_in_ground_truth():
    t = prf_table(*_synthetic())
    # mount is absent from gt (its F1 of 0 from the false positive is excluded): mean of attack and investigation
    assert present_macro(t) == pytest.approx((2 / 3 + 0.4) / 2)
    # a present behavior with F1 = 0 is included, an absent one with any F1 is not
    entry = {"attack": {"f1": 0.0, "gt_frames": 5}, "investigation": {"f1": 0.6, "gt_frames": 9},
             "mount": {"f1": 0.9, "gt_frames": 0}}
    assert present_macro(entry) == pytest.approx(0.3)


def _nineteen():
    vals = [0.30 + 0.02 * i for i in range(19)]  # sorted: 0.30, 0.32, ..., 0.66
    order = [7, 0, 18, 3, 11, 5, 14, 1, 9, 16, 2, 12, 6, 17, 4, 10, 15, 8, 13]  # shuffled insertion order
    return {f"v{j:02d}": vals[j] for j in order}


def test_per_video_summary_hand_values():
    s = per_video_summary(_nineteen())
    assert s["n"] == 19
    # numpy linear p25: position 0.25 * (19 - 1) = 4.5 -> midway between sorted[4] = 0.38 and sorted[5] = 0.40
    assert s["p25_linear"] == pytest.approx(0.39)
    assert s["p25_nearest_rank"] == pytest.approx(0.38)  # rank ceil(0.25 * 19) = 5 -> sorted[4]
    assert s["p25_lower"] == pytest.approx(0.38)
    assert s["p25_higher"] == pytest.approx(0.40)
    assert s["median"] == pytest.approx(0.48)  # sorted[9]
    assert s["mean"] == pytest.approx(0.48)
    assert [v for _, v in s["sorted"]] == sorted(_nineteen().values())


def test_per_video_summary_reproduces_recorded_p25_and_median_from_committed_counts():
    per_seq = json.loads(PER_SEQ.read_text())["arms"]["zs_t1"]["per_sequence"]
    assert len(per_seq) == 19
    # F1 recomputed from the committed TP / FP / FN counts, not read from the stored f1 fields
    pv = {}
    for seq, e in per_seq.items():
        f1s = []
        for b in BEHAVIORS:
            tp, fp, fn = e[b]["tp"], e[b]["fp"], e[b]["fn"]
            if tp + fn > 0:  # present in ground truth (gt_frames = tp + fn)
                assert tp + fn == e[b]["gt_frames"]
                f1s.append(2 * tp / (2 * tp + fp + fn))
        pv[seq] = float(np.mean(f1s))
        assert pv[seq] == pytest.approx(present_macro(e), abs=1e-12)
    s = per_video_summary(pv)
    assert s["p25_linear"] == pytest.approx(0.465034661, abs=5e-10)
    assert s["median"] == pytest.approx(0.506128246, abs=5e-10)
    # and the script's path (present_macro on the stored entries) gives the recorded output exactly
    rec = json.loads(RECORDED.read_text())["test_averages"]["per_video_macro"]
    s2 = per_video_summary({seq: present_macro(e) for seq, e in per_seq.items()})
    for k in ("n", "mean", "median", "p25_linear", "p25_nearest_rank", "p25_lower", "p25_higher"):
        assert s2[k] == rec[k], k
    assert [list(x) for x in s2["sorted"]] == rec["sorted"]


def test_far_below_average_is_strict_equality_is_not_below():
    s = per_video_summary(_nineteen())  # p25: linear 0.39, nearest-rank / lower 0.38, higher 0.40
    p25 = s["p25_linear"]
    # equality with the threshold is NOT "below the 25th percentile" (DECISIONS 2026-10-30): not far below
    assert far_below_average(p25, s) == (False, False)  # higher (0.40) would say below -> not robust
    assert far_below_average(np.nextafter(p25, 0.0), s) == (True, False)  # just below linear, above 0.38
    assert far_below_average(0.37, s) == (True, True)
    assert far_below_average(0.41, s) == (False, True)
    # equality at the lower definitions: 0.38 is below linear and higher but not below nearest-rank / lower
    assert far_below_average(0.38, s) == (True, False)


def test_far_below_average_reproduces_recorded_decision():
    d = json.loads(RECORDED.read_text())
    far, robust = far_below_average(d["showcase"]["final"]["macro"], d["test_averages"]["per_video_macro"])
    assert far is d["rule"]["far_below_average"] is True
    assert robust is d["rule"]["same_outcome_under_all_p25_definitions"]
