"""Guards for a prediction run under the frozen fly config (EVAL_PLAN 14.12 item 10).

A run with `--frozen-config` (the lunge gate, the wing gate, the test) must be exactly the frozen pipeline:
  * the detectors file is byte-identical to the frozen `assets.detectors` entry, and no override is applied;
  * every frozen asset still has its recorded hash in the working tree, and nothing under etho/ (outside
    etho/app) or configs/ is dirty;
  * window, sample rate and behaviors come from the frozen decision tree: the branch in the committed decision
    record `eval/flies_decision.json` when it exists, else the GO branch (the lunge gate on dev: mid5, 30 fps,
    both behaviors). The CLI cannot pick them.
The scorer re-checks the hashes and the git diff; these guards make a wrong run fail before it writes anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .predict import REPO, git_state, in_frozen_tree

DECISION_FILE = "eval/flies_decision.json"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def check_frozen_run(frozen_path: Path, detectors_path: Path, *, override: str | None, repo: Path = REPO,
                     state: dict | None = None) -> dict:
    """Raise SystemExit on any violation; return a record for the manifest."""
    cfg = json.loads(Path(frozen_path).read_text())
    probs: list[str] = []
    if override:
        probs.append("--override is not allowed under --frozen-config")
    want = {a["sha256"] for a in cfg.get("assets", {}).get("detectors", [])}
    got = sha256_file(detectors_path)
    if got not in want:
        probs.append(f"--detectors ({Path(detectors_path).name}, sha256 {got[:12]}) is not the frozen detectors "
                     f"asset ({sorted(w[:12] for w in want)})")
    n = 0
    for role, items in cfg.get("assets", {}).items():
        for a in items:
            p = Path(repo) / a["path"]
            n += 1
            if not p.exists() or sha256_file(p) != a["sha256"]:
                probs.append(f"frozen asset {role}:{a['path']} differs from the frozen hash")
    state = state if state is not None else git_state(repo)
    dirty = [p for p in state.get("run_dirty_paths", []) if in_frozen_tree(p)]
    if dirty:
        probs.append(f"uncommitted changes in frozen paths: {dirty[:5]}")
    if probs:
        raise SystemExit("frozen run refused: " + "; ".join(probs))
    return {"frozen_sha256": sha256_file(frozen_path), "frozen_at_commit": cfg.get("frozen_at_commit"),
            "assets_checked": n, "detectors_sha256": got}


def run_branch(cfg: dict, decision_path: Path | None, requested_window: str | None = None) -> dict:
    """{"branch", "window", "sample_fps", "behaviors", "decision_record_sha256"} from the frozen tree.

    With a decision record the branch is the recorded one; without it, GO (the dev lunge gate). A `requested_window`
    that disagrees is refused rather than followed."""
    name, sha = "GO", None
    if decision_path is not None and Path(decision_path).exists():
        doc = json.loads(Path(decision_path).read_text())
        if doc.get("branch") in cfg["branches"]:
            name, sha = doc["branch"], sha256_file(decision_path)
    br = cfg["branches"][name]
    if requested_window not in (None, br["window"]):
        raise SystemExit(f"frozen run refused: window {requested_window} requested, the frozen tree says "
                         f"{br['window']} (branch {name})")
    return {"branch": name, **br, "decision_record_sha256": sha}
