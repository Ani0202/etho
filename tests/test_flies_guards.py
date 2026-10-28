"""Frozen-run guards (wrong detectors, override, dirty tree, branch from the decision record), the dirty-path filter,
and a real 10 fps pipeline (stride-3 tracks through finalize_fly -> predict_movie mid10 -> the scorer's loader)."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from etho.flies import predict as P
from etho.flies.frozen import check_frozen_run, run_branch, sha256_file
from etho.tracking.fly_features import finalize_fly
from tests.test_fly_features import blank

ROOT = Path(__file__).resolve().parents[1]
CLEAN = {"run_dirty_paths": []}
BRANCHES = {"GO": {"window": "mid5", "sample_fps": 30, "behaviors": ["wing_threat", "lunge"]},
            "NO-GO": {"window": "mid10", "sample_fps": 10, "behaviors": ["wing_threat"]}}


def mini_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "configs/detectors").mkdir(parents=True)
    (repo / "etho").mkdir()
    det = repo / "configs/detectors/F1_flies.json"
    det.write_text('{"behaviors": {}}')
    (repo / "etho/x.py").write_text("x = 1\n")
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"name": "t", "frozen_at_commit": "abc", "branches": BRANCHES, "assets": {
        "detectors": [{"path": "configs/detectors/F1_flies.json", "sha256": sha256_file(det)}],
        "runner": [{"path": "etho/x.py", "sha256": sha256_file(repo / "etho/x.py")}]}}))
    return repo, det, frozen


def test_good_frozen_run_passes_and_records_hashes(tmp_path):
    repo, det, frozen = mini_repo(tmp_path)
    rec = check_frozen_run(frozen, det, override=None, repo=repo, state=CLEAN)
    assert rec["detectors_sha256"] == sha256_file(det) and rec["assets_checked"] == 2
    assert rec["frozen_sha256"] == sha256_file(frozen)


def test_wrong_detectors_file_is_refused(tmp_path):
    repo, det, frozen = mini_repo(tmp_path)
    other = tmp_path / "other.json"
    other.write_text('{"behaviors": {"lunge": {}}}')
    with pytest.raises(SystemExit, match="not the frozen detectors"):
        check_frozen_run(frozen, other, override=None, repo=repo, state=CLEAN)


def test_override_is_refused(tmp_path):
    repo, det, frozen = mini_repo(tmp_path)
    with pytest.raises(SystemExit, match="override"):
        check_frozen_run(frozen, det, override='{"lunge": {"post": {}}}', repo=repo, state=CLEAN)


def test_dirty_frozen_trees_and_changed_assets_are_refused(tmp_path):
    repo, det, frozen = mini_repo(tmp_path)
    for dirty in (["etho/flies/predict.py"], ["configs/flies_tracking.json"]):
        with pytest.raises(SystemExit, match="uncommitted"):
            check_frozen_run(frozen, det, override=None, repo=repo, state={"run_dirty_paths": dirty})
    # etho/app, docs and logs are outside the frozen trees
    check_frozen_run(frozen, det, override=None, repo=repo,
                     state={"run_dirty_paths": ["etho/app/server.py", "docs/STATE.md", "eval/results/runs.jsonl"]})
    (repo / "etho/x.py").write_text("x = 2\n")  # a frozen asset edited after the freeze
    with pytest.raises(SystemExit, match="differs from the frozen hash"):
        check_frozen_run(frozen, det, override=None, repo=repo, state=CLEAN)


def test_branch_comes_from_the_decision_record_not_the_cli(tmp_path):
    cfg = {"branches": BRANCHES}
    assert run_branch(cfg, tmp_path / "none.json")["window"] == "mid5"  # no record: the dev lunge gate (GO)
    rec = tmp_path / "decision.json"
    rec.write_text(json.dumps({"branch": "NO-GO", **BRANCHES["NO-GO"]}))
    br = run_branch(cfg, rec)
    assert (br["branch"], br["window"], br["sample_fps"], br["behaviors"]) == ("NO-GO", "mid10", 10, ["wing_threat"])
    assert br["decision_record_sha256"] == sha256_file(rec)
    assert run_branch(cfg, rec, "mid10")["window"] == "mid10"
    with pytest.raises(SystemExit, match="mid5 requested"):
        run_branch(cfg, rec, "mid5")
    with pytest.raises(SystemExit, match="mid10 requested"):
        run_branch(cfg, tmp_path / "none.json", "mid10")


def test_cli_refuses_override_under_frozen_config(tmp_path):
    r = subprocess.run([sys.executable, "scripts/run_fly_etho.py", "predict", "--detectors",
                        "configs/detectors/F1_flies.json", "--frozen-config", "configs/frozen_flies_v1.json",
                        "--override", '{"lunge": {"use": [0]}}', "--out", str(tmp_path / "o")], cwd=ROOT,
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin", "PYTHONPATH": str(ROOT), "ETHO_ROOT": str(tmp_path)})
    assert r.returncode != 0 and "override" in r.stderr


def test_git_state_filters_to_frozen_trees_before_truncating(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    for d in ("etho/app", "etho/flies", "configs", "logs"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "etho/flies/a.py").write_text("a = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    for i in range(80):  # more than the old 50-path truncation, all outside the frozen trees
        (tmp_path / "logs" / f"{i:03d}.log").write_text("x")
    (tmp_path / "etho/app/web.py").write_text("x")
    (tmp_path / "etho/flies/a.py").write_text("a = 2\n")
    (tmp_path / "configs/new.json").write_text("{}")
    st = P.git_state(tmp_path)
    assert sorted(st["run_dirty_paths"]) == ["configs/new.json", "etho/flies/a.py"]
    assert st["n_dirty_paths_outside_frozen_trees"] >= 2 and st["run_commit"]


# ---------------------------------------------------------------- a real 10 fps pipeline

def table10(n=6000):
    t = blank(n)
    rng = np.random.default_rng(2)
    for p, x0 in (("a", 40.0), ("b", 100.0)):
        t[f"{p}_cx"] = x0 + rng.normal(0, 0.3, n)
        t[f"{p}_cy"] = 70.0 + rng.normal(0, 0.3, n)
        t[f"{p}_bx"], t[f"{p}_by"] = t[f"{p}_cx"].copy(), t[f"{p}_cy"].copy()
    t["b_minor_len"][2000:2100] = 15.0  # a held wide silhouette for 10 s
    t["gap"] = np.full(n, 20.0)
    t["src_frame"] = 18000 + 3 * np.arange(n)  # mid10 at stride 3
    t["id_unknown"] = np.zeros(n)
    t["id_epoch"] = np.zeros(n)
    return finalize_fly(t, 10.0, 27.0)


def test_nogo_branch_pipeline_at_10_fps_is_accepted_by_the_scorer(tmp_path):
    from eval.flies_score import load_prediction
    spec = json.loads((ROOT / "configs/detectors/F1_flies.json").read_text())
    spec["behaviors"] = {"wing_threat": spec["behaviors"]["wing_threat"]}  # the NO-GO branch scores wing only
    t = table10()
    assert float(t["a_speed"][np.isfinite(t["a_speed"])].max()) < 100  # features really are at 10 fps
    out = P.predict_movie(t, spec, "mid10", 10.0)
    assert float(out["sample_fps"]) == 10.0 and out["wing_threat"].shape == (6000,)
    assert out["window"].tolist() == [18000, 36000] and out["track_xy"].shape == (6000, 2, 2)
    np.savez(tmp_path / "movie6.npz", **out)
    got = load_prediction(tmp_path / "movie6.npz", (18000, 36000), ("wing_threat",))
    assert got["step"] == 3 and got["pair"]["wing_threat"].shape == (18000,)
    with pytest.raises(ValueError, match="do not cover window"):
        P.predict_movie(t, spec, "mid5", 10.0)
