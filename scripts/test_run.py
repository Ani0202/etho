"""Run the frozen config (configs/frozen_test_v1.json) on a split and write the four prediction arms
(zs_rules, zs_t1, fs_rules, fs_t1) in the format eval.score_arms expects. Product code only: no labels.

  uv run python scripts/test_run.py --split dev_holdout --run PF            # $0 pre-flight (all cached)
  uv run python scripts/test_run.py --split test --run T1 --stages track,rules
  uv run python scripts/test_run.py --split test --run T1 --stages t1 --allow-t1   # needs Aniket's OK (> $5)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import numpy as np

from etho.adjudication.tier1 import CONFIDENT, DEFAULT_ALLOW, adjudicate, apply, make_windows
from etho.gateway import Gateway
from etho.rules.sandbox import run_detector
from etho.tracking.pipeline import count_frames, track_video

CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}
PRIORITY = ("mount", "attack", "investigation")
FROZEN = Path("configs/frozen_test_v1.json")


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify_frozen() -> dict:
    cfg = json.loads(FROZEN.read_text())
    bad = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("path"), str) and isinstance(o.get("sha256"), str):
                if not Path(o["path"]).exists() or sha(Path(o["path"])) != o["sha256"]:
                    bad.append(o["path"])
            for v in o.values():
                walk(v)
    walk(cfg)
    if bad:
        raise SystemExit(f"frozen config hash mismatch: {bad}")
    return cfg


def verify_product_tree(cfg: dict) -> None:
    """The code that runs must be the frozen code: etho/ unchanged since frozen_at_commit, same tier-1 prompt."""
    from etho.adjudication import tier1
    diff = subprocess.run(["git", "diff", "--stat", cfg["frozen_at_commit"], "--", "etho/"], capture_output=True,
                          text=True).stdout.strip()
    if diff:
        raise SystemExit(f"etho/ differs from the frozen commit {cfg['frozen_at_commit']}:\n{diff}")
    ph = hashlib.sha256(tier1.PROMPT.encode()).hexdigest()
    if ph != cfg["zero_shot_arm"]["tier1"]["prompt_sha256"]:
        raise SystemExit("tier-1 prompt hash differs from the frozen config")


def video_for(seq: str, split: str) -> Path:
    return Path(f"data/calms21/videos/{'test' if split == 'test' else 'train'}/{seq}.mp4")


def labels_from(dets: list[dict], table: dict) -> np.ndarray:
    masks = {d["behavior"]: run_detector(d["code"], table, 10.0) for d in dets}
    lab = np.full(len(next(iter(table.values()))), CODES["other"], np.int8)
    for b in reversed(PRIORITY):
        lab[masks[b]] = CODES[b]
    return lab


def save(arm_dir: Path, seq: str, lab: np.ndarray) -> None:
    arm_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(arm_dir / f"{seq}.npz", labels=lab, sample_fps=np.float64(10.0), sample_stride=np.int64(3))


def machine() -> dict:
    return {"cpus": os.cpu_count(), "platform": platform.platform(), "python": platform.python_version(),
            "mem_gb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["dev", "dev_holdout", "test"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--stages", default="track,rules,t1")
    ap.add_argument("--allow-t1", action="store_true", help="tier-1 spends on Fireworks; on test needs approval")
    a = ap.parse_args()
    cfg = verify_frozen()
    verify_product_tree(cfg)
    t_start_ledger = time.time()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    seqs = json.loads(Path("eval/splits/calms21_v1.json").read_text())[a.split]
    run = Path("data/runs") / a.run
    run.mkdir(parents=True, exist_ok=True)
    stages = set(a.stages.split(","))
    log_path = run / f"pipeline_{a.split}.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {"split": a.split, "seqs": {}}
    gw = Gateway()
    arms_dets = {"zs": json.loads(Path(cfg["zero_shot_arm"]["detectors"]["path"]).read_text())["detectors"],
                 "fs": json.loads(Path(cfg["few_shot_arm"]["detectors"]["path"]).read_text())["detectors"]}
    defs = json.loads(Path(cfg["zero_shot_arm"]["definitions"]["path"]).read_text())

    if "track" in stages:
        for s in seqs:
            t0 = time.monotonic()
            entry = {"src_frames": count_frames(video_for(s, a.split))}
            try:
                r = track_video(video_for(s, a.split), run / s, gw=gw, public_data=True, tag="eval-sam")
                entry |= {"ok": True, "n_samples": r.n_samples, "video_s": r.n_samples / 10.0, "timings": r.timings,
                          "sam_cost_usd": r.cost_usd}
            except Exception as e:  # a failed video still gets a prediction file (all "other")
                entry |= {"ok": False, "error": repr(e)[:500]}
            entry["track_wall_s"] = time.monotonic() - t0
            log["seqs"][s] = {**log["seqs"].get(s, {}), **entry}
            print(s, {k: entry.get(k) for k in ("ok", "n_samples", "src_frames", "sam_cost_usd")}, flush=True)
            log_path.write_text(json.dumps(log, indent=1, default=float))

    def table(s):
        p = run / s / "features.npz"
        return dict(np.load(p)) if p.exists() else None

    if "rules" in stages:
        for arm, dets in arms_dets.items():
            t0 = time.monotonic()
            for s in seqs:
                ts = time.monotonic()
                t = table(s)
                n = log["seqs"][s].get("n_samples") or -(-log["seqs"][s]["src_frames"] // 3)
                save(run / f"{arm}_rules", s, labels_from(dets, t) if t is not None else
                     np.full(n, CODES["other"], np.int8))
                log["seqs"][s][f"{arm}_rules_wall_s"] = time.monotonic() - ts
            log[f"{arm}_rules_wall_s"] = time.monotonic() - t0
        log_path.write_text(json.dumps(log, indent=1, default=float))

    if "t1" in stages:
        if not a.allow_t1:
            raise SystemExit("tier-1 stage needs --allow-t1 (on test: only after Aniket approves the Fireworks spend)")
        for arm in arms_dets:
            t0 = time.monotonic()
            cost, calls, wall_by_seq = 0.0, 0, {}
            allv = []
            for s in seqs:
                t = table(s)
                lab = np.load(run / f"{arm}_rules" / f"{s}.npz")["labels"]
                ts = time.monotonic()
                v = adjudicate(run, s, make_windows(s, lab, t, CODES, 10.0), defs, gw=gw, effort="low") \
                    if t is not None else []
                wall_by_seq[s] = time.monotonic() - ts
                log["seqs"][s][f"{arm}_t1_wall_s"] = wall_by_seq[s]
                log["seqs"][s][f"{arm}_t1_cost_usd"] = sum(x["cost_usd"] for x in v)
                # uncached-equivalent tier-1 cost from token usage (cache hits keep their original usage)
                log["seqs"][s][f"{arm}_t1_uncached_usd"] = sum(
                    ((x.get("in_tok") or 0) * 1.25 + (x.get("out_tok") or 0) * 4.25) / 1e6 for x in v)
                save(run / f"{arm}_t1", s, apply(lab, v, CODES, CONFIDENT, DEFAULT_ALLOW))
                cost += sum(x["cost_usd"] for x in v)
                calls += sum(not x["cache_hit"] for x in v)
                allv += v
            (run / f"verdicts_{a.split}_{arm}_t1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in allv))
            log[f"{arm}_t1"] = {"wall_s": time.monotonic() - t0, "cost_usd": cost, "new_calls": calls,
                                "windows": len(allv), "wall_by_seq": wall_by_seq}
        log_path.write_text(json.dumps(log, indent=1, default=float))

    # Failed / possibly billed calls during this invocation (gateway ledger)
    led = [json.loads(x) for x in Path("ops/spend.jsonl").read_text().splitlines() if x.strip()]
    from datetime import datetime
    bad = [r for r in led if r.get("error") and datetime.fromisoformat(r["time"]).timestamp() >= t_start_ledger]
    log.setdefault("failed_calls", []).extend({k: r.get(k) for k in ("time", "provider", "model", "cost_usd",
                                                                       "maybe_billed", "error")} for r in bad)
    log_path.write_text(json.dumps(log, indent=1, default=float))
    # Manifests (every arm, from whatever stages have run). Denominator: every video's true duration.
    video_s = sum(v["src_frames"] / 30.0 for v in log["seqs"].values())
    track_wall = sum(v.get("track_wall_s", 0) for v in log["seqs"].values())
    sam_cost = sum(v.get("sam_cost_usd", 0) for v in log["seqs"].values())
    sam_uncached = sum(max(v.get("n_samples", 0), 50) * 0.0002 for v in log["seqs"].values())
    for arm in arms_dets:
        for kind in ("rules", "t1"):
            d = run / f"{arm}_{kind}"
            if not d.exists():
                continue
            per_video = {}
            for s, v in log["seqs"].items():
                parts = {"track_s": v.get("track_wall_s"), "rules_s": v.get(f"{arm}_rules_wall_s"),
                         "tier1_s": v.get(f"{arm}_t1_wall_s") if kind == "t1" else 0.0}
                tot = sum(x for x in parts.values() if x is not None)
                per_video[s] = {**parts, "summed_s": tot, "video_s": v["src_frames"] / 30.0,
                                "x_realtime": tot / (v["src_frames"] / 30.0), "ok": v.get("ok", False)}
            xs = [p["x_realtime"] for p in per_video.values()]
            t1 = log.get(f"{arm}_t1", {}) if kind == "t1" else {}
            wall = {"track_s": track_wall, "rules_s": log.get(f"{arm}_rules_wall_s"), "tier1_s": t1.get("wall_s"),
                    "video_s": video_s}
            total = track_wall + (log.get(f"{arm}_rules_wall_s") or 0) + (t1.get("wall_s") or 0)
            wall |= {"total_s": total, "x_realtime": total / video_s if video_s else None,
                     "per_video": per_video, "median_x_realtime": float(np.median(xs)) if xs else None,
                     "max_x_realtime": max(xs) if xs else None,
                     "note": ("summed processing time per video: chunking+SAM+decode+features, then rules, then "
                              "tier-1, each timed on that video; videos run one after another, each video's chunks "
                              "and tier-1 calls run in parallel; denominator = all videos' true durations")}
            t1c = t1.get("cost_usd", 0.0)
            t1u = sum(v.get(f"{arm}_t1_uncached_usd", 0.0) for v in log["seqs"].values()) if kind == "t1" else 0.0
            cst = {"sam_usd": sam_cost, "tier1_usd": t1c, "total_usd": sam_cost + t1c,
                   "uncached_equivalent_usd": {"sam": sam_uncached, "tier1": t1u, "total": sam_uncached + t1u},
                   "failed_or_maybe_billed_calls": len(log.get("failed_calls", []))}
            cst["per_video_min_usd"] = cst["total_usd"] / (video_s / 60) if video_s else None
            cst["uncached_per_video_min_usd"] = (sam_uncached + t1u) / (video_s / 60) if video_s else None
            (d / "manifest.json").write_text(json.dumps({
                "sample_fps": 10, "arm": f"{arm}_{kind}",
                "config": {"frozen_config": str(FROZEN), "frozen_sha256": sha(FROZEN),
                           "frozen_at_commit": cfg["frozen_at_commit"], "run_commit": commit},
                "cost": cst, "wall_clock_s": wall, "calls": {"tier1_new": t1.get("new_calls"),
                                                             "tier1_windows": t1.get("windows")},
                "machine": machine(),
                "failed_sequences": [s for s, v in log["seqs"].items() if v.get("ok") is False],
                "frames": {s: {"src_frames": v.get("src_frames"), "n_samples": v.get("n_samples")}
                           for s, v in log["seqs"].items()}}, indent=1, default=float))
    print("arms:", sorted(p.name for p in run.iterdir() if p.is_dir() and p.name.endswith(("_rules", "_t1"))))


if __name__ == "__main__":
    main()
