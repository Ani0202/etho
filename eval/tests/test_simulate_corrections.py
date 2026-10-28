"""Tests for eval/simulate_corrections.py on synthetic data. No network, no real labels, no model calls."""

import json

import numpy as np
import pytest

from eval import score
from eval import simulate_corrections as sc
from eval.tests.synth import A, I, M, O, write_dataset


def test_error_bouts_fp_fn_definitions():
    gt = np.array([O] * 10 + [A] * 10 + [O] * 10, np.int8)
    pred = np.array([A] * 5 + [O] * 5      # FP bout, overlap 0
                    + [A] * 2 + [O] * 8    # matched? overlap 1.0 -> not FP; GT attack bout overlap 0.2 -> not FN
                    + [A] * 10, np.int8)   # FP bout, overlap 0
    fps = sc.error_bouts(gt, pred, "attack", "fp")
    assert [(b["s0"], b["s1"]) for b in fps] == [(0, 5), (20, 30)]
    assert sc.error_bouts(gt, pred, "attack", "fn") == []  # overlap exactly 0.2 is not < 0.2
    pred2 = pred.copy()
    pred2[11] = O  # GT bout overlap now 0.1
    assert [(b["s0"], b["s1"], b["overlap"]) for b in sc.error_bouts(gt, pred2, "attack", "fn")] == [(10, 20, 0.1)]
    # a predicted bout that is mostly wrong but overlaps GT by 1/6 (< 0.2) counts as FP
    gt3 = np.array([O] * 5 + [M] + [O] * 4, np.int8)
    pred3 = np.array([M] * 6 + [O] * 4, np.int8)
    assert [(b["s0"], b["s1"]) for b in sc.error_bouts(gt3, pred3, "mount", "fp")] == [(0, 6)]
    assert sc.error_bouts(gt, pred, "attack", "fp", min_len=6) == [{"s0": 20, "s1": 30, "overlap": 0.0}]
    with pytest.raises(ValueError):
        sc.error_bouts(gt, pred, "attack", "tp")
    with pytest.raises(ValueError):
        sc.error_bouts(gt, pred[:-1], "attack", "fp")


@pytest.mark.parametrize("n_frames", [30, 31, 32, 100])
def test_frames_for_window_matches_scorer_upsampling(n_frames):
    step = 3
    k = -(-n_frames // step)
    for s0 in range(k):
        for s1 in range(s0 + 1, k + 1):
            ind = np.zeros(k, np.int8)
            ind[s0:s1] = 1
            up = score.upsample_samples(ind, n_frames, step) == 1
            f0, f1 = sc.frames_for_window(s0, s1, n_frames, step)
            expect = np.zeros(n_frames, bool)
            expect[f0:f1] = True
            assert (up == expect).all(), (s0, s1, f0, f1)


def test_select_reproducible_without_replacement_and_clipped():
    cands = [{"seq": "s", "s0": 100 * i, "s1": 100 * i + L, "overlap": 0.0} for i, L in enumerate([5, 80, 31, 30, 2])]
    a = sc.select(cands, 3, 7, "attack", "fp", 30)
    assert a == sc.select(cands, 3, 7, "attack", "fp", 30)
    assert len({c["candidate_index"] for c in a}) == 3
    for c in a:
        assert c["s0"] <= c["w0"] < c["w1"] <= c["s1"] and c["w1"] - c["w0"] == min(30, c["s1"] - c["s0"])
    assert len(sc.select(cands, 10, 7, "attack", "fp", 30)) == 5  # shortfall: take all
    assert sc.select([], 3, 7, "attack", "fp", 30) == []
    # strata use separate RNG streams: same seed, different stratum -> different draws for some seeds
    def picks(b, k, seed):
        return [c["candidate_index"] for c in sc.select(cands, 2, seed, b, k, 30)]
    assert any(picks("attack", "fp", s) != picks("mount", "fn", s) for s in range(10))


def test_select_is_uniform():
    cands = [{"seq": "s", "s0": 10 * i, "s1": 10 * i + 3, "overlap": 0.0} for i in range(10)]
    hits = np.zeros(10)
    reps = 3000
    for seed in range(reps):
        for c in sc.select(cands, 3, seed, "investigation", "fn", 30):
            hits[c["candidate_index"]] += 1
    expected = reps * 3 / 10
    assert np.all(np.abs(hits - expected) < 0.1 * expected), hits
    # clip offsets uniform over [0, L - max_len]
    offs = np.zeros(11)
    for seed in range(2000):
        (c,) = sc.select([{"seq": "s", "s0": 0, "s1": 40, "overlap": 0.0}], 1, seed, "attack", "fp", 30)
        offs[c["w0"]] += 1
    assert np.all(offs > 2000 / 11 * 0.7), offs


def test_feature_summary_nan_aware():
    f = {"dist": np.array([1.0, np.nan, 3.0, 10.0]), "gap": np.array([np.nan, np.nan, 5.0, 1.0])}
    s = sc.feature_summary(f, 0, 3)
    assert s["dist"] == {"median": 2.0, "min": 1.0, "max": 3.0, "n_finite": 2}
    assert s["gap"] == {"median": 5.0, "min": 5.0, "max": 5.0, "n_finite": 1}
    assert sc.feature_summary(f, 0, 2)["gap"] == {"median": None, "min": None, "max": None, "n_finite": 0}


def _setup(tmp_path):
    """Two dev sequences at 30 fps; per-sample predictions at 10 fps with known errors; features."""
    T = 600  # 200 samples
    g1 = np.full(T, O, np.int8)
    g1[60:150] = A      # samples 20..50: attack, predicted -> TP
    g1[300:390] = I     # samples 100..130: investigation, missed -> FN (30 samples)
    g1[450:600] = M     # samples 150..200: mount, missed -> FN (50 samples, clipped)
    g2 = np.full(T, O, np.int8)
    g2[0:120] = I       # samples 0..40: investigation, predicted as attack -> attack FP + investigation FN
    sf = write_dataset(tmp_path, {"mouse900_task1_annotator1": g1, "mouse901_task1_annotator1": g2})
    pred_dir = tmp_path / "pred"
    pred_dir.mkdir()
    p1 = g1[::3].copy()
    p1[100:130] = O
    p1[150:200] = O
    p1[170:175] = A     # attack FP inside missed mount
    p1[5:8] = M         # mount FP (3 samples)
    p2 = g2[::3].copy()
    p2[0:40] = A
    np.savez(pred_dir / "mouse900_task1_annotator1.npz", labels=p1, sample_fps=10.0)
    np.savez(pred_dir / "mouse901_task1_annotator1.npz", labels=p2, sample_fps=10.0)
    feat_dir = tmp_path / "feat"
    for name in ("mouse900_task1_annotator1", "mouse901_task1_annotator1"):
        (feat_dir / name).mkdir(parents=True)
        np.savez(feat_dir / name / "features.npz", dist=np.arange(200, dtype=float),
                 gap=np.where(np.arange(200) % 2, np.nan, 1.0))
    return sf, pred_dir, feat_dir, {"mouse900_task1_annotator1": (g1, p1), "mouse901_task1_annotator1": (g2, p2)}


def _build(tmp_path, sf, pred_dir, feat_dir, seed=1, out="c.json", **kw):
    return sc.build("dev", pred_dir, feat_dir, tmp_path / out, seed=seed, labels_dir=tmp_path / "labels",
                    split_file=sf, log_path=tmp_path / "runs.jsonl", **kw)


def test_build_end_to_end(tmp_path):
    sf, pred_dir, feat_dir, data = _setup(tmp_path)
    doc = _build(tmp_path, sf, pred_dir, feat_dir)
    st = doc["strata"]
    assert st["attack/fp"]["candidates"] == 2 and st["attack/fn"]["candidates"] == 0
    assert st["investigation/fn"]["candidates"] == 2 and st["investigation/fp"]["candidates"] == 0
    assert st["mount/fp"]["candidates"] == 1 and st["mount/fn"]["candidates"] == 1
    assert st["attack/fn"]["shortfall"] == 3 and doc["n_corrections"] == 2 + 2 + 1 + 1
    for c in doc["corrections"]:
        gt, pred = data[c["seq"]]
        assert 0 < c["s1"] - c["s0"] <= 30
        assert c["correct_labels"] == [sc.CODE_TO_LABEL[int(x)] for x in gt[::3][c["s0"]:c["s1"]]]
        assert c["predicted_labels"] == [sc.CODE_TO_LABEL[int(x)] for x in pred[c["s0"]:c["s1"]]]
        if c["kind"] == "fp":
            assert c["predicted_label"] == c["behavior"] != c["correct_label"]
        else:
            assert c["correct_label"] == c["behavior"] != c["predicted_label"]
        assert c["features"]["dist"]["min"] == c["s0"] and c["features"]["dist"]["max"] == c["s1"] - 1
        f0, f1 = c["frames_30fps"]
        assert f0 == max(0, 3 * c["s0"] - 1)
        assert f1 == (600 if c["s1"] == 200 else 3 * c["s1"] - 1)
    mfn = [c for c in doc["corrections"] if c["id"].startswith("mount-fn")][0]
    assert mfn["source_bout"]["clipped"] and 150 <= mfn["s0"] and mfn["s1"] <= 200
    # the attack FP on mouse901 and the investigation FN on mouse901 cover the same samples
    assert doc["n_overlapping_pairs"] >= 1
    assert json.loads((tmp_path / "c.json").read_text()) == doc
    recs = [json.loads(x) for x in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert [r["status"] for r in recs] == ["started", "ok"] and recs[0]["run_id"] == recs[1]["run_id"]
    assert recs[1]["out_sha256"] and recs[1]["n_corrections"] == 6 and recs[1]["cost"]["usd"] == 0.0


def test_build_reproducible_and_seed_dependent(tmp_path):
    sf, pred_dir, feat_dir, _ = _setup(tmp_path)
    a = _build(tmp_path, sf, pred_dir, feat_dir, seed=5, out="a.json", k_fp=1, k_fn=1)
    b = _build(tmp_path, sf, pred_dir, feat_dir, seed=5, out="b.json", k_fp=1, k_fn=1)
    assert a["corrections"] == b["corrections"]
    picks = {tuple((c["seq"], c["s0"]) for c in _build(tmp_path, sf, pred_dir, feat_dir, seed=s, out=f"{s}.json",
                                                       k_fp=1, k_fn=1)["corrections"]) for s in range(12)}
    assert len(picks) > 1


@pytest.mark.parametrize("split", ["test", "dev_holdout", "dev-holdout"])
def test_refuses_non_dev_splits(tmp_path, split):
    sf, pred_dir, feat_dir, _ = _setup(tmp_path)
    with pytest.raises(SystemExit):
        sc.build(split, pred_dir, feat_dir, tmp_path / "x.json", labels_dir=tmp_path / "labels", split_file=sf,
                 log_path=tmp_path / "runs.jsonl")
    assert not (tmp_path / "runs.jsonl").exists() and not (tmp_path / "x.json").exists()


def test_missing_prediction_and_bad_features_are_logged_failures(tmp_path):
    sf, pred_dir, feat_dir, _ = _setup(tmp_path)
    np.savez(feat_dir / "mouse901_task1_annotator1" / "features.npz", dist=np.zeros(5))
    with pytest.raises(ValueError, match="features length"):
        _build(tmp_path, sf, pred_dir, feat_dir)
    (pred_dir / "mouse900_task1_annotator1.npz").unlink()
    with pytest.raises(ValueError, match="no prediction file"):
        _build(tmp_path, sf, pred_dir, feat_dir)
    recs = [json.loads(x) for x in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert [r["status"] for r in recs] == ["started", "failed", "started", "failed"]
    assert recs[3]["labels_loaded"] is False


def test_excluded_frames(tmp_path):
    corr = [{"seq": "a", "frames_30fps": [2, 5]}, {"seq": "b", "frames_30fps": [0, 9]},
            {"seq": "a", "frames_30fps": [8, 10]}]
    m = sc.excluded_frames(corr, "a", 10)
    assert m.tolist() == [False, False, True, True, True, False, False, False, True, True]


def test_score_exclude_corrections_end_to_end(tmp_path):
    sf, pred_dir, feat_dir, _ = _setup(tmp_path)
    doc = _build(tmp_path, sf, pred_dir, feat_dir)
    kw = dict(labels_dir=tmp_path / "labels", split_file=sf, log_path=tmp_path / "runs.jsonl", reps=20)
    full = score.run("dev", pred_dir, **kw)
    exc = score.run("dev", pred_dir, exclude_corrections=tmp_path / "c.json", **kw)
    n_total = sum(sc.excluded_frames(doc["corrections"], s, 600).sum()
                  for s in ("mouse900_task1_annotator1", "mouse901_task1_annotator1"))
    assert exc["metrics"]["n_frames_excluded"] == n_total > 0
    assert exc["metrics"]["n_frames"] == full["metrics"]["n_frames"] - n_total
    info = exc["exclude_corrections"]
    assert info["sha256"] and info["n_corrections"] == doc["n_corrections"] and info["corrections_run_id"]
    assert full["exclude_corrections"] is None
    started = [json.loads(x) for x in (tmp_path / "runs.jsonl").read_text().splitlines()][-2]
    assert started["status"] == "started" and started["exclude_corrections"]["sha256"] == info["sha256"]
    # every corrected window was an error; removing them must not lower attack precision here
    assert exc["metrics"]["behaviors"]["attack"]["fp"] < full["metrics"]["behaviors"]["attack"]["fp"]
    for split in ("dev_holdout", "test"):
        with pytest.raises(SystemExit, match="only dev corrections"):
            score.run(split, pred_dir, exclude_corrections=tmp_path / "c.json", purpose="test-1",
                      confirm_test_run=True, **{**kw, "log_path": tmp_path / "other.jsonl"})
    assert not (tmp_path / "other.jsonl").exists()
    bad = json.loads((tmp_path / "c.json").read_text())
    bad["split"] = "dev_holdout"
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit, match="only dev corrections"):
        score.run("dev", pred_dir, exclude_corrections=tmp_path / "bad.json", **kw)
