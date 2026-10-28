"""Build the CalMS21 Task 1 eval splits (eval/splits/calms21_v1.json) deterministically.

Etho is zero-shot: the official CalMS21 *train* split is never used for fitting. It is only a pool
from which we draw a small **dev** set (prompt / threshold / effort tuning) and a disjoint
**dev-holdout** set (checked rarely, to detect overfitting to dev). The official *test* split is the
held-out test set and is never used for tuning.

Selection rule (documented in the output JSON too):
  1. Pool = the 70 official train sequences minus any shorter than MIN_LEN frames. Sequences
     already inspected during spikes are forced into dev (they are "seen" data and must not sit
     in dev-holdout).
  2. A seeded random search (stdlib `random.Random(SEED)`) draws candidate dev sets of 6-10
     sequences and accepts the first that meets every DEV constraint below.
  3. From the remaining pool, the same search draws a dev-holdout set meeting HOLDOUT constraints.
  4. Test = all 19 official test sequences (option A in docs/EVAL_PLAN.md). A stratified subset
     (option B) is listed in the JSON as `test_subset_option_b` for budget fallback only.
Only label *counts* from the index files are used; no model output influences the split.

Usage: uv run python -m eval.make_splits
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

SEED = 20261028
IDX = Path("data/calms21/seq")
OUT = Path("eval/splits/calms21_v1.json")
SEEN = ["mouse016_task1_annotator1", "mouse050_task1_annotator1"]  # spike clips, already inspected
SUB = 3  # 30 fps -> 10 fps: keep every 3rd frame
MAX_TRIALS = 200_000

MIN_LEN = 900  # drop sub-30-second sequences from the pool (only mouse036, 96 frames)
# Dev max 37,500 frames keeps a 2-phrase SAM pass at 10 fps under the $5 single-job limit.
# "long attack" = a >= 10k-frame sequence with >= 500 attack frames: all 5 test attack sequences are
# 17-20k frames long, so dev must include at least one long, mostly-"other" video with attack.
DEV = dict(min_frames=30_000, max_frames=37_500, attack=2_500, mount=2_500, investigation=8_000,
           min_seqs_per_behavior=3, min_long_attack_seqs=1, long_len=10_000, n_range=(6, 10))
HOLDOUT = dict(min_frames=20_000, max_frames=30_000, attack=1_000, mount=1_000, investigation=5_000,
               min_seqs_per_behavior=2, min_long_attack_seqs=1, long_len=10_000, n_range=(4, 8))
BEHAVIORS = ("attack", "investigation", "mount")


def load(split: str) -> dict:
    return json.loads((IDX / f"index_calms21_task1_{split}.json").read_text())


def summarize(names: list[str], idx: dict) -> dict:
    frames = sum(idx[n]["frames"] for n in names)
    s = {"n_seqs": len(names), "frames_30fps": frames,
         "frames_10fps_billed": sum(math.ceil(idx[n]["frames"] / SUB) for n in names)}
    for b in BEHAVIORS + ("other",):
        s[f"{b}_frames"] = sum(idx[n]["counts"][b] for n in names)
        s[f"{b}_seqs"] = sum(1 for n in names if idx[n]["counts"][b] > 0)
    return s


def ok(names: list[str], idx: dict, c: dict) -> bool:
    s = summarize(names, idx)
    if not c["min_frames"] <= s["frames_30fps"] <= c["max_frames"]:
        return False
    if any(s[f"{b}_frames"] < c[b] for b in BEHAVIORS):
        return False
    if any(s[f"{b}_seqs"] < c["min_seqs_per_behavior"] for b in BEHAVIORS):
        return False
    long_attack = [n for n in names if idx[n]["frames"] >= c["long_len"] and idx[n]["counts"]["attack"] >= 500]
    return len(long_attack) >= c["min_long_attack_seqs"]


def search(rng: random.Random, pool: list[str], forced: list[str], idx: dict, c: dict) -> tuple[list[str], int]:
    free = [n for n in pool if n not in forced]
    lo, hi = c["n_range"]
    for trial in range(1, MAX_TRIALS + 1):
        k = rng.randint(lo, hi) - len(forced)
        cand = sorted(forced + rng.sample(free, k))
        if ok(cand, idx, c):
            return cand, trial
    raise RuntimeError("no split satisfies constraints; relax them and record why")


def main() -> None:
    train, test = load("train"), load("test")
    pool = sorted(n for n in train if train[n]["frames"] >= MIN_LEN)
    rng = random.Random(SEED)
    dev, dev_trial = search(rng, pool, SEEN, train, DEV)
    rest = [n for n in pool if n not in dev]
    holdout, ho_trial = search(rng, rest, [], train, HOLDOUT)
    test_all = sorted(test)
    # Option B (fallback only): every test sequence containing attack, plus the mount-richest
    # remaining sequences by mount/frames ratio until >= 40% of test mount frames are covered.
    att = [n for n in test_all if test[n]["counts"]["attack"] > 0]
    others = sorted((n for n in test_all if n not in att),
                    key=lambda n: -test[n]["counts"]["mount"] / test[n]["frames"])
    sub = list(att)
    total_mount = sum(test[n]["counts"]["mount"] for n in test_all)
    for n in others:
        if sum(test[m]["counts"]["mount"] for m in sub) >= 0.4 * total_mount:
            break
        sub.append(n)
    sub = sorted(sub)
    out = {
        "version": "calms21_v1",
        "status": "DRAFT (not committed; targets not frozen)",
        "seed": SEED,
        "generator": "eval/make_splits.py",
        "source_indexes": ["data/calms21/seq/index_calms21_task1_train.json",
                           "data/calms21/seq/index_calms21_task1_test.json"],
        "selection_rule": __doc__.split("Selection rule")[1].split("Usage:")[0].strip(),
        "constraints": {"min_len_frames": MIN_LEN, "dev": DEV, "dev_holdout": HOLDOUT},
        "search_trials_until_accept": {"dev": dev_trial, "dev_holdout": ho_trial},
        "seen_forced_into_dev": {"mouse016_task1_annotator1": "frames 2400-3000",
                                 "mouse050_task1_annotator1": "frames 0-600"},
        "dev": dev,
        "dev_holdout": holdout,
        "test": test_all,
        "test_subset_option_b": sub,
        "unused_train": [n for n in rest if n not in holdout],
        "excluded_too_short": sorted(n for n in train if train[n]["frames"] < MIN_LEN),
        "summary": {"dev": summarize(dev, train), "dev_holdout": summarize(holdout, train),
                    "test": summarize(test_all, test), "test_subset_option_b": summarize(sub, test)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    main()
