"""Assemble the fly detectors config and write the frozen pipeline config (EVAL_PLAN 14.12 item 10).

  uv run python scripts/freeze_flies.py assemble --wing <detectors.json> --lunge <detectors.json> \\
      [--wing-post JSON] [--lunge-post JSON] [--wing-use 0,1 --wing-min-votes 2] --out configs/detectors/F1_flies.json
  # commit the detectors file, then:
  uv run python scripts/freeze_flies.py freeze --out configs/frozen_flies_v1.json

`assemble` keeps, per behavior, only the candidates that are used (their code, model, prompt hash, calls) plus the
label-free selection record of the whole candidate set. `freeze` hashes every asset, sets `frozen_at_commit` to HEAD
(it refuses if an asset has uncommitted changes), and writes the config; commit that file afterwards, touching
nothing else under etho/ or configs/. No label and no model call is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS = "configs/flies_definitions.json"
DETECTORS = "configs/detectors/F1_flies.json"
TRACKING_CFG = "configs/flies_tracking.json"
# the frozen decision tree (eval/flies_score.py BRANCHES; verify_frozen_flies_config requires equality)
BRANCHES = {"GO": {"window": "mid5", "sample_fps": 30, "behaviors": ["wing_threat", "lunge"]},
            "NO-GO": {"window": "mid10", "sample_fps": 10, "behaviors": ["wing_threat"]}}
ASSETS = {
    "definitions": [DEFINITIONS],
    "detectors": [DETECTORS],
    "tracking": [TRACKING_CFG, "etho/tracking/fly_pipeline.py", "etho/tracking/fly_identity.py",
                 "etho/tracking/fly_features.py", "etho/tracking/fly_video.py", "etho/tracking/features.py",
                 "etho/tracking/sam_parse.py", "etho/tracking/mask_codec.py", "etho/tracking/pipeline.py"],
    "runner": ["scripts/run_fly.py", "scripts/run_fly_etho.py", "etho/flies/predict.py", "etho/flies/extras.py",
               "etho/flies/frozen.py", "etho/rules/sandbox.py"],
    "prompts": ["etho/flies/synth.py"],
    "other": ["etho/rules/synthesize.py", "scripts/freeze_flies.py"],
}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=True).stdout.strip()


def assemble(a) -> None:
    out = {"version": 1, "name": "F1_flies", "definitions": DEFINITIONS,
           "definitions_sha256": sha256(ROOT / DEFINITIONS), "behaviors": {}, "sources": {}}
    for beh, src, post, use, mv in (("wing_threat", a.wing, a.wing_post, a.wing_use, a.wing_min_votes),
                                    ("lunge", a.lunge, a.lunge_post, a.lunge_use, a.lunge_min_votes)):
        doc = json.loads(Path(src).read_text())
        b = doc["behaviors"][beh]
        chosen = [int(x) for x in use.split(",")] if use else list(b["use"])
        cands = []
        for i in chosen:
            c = dict(b["candidates"][i])
            c["source_index"] = i
            cands.append(c)
        out["behaviors"][beh] = {
            "extras": bool(doc.get("extras")), "variant": doc.get("variant"), "model": doc["model"],
            "effort": doc["effort"], "public_data": doc.get("public_data", True), "tag": doc.get("tag"),
            "n_candidates_synthesized": len(b["candidates"]), "selection": b.get("selection"),
            "candidates": cands, "use": list(range(len(cands))),
            "min_votes": mv if mv is not None else (1 if len(cands) == 1 else len(cands) // 2 + 1),
            "post": json.loads(post) if post else b.get("post", {})}
        out.setdefault("synthesis_cost_usd_all_candidates", {})[beh] = round(
            sum(x["cost_usd"] for c in b["candidates"] for x in c["calls"]), 6)
        out["sources"][beh] = {"file": Path(src).name, "name": doc.get("name"),
                               "file_sha256": sha256(Path(src)), "stats_from": doc.get("stats_from")}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(f"wrote {a.out} sha256 {sha256(Path(a.out))[:12]}")


def freeze(a) -> None:
    paths = [p for ps in ASSETS.values() for p in ps]
    dirty = git("status", "--porcelain", "--", *paths, "etho", "configs")
    if dirty and not a.allow_dirty:
        raise SystemExit("refusing to freeze with uncommitted changes under etho/, configs/ or the assets:\n" + dirty)
    missing = [p for p in paths if not (ROOT / p).exists()]
    if missing:
        raise SystemExit(f"missing assets: {missing}")
    tc = json.loads((ROOT / TRACKING_CFG).read_text())
    det = json.loads((ROOT / DETECTORS).read_text())
    cfg = {"name": "flies_v1", "frozen_at_commit": git("rev-parse", "HEAD"), "branches": BRANCHES,
           "window": {"GO": [22500, 31500], "NO-GO": [18000, 36000]},
           "tracking_params": {k: tc[k] for k in ("phrase", "scale", "chunk_s", "overlap_s", "src_fps")},
           "models": {b: {"model": v["model"], "effort": v["effort"], "public_data": v["public_data"]}
                      for b, v in det["behaviors"].items()},
           "note": ("Rules-only: the detectors are model-written code frozen in configs/detectors/F1_flies.json; "
                    "prediction makes no model call. Dev movies 1-2 were used to choose the prompt variant, the "
                    "candidate selection rule and the post-processing."),
           "assets": {role: [{"path": p, "sha256": sha256(ROOT / p)} for p in ps] for role, ps in ASSETS.items()}}
    Path(a.out).write_text(json.dumps(cfg, indent=1) + "\n")
    print(f"wrote {a.out} frozen_at_commit {cfg['frozen_at_commit'][:10]} sha256 {sha256(Path(a.out))[:12]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s1 = sp.add_parser("assemble")
    for b in ("wing", "lunge"):
        s1.add_argument(f"--{b}", required=True, type=Path)
        s1.add_argument(f"--{b}-post")
        s1.add_argument(f"--{b}-use")
        s1.add_argument(f"--{b}-min-votes", type=int)
    s1.add_argument("--out", type=Path, default=ROOT / DETECTORS)
    s2 = sp.add_parser("freeze")
    s2.add_argument("--out", type=Path, default=ROOT / "configs/frozen_flies_v1.json")
    s2.add_argument("--allow-dirty", action="store_true", help="tests only")
    a = ap.parse_args()
    {"assemble": assemble, "freeze": freeze}[a.cmd](a)


if __name__ == "__main__":
    main()
