"""Shared eval helpers: splits, CalMS21 label/keypoint loading, run logging, and the test-run guard.

Only eval/ code reads labels. Product code (etho/) must never import this module.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SPLIT_FILE = Path("eval/splits/calms21_v1.json")
LABELS_DIR = Path("data/calms21/seq")
RUNS_LOG = Path("eval/results/runs.jsonl")

BEHAVIORS = ("attack", "investigation", "mount")
OTHER = "other"
# CalMS21 Task 1 vocab, as stored in data/calms21/seq/*/<seq>.npz ("vocab" key).
VOCAB = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}
NATIVE_FPS = 30
SEED = 20261028

SPLIT_ALIASES = {"dev": "dev", "dev-holdout": "dev_holdout", "dev_holdout": "dev_holdout", "test": "test"}


def canonical_split(name: str) -> str:
    try:
        return SPLIT_ALIASES[name]
    except KeyError:
        raise SystemExit(f"unknown split {name!r}; use dev, dev-holdout, or test") from None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def load_split(split: str, split_file: Path = SPLIT_FILE) -> tuple[list[str], dict]:
    data = json.loads(Path(split_file).read_text())
    return list(data[canonical_split(split)]), data


def source_dir(split: str) -> str:
    """dev and dev_holdout come from the official train split; test from the official test split."""
    return "test" if canonical_split(split) == "test" else "train"


def load_sequence(split: str, seq: str, labels_dir: Path = LABELS_DIR) -> dict:
    """Return {"labels": int8[T] in VOCAB coding, "keypoints": float[T,2,2,7], "scores": float[T,2,7]}."""
    d = np.load(Path(labels_dir) / source_dir(split) / f"{seq}.npz")
    vocab = json.loads(str(d["vocab"]))
    if vocab != VOCAB:
        raise ValueError(f"{seq}: unexpected vocab {vocab}")
    return {"labels": d["labels"].astype(np.int8), "keypoints": d["keypoints"].astype(np.float64),
            "scores": d["scores"].astype(np.float64)}


def git_info() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    status = run("status", "--porcelain")
    return {"git_commit": run("rev-parse", "HEAD") or None, "git_dirty": bool(status),
            "git_dirty_paths": [line[3:] for line in status.splitlines()][:50]}


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_run(record: dict, log_path: Path = RUNS_LOG) -> None:
    """Append-only: one JSON object per line, never edited."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _records(log_path: Path) -> list[dict]:
    log_path = Path(log_path)
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def prior_test_runs(log_path: Path = RUNS_LOG) -> dict[str, list[dict]]:
    """{purpose ("test-1"/"test-2"): [records]} for every logged execution that touched the test split."""
    out: dict[str, list[dict]] = {}
    for rec in _records(log_path):
        if rec.get("split") == "test":
            out.setdefault(rec.get("purpose", "?"), []).append(rec)
    return out


def guard_test_run(split: str, confirm: bool, purpose: str, kind: str, log_path: Path = RUNS_LOG,
                   require_clean: tuple[str, ...] = ("eval", "docs/EVAL_PLAN.md",
                                                     ":(exclude)eval/results")) -> None:
    """Refuse to touch test labels unless every protocol precondition we can check mechanically holds.

    EVAL_PLAN section 7: at most two test runs ("test-1", "test-2"). One test run may contain one
    execution of each kind (score, identity), so the scorer and the identity check of the same run
    share a purpose and count once. Re-executing the same (kind, purpose) is refused unless the
    previous execution failed before any label file was opened (`labels_loaded` false).
    Plan and eval code must be committed (clean). Frozen targets/config are human checks, not verified here.
    """
    if canonical_split(split) != "test":
        return
    if not confirm:
        raise SystemExit("test split: pass --confirm-test-run (this is one of the 2 allowed test runs)")
    if purpose not in ("test-1", "test-2"):
        raise SystemExit("test split: --purpose must be test-1 or test-2")
    prior = prior_test_runs(log_path)
    if purpose == "test-2" and "test-1" not in prior:
        raise SystemExit("test split: test-2 requested but no test-1 is logged")
    same = [r for r in prior.get(purpose, []) if r.get("kind") == kind]
    finals = {r["run_id"]: r for r in same if r.get("status") != "started"}
    for r in same:
        if r.get("status") != "started":
            continue
        fin = finals.get(r["run_id"])
        if fin is None or fin.get("status") != "failed" or fin.get("labels_loaded", True):
            raise SystemExit(f"test split: {kind} {purpose} already executed (run {r['run_id']}); "
                             "only a run that failed before loading labels may be restarted")
    if not any(not p.startswith(":(exclude)") for p in require_clean):
        return
    status = subprocess.run(["git", "status", "--porcelain", "--", *require_clean],
                            capture_output=True, text=True).stdout.strip()
    if status:
        raise SystemExit(f"test split: uncommitted changes in {require_clean}; commit first:\n{status}")


def bootstrap_weights(n_seqs: int, reps: int = 10_000, seed: int = SEED) -> np.ndarray:
    """[reps, n_seqs] multiplicity of each sequence in a resample-with-replacement of whole sequences."""
    rng = np.random.default_rng(seed)
    return rng.multinomial(n_seqs, np.full(n_seqs, 1.0 / n_seqs), size=reps)
