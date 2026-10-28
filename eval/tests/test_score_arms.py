"""eval/score_arms.py: multi-arm scoring, pre-registration, sealing, frozen-config check, targets. No network."""

import json

import numpy as np
import pytest

from eval import common, score, score_arms
from eval.tests.synth import A, I, M, O, write_dataset

SEQS = {"mouse900_task1_annotator1": np.array([A] * 30 + [I] * 60 + [O] * 30 + [M] * 30, np.int8),
        "mouse901_task1_annotator1": np.array([O] * 50 + [I] * 40 + [O] * 30, np.int8),
        "mouse902_task1_annotator1": np.array([M] * 40 + [O] * 50 + [A] * 30, np.int8)}


def _write_arm(d, labels_by_seq):
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"sample_fps": 30, "config": {"arm": d.name}}))
    for s, lab in labels_by_seq.items():
        np.savez(d / f"{s}.npz", labels=np.asarray(lab, np.int8), sample_fps=30)


@pytest.fixture
def arms(tmp_path):
    def setup(split_name="dev"):
        sf = write_dataset(tmp_path, SEQS, split_name)
        perfect = tmp_path / "perfect"
        _write_arm(perfect, SEQS)
        noisy = {s: lab.copy() for s, lab in SEQS.items()}
        noisy["mouse900_task1_annotator1"][:30] = O  # miss all attack in one sequence
        worse = tmp_path / "worse"
        _write_arm(worse, noisy)
        return sf, {"good": perfect, "bad": worse}
    return setup


def test_one_started_record_with_all_arm_hashes_then_ok(arms, tmp_path):
    sf, a = arms()
    log = tmp_path / "runs.jsonl"
    rec = score_arms.run_arms("dev", a, "good", compares=[("bad", "good")], labels_dir=tmp_path / "labels",
                              split_file=sf, log_path=log, out_dir=tmp_path / "out", reps=200)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["started", "ok"]
    assert "metrics" not in lines[0]
    assert set(lines[0]["arms"]) == {"good", "bad"}
    for n in ("good", "bad"):
        assert lines[0]["arms"][n]["predictions_sha256"] == lines[1]["arms"][n]["predictions_sha256"]
    m = rec["metrics"]["arms"]
    assert m["good"]["macro_f1"] == 1.0 and m["bad"]["behaviors"]["attack"]["recall"] == 0.5
    d = rec["metrics"]["paired_differences"]["bad:good"]
    assert d["behaviors"]["attack"]["diff"] == pytest.approx(1.0 - 2 / 3)
    assert d["behaviors"]["investigation"]["diff"] == 0.0
    lo, hi = d["macro_f1"]["ci95"]
    assert lo <= d["macro_f1"]["diff"] <= hi
    # single-arm equivalence: each arm's metrics equal eval.score on that directory alone
    single = score.run("dev", a["bad"], labels_dir=tmp_path / "labels", split_file=sf, log_path=log, reps=200)
    assert single["metrics"]["macro_f1"] == m["bad"]["macro_f1"]
    assert single["metrics"]["macro_f1_ci95"] == m["bad"]["macro_f1_ci95"]
    assert m["good"]["duration_agreement"]["mount"]["mae_s"] == 0.0


def test_paired_diff_identical_arms_is_zero():
    c = np.array([[[5, 1, 2], [3, 0, 1], [0, 0, 0]], [[0, 2, 0], [4, 1, 1], [6, 0, 2]]], np.int64)
    d = score_arms.paired_diff(c, c, reps=100)
    assert d["macro_f1"]["diff"] == 0.0 and d["macro_f1"]["ci95"] == [0.0, 0.0]


def test_seal_hides_per_sequence(arms, tmp_path):
    sf, a = arms()
    rec = score_arms.run_arms("dev", a, "good", labels_dir=tmp_path / "labels", split_file=sf,
                              log_path=tmp_path / "r.jsonl", out_dir=tmp_path / "out", reps=50, seal=True)
    assert all("per_sequence" not in m for m in rec["metrics"]["arms"].values())
    sp = rec["per_sequence_sealed"]["path"]
    assert common.sha256_file(__import__("pathlib").Path(sp)) == rec["per_sequence_sealed"]["sha256"]
    assert "mouse900_task1_annotator1" in json.loads(open(sp).read())["arms"]["good"]["per_sequence"]
    text = score_arms.format_arms_report(rec)
    assert "mouse900" not in text and "sealed" in text


def test_targets_verdicts(arms, tmp_path):
    sf, a = arms()
    tg = tmp_path / "targets.json"
    tg.write_text(json.dumps({"rule": "r", "primary_arm": "bad",
                              "f1": {"minimum_credible": {"macro": 0.5, "attack": 0.9}},
                              "tier1_lift_macro": {"baseline_arm": "bad", "arm": "good", "minimum_credible": 0.01,
                                                   "good": 0.5}}))
    rec = score_arms.run_arms("dev", a, "bad", compares=[("bad", "good")], targets_path=tg,
                              labels_dir=tmp_path / "labels", split_file=sf, log_path=tmp_path / "r.jsonl",
                              out_dir=tmp_path / "out", reps=50)
    t = rec["targets_check"]
    assert t["f1"]["minimum_credible"]["attack"]["verdict"] == "NOT MET"  # 0.8 < 0.9
    assert t["f1"]["minimum_credible"]["macro"]["verdict"] in ("MET", "MET ROBUSTLY")
    assert t["tier1_lift_macro"]["good"]["verdict"] == "NOT MET"


def test_test_split_requires_frozen_config_and_checks_hashes(arms, tmp_path, monkeypatch):
    sf, a = arms("test")
    log = tmp_path / "runs.jsonl"
    kw = dict(labels_dir=tmp_path / "labels", split_file=sf, log_path=log, out_dir=tmp_path / "out", reps=20,
              confirm_test_run=True, purpose="test-1")
    monkeypatch.setattr(common, "guard_test_run", lambda *x, **k: None)  # git cleanliness not under test here
    with pytest.raises(SystemExit, match="frozen-config"):
        score_arms.run_arms("test", a, "good", **kw)
    f = tmp_path / "thing.json"
    f.write_text("{}")
    fc = tmp_path / "frozen.json"
    fc.write_text(json.dumps({"x": {"path": str(f), "sha256": "0" * 64}}))
    with pytest.raises(SystemExit, match="mismatch"):
        score_arms.run_arms("test", a, "good", frozen_config=fc, **kw)
    assert not log.exists()  # refused before any record
    fc.write_text(json.dumps({"x": {"path": str(f), "sha256": common.sha256_file(f)}}))
    rec = score_arms.run_arms("test", a, "good", frozen_config=fc, **kw)
    assert rec["per_sequence_sealed"] is not None  # always sealed on test
    assert rec["frozen_config"]["files_checked"][str(f)] == common.sha256_file(f)


def test_missing_arm_file_fails_before_labels(arms, tmp_path):
    sf, a = arms()
    (a["bad"] / "mouse901_task1_annotator1.npz").unlink()
    log = tmp_path / "r.jsonl"
    with pytest.raises(ValueError, match="missing"):
        score_arms.run_arms("dev", a, "good", labels_dir=tmp_path / "labels", split_file=sf, log_path=log,
                            reps=10)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["started", "failed"] and lines[1]["labels_loaded"] is False


def test_single_arm_scorer_refuses_test(tmp_path):
    with pytest.raises(SystemExit, match="score_arms"):
        score.run("test", tmp_path, purpose="test-1", confirm_test_run=True)
