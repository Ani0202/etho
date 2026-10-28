"""eval/score_arms.py: multi-arm scoring, pre-registration, sealing, frozen-config check, targets. No network."""

import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pytest

from eval import common, score, score_arms
from eval.tests.synth import A, I, M, O, write_dataset

SEQS = {"mouse900_task1_annotator1": np.array([A] * 30 + [I] * 60 + [O] * 30 + [M] * 30, np.int8),
        "mouse901_task1_annotator1": np.array([O] * 50 + [I] * 40 + [O] * 30, np.int8),
        "mouse902_task1_annotator1": np.array([M] * 40 + [O] * 50 + [A] * 30, np.int8)}


def _records(log):
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _no_labels(monkeypatch):
    """Fail loudly if any code path opens a label file."""
    def boom(*a, **k):
        raise AssertionError("a label file was opened")
    monkeypatch.setattr(common, "load_sequence", boom)


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
    assert common.sha256_file(Path(sp)) == rec["per_sequence_sealed"]["sha256"]
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


def test_frozen_asset_mismatch_refused_before_started(arms, tmp_path):
    sf, a = arms()
    log = tmp_path / "runs.jsonl"
    f = tmp_path / "thing.json"
    f.write_text("{}")
    fc = tmp_path / "frozen.json"
    fc.write_text(json.dumps({"x": {"path": str(f), "sha256": "0" * 64}}))
    with pytest.raises(SystemExit, match="frozen config mismatch"):
        score_arms.run_arms("dev", a, "good", frozen_config=fc, labels_dir=tmp_path / "labels", split_file=sf,
                            log_path=log, reps=10)
    recs = _records(log)
    assert [(r["kind"], r["status"]) for r in recs] == [("preflight", "refused")]  # no `score` record


def test_missing_arm_file_refused_before_started(arms, tmp_path, monkeypatch):
    sf, a = arms()
    (a["bad"] / "mouse901_task1_annotator1.npz").unlink()
    log = tmp_path / "r.jsonl"
    _no_labels(monkeypatch)
    with pytest.raises(SystemExit, match="no prediction file for mouse901"):
        score_arms.run_arms("dev", a, "good", labels_dir=tmp_path / "labels", split_file=sf, log_path=log,
                            reps=10)
    recs = _records(log)
    assert [(r["kind"], r["status"]) for r in recs] == [("preflight", "refused")]
    assert recs[0]["labels_loaded"] is False


def test_single_arm_scorer_refuses_test(tmp_path):
    with pytest.raises(SystemExit, match="score_arms"):
        score.run("test", tmp_path, purpose="test-1", confirm_test_run=True)


# --------------------------------------------------------------------------- test-split protocol (amendment A1)

PROMPT = "judge {n} frames"


def _git(repo, *a):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """A tiny git repo (frozen commit, then run commit), a synthetic test split, and four arms."""
    repo = tmp_path / "repo"
    (repo / "etho" / "adjudication").mkdir(parents=True)
    (repo / "configs").mkdir()
    (repo / "etho" / "adjudication" / "tier1.py").write_text(f'import os\n\nPROMPT = """{PROMPT}"""\n')
    (repo / "configs" / "det.json").write_text('{"detectors": []}')
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "frozen")
    frozen_at = _git(repo, "rev-parse", "HEAD")
    cfg = {"name": "t", "frozen_at_commit": frozen_at,
           "zero_shot_arm": {"detectors": {"path": "configs/det.json",
                                           "sha256": _sha((repo / "configs" / "det.json").read_bytes())},
                             "tier1": {"prompt_sha256": _sha(PROMPT.encode())}}}
    (repo / "configs" / "frozen.json").write_text(json.dumps(cfg))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "run")
    run_commit = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)
    sf = write_dataset(tmp_path, SEQS, "test")
    frozen_sha = common.sha256_file(repo / "configs" / "frozen.json")

    def write_arms(failed=(), tweak=None, arms_names=score_arms.TEST_ARMS, commit=None):
        root = tmp_path / f"preds{len(list(tmp_path.glob('preds*')))}"
        frames = {s: {"src_frames": len(lab), "n_samples": None if s in failed else math.ceil(len(lab) / 3)}
                  for s, lab in SEQS.items()}
        out = {}
        for arm in arms_names:
            d = root / arm
            d.mkdir(parents=True)
            man = {"sample_fps": 10, "arm": arm,
                   "config": {"frozen_config": "configs/frozen.json", "frozen_sha256": frozen_sha,
                              "frozen_at_commit": frozen_at, "run_commit": commit or run_commit,
                              "run_git_dirty_paths": []},
                   "frames": frames, "failed_sequences": list(failed),
                   "wall_clock_s": {"per_video": {s: {"track_s": 2.0, "rules_s": 0.5, "tier1_s": 1.5}
                                                  for s in SEQS}}}
            for s, lab in SEQS.items():
                samples = np.full(math.ceil(len(lab) / 3), O, np.int8) if s in failed else lab[::3]
                np.savez(d / f"{s}.npz", labels=samples, sample_fps=np.float64(10.0), sample_stride=np.int64(3))
            if tweak:
                tweak(arm, d, man)
            (d / "manifest.json").write_text(json.dumps(man))
            out[arm] = d
        return out

    kw = dict(purpose="test-1", confirm_test_run=True, frozen_config=Path("configs/frozen.json"),
              labels_dir=tmp_path / "labels", split_file=sf, log_path=tmp_path / "runs.jsonl",
              out_dir=tmp_path / "out", reps=50, compares=list(score_arms.TEST_COMPARES))
    return {"repo": repo, "write_arms": write_arms, "kw": kw, "log": tmp_path / "runs.jsonl",
            "frozen_at": frozen_at, "run_commit": run_commit}


def _refused(env, arms, match, monkeypatch, **extra):
    _no_labels(monkeypatch)
    with pytest.raises(SystemExit, match=match):
        score_arms.run_arms("test", arms, "zs_t1", **{**env["kw"], **extra})
    recs = _records(env["log"])
    assert recs and all(r["kind"] == "preflight" and r["status"] == "refused" for r in recs)
    assert not any(r["kind"] == "score" for r in recs)  # no test run used
    return recs[-1]


def test_test_run_happy_path_strict(test_env):
    rec = score_arms.run_arms("test", test_env["write_arms"](), "zs_t1", **test_env["kw"])
    recs = _records(test_env["log"])
    assert [(r["kind"], r["status"]) for r in recs] == [("score", "started"), ("score", "ok")]
    ex = recs[0]["preflight"]["execution"]
    assert ex["product_diff_empty"] is True and ex["tier1_prompt_sha256_at_run_commit"] == _sha(PROMPT.encode())
    assert recs[0]["preflight"]["validation"]["n_problems"] == 0
    assert rec["metrics"]["arms"]["zs_t1"]["macro_f1"] == pytest.approx(1.0, abs=0.05)
    assert rec["per_sequence_sealed"] is not None and "bootstrap_scope" in rec["metrics"]
    # undefined-bootstrap fractions are kept in the result (amendment A1, item 5)
    u = rec["metrics"]["arms"]["zs_t1"]["behaviors"]["attack"]["ci95_undefined_frac"]
    assert set(u) == {"precision", "recall", "f1"}
    sp = rec["speed"]
    assert sp["computed"] and sp["n_videos"] == 3
    assert sp["pooled_x_realtime"] == pytest.approx(4.0 * 3 / ((150 + 120 + 120) / 30))
    assert rec["targets_check"] is None


def test_test_run_preflight_only_opens_no_labels(test_env, monkeypatch):
    _no_labels(monkeypatch)
    kw = {**test_env["kw"], "confirm_test_run": False}
    rec = score_arms.run_arms("test", test_env["write_arms"](), "zs_t1", preflight_only=True, **kw)
    assert rec["kind"] == "preflight" and rec["status"] == "ok" and rec["labels_loaded"] is False
    assert [r["kind"] for r in _records(test_env["log"])] == ["preflight"]


@pytest.mark.parametrize("case,match", [
    ("n_samples", r"n_samples 51 != ceil"),
    ("short_file", r"labels, expected 50"),
    ("src_vs_index", r"decoded src_frames 151 != expected annotation length 150"),
    ("bad_code", r"label codes \[7\]"),
    ("no_fps", r"sample_fps missing"),
    ("bad_stride", r"sample_stride"),
    ("garbage", r"unreadable"),
    ("undeclared_failure", r"not in failed_sequences"),
    ("no_frames", r"no `frames`"),
    ("no_failed_list", r"no `failed_sequences`"),
])
def test_test_run_prediction_validation_refuses(test_env, monkeypatch, case, match):
    s0 = "mouse900_task1_annotator1"

    def tweak(arm, d, man):
        if arm != "fs_rules":
            return
        f = d / f"{s0}.npz"
        if case == "n_samples":
            man["frames"] = {**man["frames"], s0: {"src_frames": 150, "n_samples": 51}}
        elif case == "short_file":
            np.savez(f, labels=np.full(49, O, np.int8), sample_fps=10.0, sample_stride=3)
        elif case == "src_vs_index":
            man["frames"] = {**man["frames"], s0: {"src_frames": 151, "n_samples": 51}}
            np.savez(f, labels=np.full(51, O, np.int8), sample_fps=10.0, sample_stride=3)
        elif case == "bad_code":
            np.savez(f, labels=np.full(50, 7, np.int8), sample_fps=10.0, sample_stride=3)
        elif case == "no_fps":
            np.savez(f, labels=np.full(50, O, np.int8))
            man.pop("sample_fps")
        elif case == "bad_stride":
            np.savez(f, labels=np.full(50, O, np.int8), sample_fps=10.0, sample_stride=2)
        elif case == "garbage":
            f.write_bytes(b"not an npz")
        elif case == "undeclared_failure":
            man["frames"] = {**man["frames"], s0: {"src_frames": 150, "n_samples": None}}
        elif case == "no_frames":
            man.pop("frames")
        elif case == "no_failed_list":
            man.pop("failed_sequences")

    _refused(test_env, test_env["write_arms"](tweak=tweak), match, monkeypatch)


def test_named_length_mismatch_can_be_accepted_and_is_logged(test_env):
    s0 = "mouse900_task1_annotator1"

    def tweak(arm, d, man):  # video decodes to 149 frames, annotation has 150: still 50 samples
        man["frames"] = {**man["frames"], s0: {"src_frames": 149, "n_samples": 50}}

    a = test_env["write_arms"](tweak=tweak)
    rec = score_arms.run_arms("test", a, "zs_t1", accept_length_mismatch={s0: "one frame short on decode"},
                              **test_env["kw"])
    v = _records(test_env["log"])[0]["preflight"]["validation"]
    assert v["accept_reasons"] == {s0: "one frame short on decode"}
    assert any("149 != expected annotation length 150" in x for x in v["accepted_length_mismatches"])
    assert rec["status"] == "ok"


def test_unscorable_mismatch_cannot_be_accepted(test_env, monkeypatch):
    s0 = "mouse900_task1_annotator1"

    def tweak(arm, d, man):
        man["frames"] = {**man["frames"], s0: {"src_frames": 151, "n_samples": 51}}
        np.savez(d / f"{s0}.npz", labels=np.full(51, O, np.int8), sample_fps=10.0, sample_stride=3)

    rec = _refused(test_env, test_env["write_arms"](tweak=tweak), "unscorable", monkeypatch,
                   accept_length_mismatch={s0: "one extra decoded frame"})
    assert not any("151 != expected" in p for p in rec["problems"])  # that part was accepted


def test_declared_failure_placeholder_is_scored_as_other(test_env):
    s0 = "mouse901_task1_annotator1"
    rec = score_arms.run_arms("test", test_env["write_arms"](failed=(s0,)), "zs_t1", **test_env["kw"])
    assert rec["status"] == "ok"
    inv = rec["metrics"]["arms"]["zs_t1"]["behaviors"]["investigation"]
    assert inv["fn"] >= 40 - 1  # mouse901's 40 investigation frames are missed (scored as other)
    assert rec["speed"]["n_videos"] == 3  # the failed video stays in the speed denominator


def test_declared_failure_must_be_all_other(test_env, monkeypatch):
    s0 = "mouse901_task1_annotator1"

    def tweak(arm, d, man):
        np.savez(d / f"{s0}.npz", labels=np.full(40, I, np.int8), sample_fps=10.0, sample_stride=3)

    _refused(test_env, test_env["write_arms"](failed=(s0,), tweak=tweak), "not all 'other'", monkeypatch)


def test_test_run_requires_four_named_arms_and_primary(test_env, monkeypatch):
    a = test_env["write_arms"](arms_names=("zs_t1", "zs_rules", "fs_t1"))
    _refused(test_env, a, r"arms must be exactly", monkeypatch, compares=[("zs_rules", "zs_t1")])
    a = test_env["write_arms"]()
    _no_labels(monkeypatch)
    with pytest.raises(SystemExit, match="primary arm must be 'zs_t1'"):
        score_arms.run_arms("test", a, "fs_t1", **test_env["kw"])


def test_test_run_requires_all_planned_comparisons(test_env, monkeypatch):
    _refused(test_env, test_env["write_arms"](), "required paired comparisons missing", monkeypatch,
             compares=[("zs_rules", "zs_t1")])


@pytest.mark.parametrize("case,match", [
    ("frozen_sha", r"frozen_sha256"),
    ("frozen_at", r"frozen_at_commit"),
    ("no_run_commit", r"run_commit missing"),
    ("dirty_etho", r"uncommitted changes in frozen paths"),
    ("no_dirty_record", r"run_git_dirty_paths missing"),
    ("arm_name", r"manifest arm 'zs_rules' != 'zs_t1'"),
    ("bad_commit", r"is not a commit"),
])
def test_test_run_manifest_config_checks(test_env, monkeypatch, case, match):
    def tweak(arm, d, man):
        c = man["config"]
        if case == "bad_commit":  # every arm, so the arms agree with each other
            c["run_commit"] = "f" * 40
        if arm != "zs_t1":
            return
        if case == "frozen_sha":
            c["frozen_sha256"] = "0" * 64
        elif case == "frozen_at":
            c["frozen_at_commit"] = "1" * 40
        elif case == "no_run_commit":
            c.pop("run_commit")
        elif case == "dirty_etho":
            c["run_git_dirty_paths"] = ["etho/adjudication/tier1.py"]
        elif case == "no_dirty_record":
            c.pop("run_git_dirty_paths")
        elif case == "arm_name":
            man["arm"] = "zs_rules"

    _refused(test_env, test_env["write_arms"](tweak=tweak), match, monkeypatch)


def test_test_run_refuses_product_change_and_prompt_change(test_env, monkeypatch):
    repo = test_env["repo"]
    (repo / "etho" / "adjudication" / "tier1.py").write_text('import os\n\nPROMPT = """judge {n} frames!"""\n')
    _git(repo, "commit", "-qam", "change prompt after the freeze")
    new = _git(repo, "rev-parse", "HEAD")
    rec = _refused(test_env, test_env["write_arms"](commit=new), r"tier-1 prompt", monkeypatch)
    assert any("is not empty" in p for p in rec["problems"])  # git diff frozen..run -- etho/ non-empty


def test_payload_hash_ignores_manifest_metadata(test_env):
    a1 = test_env["write_arms"]()

    def tweak(arm, d, man):
        man["wall_clock_s"]["per_video"]["mouse900_task1_annotator1"]["track_s"] = 9.0
    a2 = test_env["write_arms"](tweak=tweak)
    kw = {**test_env["kw"], "confirm_test_run": False}
    r1 = score_arms.run_arms("test", a1, "zs_t1", preflight_only=True, **kw)
    r2 = score_arms.run_arms("test", a2, "zs_t1", preflight_only=True, **kw)
    assert r1["payload_sha256"] == r2["payload_sha256"]
    assert r1["predictions_sha256"] != r2["predictions_sha256"]
    assert r1["arms"]["zs_t1"]["manifest_sha256"] != r2["arms"]["zs_t1"]["manifest_sha256"]


def test_speed_not_computed_when_a_video_lacks_timing(test_env):
    def tweak(arm, d, man):
        man["wall_clock_s"]["per_video"].pop("mouse902_task1_annotator1")
    kw = {**test_env["kw"], "confirm_test_run": False}
    rec = score_arms.run_arms("test", test_env["write_arms"](tweak=tweak), "zs_t1", preflight_only=True, **kw)
    assert rec["speed"]["computed"] is False and "mouse902" in rec["speed"]["reason"]


# --------------------------------------------------------------------------- claim rules (amendment A1)

def test_verdict_direction_aware():
    v = score_arms._verdict
    assert v(0.45, [0.41, 0.50], 0.40) == "MET ROBUSTLY"
    assert v(0.45, [0.39, 0.50], 0.40) == "MET"
    assert v(0.39, [0.30, 0.50], 0.40) == "NOT MET"
    # lower is better: robust needs the UPPER bound at or below the threshold
    assert v(0.04, [0.03, 0.06], 0.05, "lower") == "MET"
    assert v(0.04, [0.03, 0.045], 0.05, "lower") == "MET ROBUSTLY"
    assert v(0.04, None, 0.05, "lower") == "MET"
    assert v(0.06, [0.01, 0.02], 0.05, "lower") == "NOT MET"
    assert v(None, None, 0.05, "lower") == "NOT COMPUTED"


def test_level_needs_every_component():
    comp = {"macro": {"verdict": "MET ROBUSTLY"}, "attack": {"verdict": "NOT MET"},
            "investigation": {"verdict": "MET"}, "mount": {"verdict": "MET"}}
    assert score_arms._level(comp) == {"verdict": "NOT MET", "not_met": ["attack"]}
    comp["attack"]["verdict"] = "MET"
    assert score_arms._level(comp)["verdict"] == "MET"


def test_ci_reading_inconclusive_when_ci_contains_zero():
    assert score_arms.ci_reading([-0.01, 0.08]) == "inconclusive (CI contains 0)"
    assert score_arms.ci_reading([0.0, 0.0]) == "inconclusive (CI contains 0)"
    assert score_arms.ci_reading([0.01, 0.08]) == "above zero"
    assert score_arms.ci_reading([-0.08, -0.01]) == "below zero"


def test_targets_speed_and_levels(test_env):
    tg = test_env["repo"].parent / "targets.json"
    t = json.loads(Path(__file__).resolve().parents[1].joinpath("targets_v1.json").read_text())
    tg.write_text(json.dumps(t))
    rec = score_arms.run_arms("test", test_env["write_arms"](), "zs_t1", targets_path=tg, **test_env["kw"])
    tc = rec["targets_check"]
    assert set(tc["f1_level"]) == {"minimum_credible", "good"}
    sp = tc["speed_x_realtime"]  # 4 s per video of 4-5 s: about 0.8-1.0x
    assert sp["minimum_credible"]["components"]["pooled"]["point"] == pytest.approx(rec["speed"]["pooled_x_realtime"])
    assert sp["good"]["components"]["pooled"]["verdict"] == "NOT MET"
    assert all(c["verdict"] != "MET ROBUSTLY" for lv in sp.values() for c in lv["components"].values())
