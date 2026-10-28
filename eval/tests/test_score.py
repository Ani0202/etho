"""Tests for eval/score.py on synthetic data. No network, no real labels."""

import json

import numpy as np
import pytest

from eval import common, score
from eval.tests.synth import A, I, M, O, write_dataset


def ovr(excl):
    return {b: excl == common.VOCAB[b] for b in common.BEHAVIORS}


def test_prf_zero_division():
    assert score.prf(0, 0, 0) == (0.0, 0.0, 0.0)
    assert score.prf(5, 5, 0) == (0.5, 1.0, pytest.approx(2 / 3))


def test_rasterize_end_exclusive_and_priority():
    bouts = [{"behavior": "attack", "start_frame": 10, "end_frame": 20},
             {"behavior": "mount", "start_frame": 15, "end_frame": 25},
             {"behavior": "investigation", "start_frame": 0, "end_frame": 12}]
    excl, o = score.rasterize_bouts(bouts, 30)
    assert o["attack"].sum() == 10 and o["mount"].sum() == 10 and o["investigation"].sum() == 12
    assert list(excl[8:27]) == [I, I, A, A, A, A, A, M, M, M, M, M, M, M, M, M, M, O, O]
    excl2, _ = score.rasterize_bouts(bouts, 30, priority=("attack", "mount", "investigation"))
    assert list(excl2[15:21]) == [A, A, A, A, A, M]
    excl3, _ = score.rasterize_bouts(bouts, 30, priority=("investigation", "attack", "mount"))
    assert list(excl3[9:13]) == [I, I, I, A]
    with pytest.raises(ValueError):
        score.rasterize_bouts(bouts, 30, priority=("mount", "attack"))


def test_rasterize_validation_and_clipping():
    w = []
    excl, _ = score.rasterize_bouts([{"behavior": "attack", "start_frame": 25, "end_frame": 40},
                                     {"behavior": "mount", "start_frame": 50, "end_frame": 60}], 30, warnings=w)
    assert (excl[25:] == A).all() and len(w) == 2
    for bad in ({"behavior": "attack", "start_frame": 5, "end_frame": 5},
                {"behavior": "attack", "start_frame": -1, "end_frame": 5},
                {"behavior": "chase", "start_frame": 0, "end_frame": 5}):
        with pytest.raises(ValueError):
            score.rasterize_bouts([bad], 30)


def test_upsample_nearest_sample():
    samples = np.array([A, I, M, O], np.int8)  # samples at frames 0, 3, 6, 9
    out = score.upsample_samples(samples, 11, 3)  # ceil(11/3) = 4 samples expected
    # frame: 0 1 2 3 4 5 6 7 8 9 10 -> sample 0 0 1 1 1 2 2 2 3 3 3
    assert list(out) == [A, A, I, I, I, M, M, M, O, O, O]
    out12 = score.upsample_samples(samples, 12, 3)  # frame 11 -> round(3.67)=4, clamped to 3
    assert out12[11] == O and len(out12) == 12
    w = []
    short = score.upsample_samples(samples[:2], 11, 3, warnings=w)  # samples 2, 3 missing
    assert list(short) == [A, A, I, I, I] + [O] * 6 and w
    with pytest.raises(ValueError):
        score.upsample_samples(np.zeros(5, np.int8), 11, 3)


def test_pooled_differs_from_per_sequence_mean():
    # seq1: 100 attack frames, all found. seq2: 10 attack frames, none found, 0 FP.
    g1 = np.array([A] * 100 + [O] * 100, np.int8)
    g2 = np.array([A] * 10 + [O] * 190, np.int8)
    p1, p2 = g1.copy(), np.full(200, O, np.int8)
    m = score.score_split({"s1": g1, "s2": g2}, {"s1": (p1, ovr(p1)), "s2": (p2, ovr(p2))}, reps=200)
    a = m["behaviors"]["attack"]
    assert (a["tp"], a["fp"], a["fn"]) == (100, 0, 10)
    assert a["f1"] == pytest.approx(200 / 210)  # pooled
    assert m["per_sequence_summary"]["attack"]["mean_f1"] == pytest.approx(0.5)  # per-sequence mean
    # investigation and mount absent everywhere and never predicted: F1 0 by zero-division rule.
    assert m["macro_f1"] == pytest.approx(np.mean([200 / 210, 0, 0]))


def test_perfect_prediction_and_confusion():
    g = np.array([A] * 30 + [I] * 40 + [M] * 20 + [O] * 10, np.int8)
    m = score.score_split({"s": g}, {"s": (g.copy(), ovr(g))}, reps=50)
    assert m["macro_f1"] == 1.0
    assert all(m["behaviors"][b]["f1"] == 1.0 for b in common.BEHAVIORS)
    assert np.array(m["confusion"]["matrix"]).trace() == 100
    assert m["macro_f1_ci95"] == [1.0, 1.0]


def test_ovr_metric_ignores_exclusivity():
    g = np.array([M] * 10 + [O] * 10, np.int8)
    bouts = [{"behavior": "mount", "start_frame": 0, "end_frame": 10},
             {"behavior": "attack", "start_frame": 0, "end_frame": 10}]
    excl, o = score.rasterize_bouts(bouts, 20)
    m = score.score_split({"s": g}, {"s": (excl, o)}, reps=20)
    assert m["behaviors"]["attack"]["pred_frames"] == 0  # exclusive: mount wins
    assert m["ovr_no_exclusivity"]["behaviors"]["attack"]["fp"] == 10


def test_bootstrap_deterministic_and_brackets_point():
    rng = np.random.default_rng(1)
    gts, preds = {}, {}
    for i in range(8):
        g = rng.choice([A, I, M, O], size=600, p=[0.1, 0.3, 0.1, 0.5]).astype(np.int8)
        p = g.copy()
        flip = rng.random(600) < 0.3
        p[flip] = O
        gts[f"s{i}"], preds[f"s{i}"] = g, (p, ovr(p))
    m1 = score.score_split(gts, preds, reps=2000)
    m2 = score.score_split(gts, preds, reps=2000)
    assert m1["macro_f1_ci95"] == m2["macro_f1_ci95"]
    lo, hi = m1["macro_f1_ci95"]
    assert lo <= m1["macro_f1"] <= hi and hi - lo < 0.2
    for b in common.BEHAVIORS:
        lo, hi = m1["behaviors"][b]["ci95"]["f1"]
        assert lo <= m1["behaviors"][b]["f1"] <= hi


def test_bootstrap_flags_resamples_without_ground_truth():
    g_att = np.array([A] * 50 + [O] * 50, np.int8)
    g_none = np.full(100, O, np.int8)
    gts = {"a": g_att, **{f"n{i}": g_none for i in range(4)}}
    preds = {k: (v.copy(), ovr(v)) for k, v in gts.items()}
    m = score.score_split(gts, preds, reps=4000)
    frac = m["behaviors"]["attack"]["ci95_no_gt_resample_frac"]
    assert frac == pytest.approx((4 / 5) ** 5, abs=0.03)


def test_bout_matching_one_to_one_and_iou():
    g = np.array([O] * 10 + [A] * 10 + [O] * 5 + [A] * 10 + [O] * 15, np.int8)  # bouts [10,20), [25,35)
    p = np.array([O] * 10 + [A] * 25 + [O] * 15, np.int8)  # one long bout [10,35)
    m = score.score_split({"s": g}, {"s": (p, ovr(p))}, reps=10)
    b3 = m["bouts"]["by_threshold"]["0.3"]["attack"]
    assert (b3["n_gt"], b3["n_pred"], b3["matched"]) == (2, 1, 1)  # IoU 10/25 = 0.4 each, one-to-one
    assert m["bouts"]["by_threshold"]["0.5"]["attack"]["matched"] == 0
    assert score.match_bouts(np.array([[0, 10]]), np.array([[5, 15]]), 0.3) == 1  # IoU 5/15
    assert score.match_bouts(np.array([[0, 10]]), np.array([[5, 15]]), 0.34) == 0


def test_random_baseline_equals_prevalence():
    g = np.array([A] * 50 + [I] * 200 + [M] * 100 + [O] * 650, np.int8)
    rb = score.random_baseline({"s": g, "t": g}, simulate_reps=30)
    assert rb["expected_f1"] == {"attack": 0.05, "investigation": 0.2, "mount": 0.1}
    assert rb["expected_macro_f1"] == pytest.approx(0.35 / 3)
    for b in common.BEHAVIORS:
        assert rb["simulated"]["mean_f1"][b] == pytest.approx(rb["expected_f1"][b], abs=0.015)


@pytest.fixture
def dataset(tmp_path):
    seqs = {"mouse900_task1_annotator1": np.array([A] * 30 + [I] * 60 + [O] * 30 + [M] * 31, np.int8),
            "mouse901_task1_annotator1": np.array([O] * 50 + [I] * 40 + [O] * 30, np.int8)}
    return tmp_path, write_dataset(tmp_path, seqs), seqs


def test_run_end_to_end_logs_prereg_then_result(dataset):
    root, sf, seqs = dataset
    pd = root / "pred"
    pd.mkdir()
    (pd / "manifest.json").write_text(json.dumps({"sample_fps": 10, "config": {"rules": "v0"},
                                                  "cost": {"total_usd": 0.0}}))
    with open(pd / "mouse900_task1_annotator1.jsonl", "w") as f:
        f.write(json.dumps({"meta": {"sample_fps": 10}}) + "\n")
        for b, s, e in (("attack", 0, 30), ("investigation", 30, 90), ("mount", 120, 151)):
            f.write(json.dumps({"behavior": b, "start_frame": s, "end_frame": e}) + "\n")
    lab = np.array([O] * 17 + [I] * 13 + [O] * 10, np.int8)  # 40 samples at 10 fps for 120 frames
    np.savez(pd / "mouse901.npz", labels=lab, sample_fps=10)
    log = root / "runs.jsonl"
    rec = score.run("dev", pd, labels_dir=root / "labels", split_file=sf, log_path=log, out_dir=root / "out",
                    reps=100)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["started", "ok"]
    assert lines[0]["run_id"] == lines[1]["run_id"] == rec["run_id"]
    assert lines[0]["predictions_sha256"] == lines[1]["predictions_sha256"]
    assert "metrics" not in lines[0] and lines[1]["purpose"] == "tune"
    assert lines[1]["config"] == {"rules": "v0"} and lines[1]["cost"] == {"total_usd": 0.0}
    m = rec["metrics"]
    assert m["behaviors"]["attack"]["f1"] == 1.0 and m["behaviors"]["mount"]["f1"] == 1.0
    inv = m["behaviors"]["investigation"]
    # mouse901 GT investigation = frames 50..89. Prediction samples 17..29 cover frames 50..88 by the
    # nearest-sample rule (frame 49 -> round(16.33) = 16, frame 89 -> round(29.67) = 30, both "other"),
    # so mouse900's 60 frames + 39 = 99 TP and 1 FN.
    assert (inv["tp"], inv["fp"], inv["fn"]) == (99, 0, 1)
    assert (root / "out" / f"{rec['run_id']}.json").exists()
    text = score.format_report(m, "t")
    assert "macro F1" in text and all(b in text for b in common.BEHAVIORS)


def test_missing_prediction_is_logged_as_failed(dataset):
    root, sf, _ = dataset
    pd = root / "pred"
    pd.mkdir()
    (pd / "mouse900.jsonl").write_text(json.dumps({"meta": {"sample_fps": 10}}) + "\n")
    log = root / "runs.jsonl"
    with pytest.raises(ValueError):
        score.run("dev", pd, labels_dir=root / "labels", split_file=sf, log_path=log, reps=10)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["started", "failed"]
    assert lines[1]["labels_loaded"] is False
    rec = score.run("dev", pd, labels_dir=root / "labels", split_file=sf, log_path=log, reps=10,
                    allow_missing=True)
    assert rec["status"] == "ok" and any("all-other" in w for w in rec["warnings"])


def test_missing_sample_fps_fails(dataset):
    root, sf, _ = dataset
    pd = root / "pred"
    pd.mkdir()
    for s in ("mouse900", "mouse901"):
        (pd / f"{s}.jsonl").write_text(json.dumps({"behavior": "attack", "start_frame": 0, "end_frame": 5}) + "\n")
    with pytest.raises(ValueError, match="sample_fps"):
        score.run("dev", pd, labels_dir=root / "labels", split_file=sf, log_path=root / "r.jsonl", reps=10)


def test_cli_main(dataset, capsys):
    root, sf, _ = dataset
    pd = root / "pred"
    pd.mkdir()
    (pd / "manifest.json").write_text(json.dumps({"sample_fps": 10}))
    for s in ("mouse900", "mouse901"):
        (pd / f"{s}.jsonl").write_text("")
    rc = score.main(["--split", "dev", "--pred-dir", str(pd), "--labels-dir", str(root / "labels"),
                     "--split-file", str(sf), "--log", str(root / "r.jsonl"), "--out-dir", str(root / "o"),
                     "--reps", "50"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "macro F1" in out and "attack" in out


def test_test_guard(tmp_path):
    log = tmp_path / "runs.jsonl"
    g = dict(log_path=log, require_clean=())
    common.guard_test_run("dev", False, "tune", "score", **g)  # no-op for dev
    with pytest.raises(SystemExit):
        common.guard_test_run("test", False, "test-1", "score", **g)
    with pytest.raises(SystemExit):
        common.guard_test_run("test", True, "tune", "score", **g)
    with pytest.raises(SystemExit, match="no test-1"):
        common.guard_test_run("test", True, "test-2", "score", **g)
    common.guard_test_run("test", True, "test-1", "score", **g)
    common.append_run({"run_id": "r1", "split": "test", "purpose": "test-1", "kind": "score",
                       "status": "started"}, log)
    common.append_run({"run_id": "r1", "split": "test", "purpose": "test-1", "kind": "score",
                       "status": "ok", "labels_loaded": True}, log)
    with pytest.raises(SystemExit, match="already executed"):
        common.guard_test_run("test", True, "test-1", "score", **g)
    common.guard_test_run("test", True, "test-1", "identity", **g)  # same test run, other kind
    common.guard_test_run("test", True, "test-2", "score", **g)
    # a run that failed before loading labels may be restarted; one that loaded labels may not
    common.append_run({"run_id": "r2", "split": "test", "purpose": "test-2", "kind": "score",
                       "status": "started"}, log)
    common.append_run({"run_id": "r2", "split": "test", "purpose": "test-2", "kind": "score",
                       "status": "failed", "labels_loaded": False}, log)
    common.guard_test_run("test", True, "test-2", "score", **g)
    common.append_run({"run_id": "r3", "split": "test", "purpose": "test-2", "kind": "score",
                       "status": "started"}, log)
    with pytest.raises(SystemExit):  # r3 has no final record (crashed mid-run): refuse
        common.guard_test_run("test", True, "test-2", "score", **g)


def test_test_split_refuses_subset(dataset):
    root, sf, _ = dataset
    with pytest.raises(SystemExit):
        score.run("test", root, purpose="test-1", confirm_test_run=True, seqs=["x"], split_file=sf,
                  log_path=root / "r.jsonl")
