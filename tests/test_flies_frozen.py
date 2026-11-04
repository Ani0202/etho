"""The frozen fly detectors execute, and the frozen config passes the scorer's schema and hash checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from etho.flies import predict as P
from tests.markers import needs_git
from tests.test_fly_features import blank, fin

ROOT = Path(__file__).resolve().parents[1]
FPS = 30.0
N = 9000


def full_table(n=N):
    t = blank(n)
    rng = np.random.default_rng(1)
    for p, x0 in (("a", 40.0), ("b", 100.0)):
        t[f"{p}_cx"] = x0 + rng.normal(0, 0.3, n)
        t[f"{p}_cy"] = 70.0 + rng.normal(0, 0.3, n)
        t[f"{p}_bx"], t[f"{p}_by"] = t[f"{p}_cx"].copy(), t[f"{p}_cy"].copy()
    t["b_minor_len"][3000:3300] = 15.0  # a held wide silhouette
    t["gap"] = np.full(n, 20.0)
    t["src_frame"] = 22500 + np.arange(n)
    t["id_unknown"] = np.zeros(n)
    t["id_epoch"] = np.zeros(n)
    return fin(t)


def test_frozen_detectors_execute_on_a_full_synthetic_table():
    spec = json.loads((ROOT / "configs/detectors/F1_flies.json").read_text())
    assert set(spec["behaviors"]) == {"wing_threat", "lunge"}
    for b in spec["behaviors"].values():
        assert b["model"] == "muse-spark-1.3-contributor" and b["effort"] == "high" and b["public_data"] is True
        assert b["use"] == list(range(len(b["candidates"]))) and b["min_votes"] >= 1 and b["extras"] is True
        assert all(c["code"].lstrip().startswith(("def detect", "#")) and c["prompt_hash"] for c in b["candidates"])
    out = P.predict_movie(full_table(), spec, "mid5", FPS)
    assert out["wing_threat"].shape == (N,) and out["lunge"].shape == (N,)
    by = out["wing_threat_by_track"]
    assert by.shape == (2, N) and not (by.any(axis=0) & ~out["wing_threat"].astype(bool)).any()
    assert out["wing_threat"][:2000].sum() == 0  # a quiet, symmetric stretch is not a wing threat


@needs_git
def test_frozen_config_schema_and_hashes(tmp_path):
    out = tmp_path / "frozen.json"
    r = subprocess.run([sys.executable, "scripts/freeze_flies.py", "freeze", "--allow-dirty", "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    from eval.flies_score import BRANCHES, verify_frozen_flies_config
    cwd = Path.cwd()
    os.chdir(ROOT)  # asset paths are repo-relative
    try:
        info = verify_frozen_flies_config(out, {"frozen_sha256": P.sha256_file(out), "run_commit": "x"},
                                          check_git=False)
    finally:
        os.chdir(cwd)
    cfg = json.loads(out.read_text())
    assert cfg["branches"] == BRANCHES and info["name"] == "flies_v1"
    assert {"definitions", "detectors", "tracking", "runner"} <= set(cfg["assets"])
    assert cfg["tracking_params"]["overlap_s"] == 1.0 and cfg["window"]["GO"] == [22500, 31500]
    assert any(a["path"] == "configs/flies_definitions.json" for a in cfg["assets"]["definitions"])
