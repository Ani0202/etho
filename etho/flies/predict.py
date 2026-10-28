"""Fly vs. Fly: apply synthesized detectors to a tracked movie and write predictions in eval/flies_score.py's format.

Label-free: reads `features.npz` (etho/tracking/fly_pipeline) and a detectors spec, nothing else.

Detectors spec (`configs/detectors/F1_flies.json`, or an experiment file with the same shape):
  {"behaviors": {<name>: {"candidates": [{"code", "model", "prompt_hash", ...}], "use": [indices],
                          "min_votes": k, "post": {...}}}}
A sample is positive when at least `min_votes` of the `use`d candidates flag it (one candidate = no vote). Then the
post-processing `post` (all durations in seconds, so the same spec runs at 30 and 10 fps):
  bridge_s    fill gaps shorter than this
  min_s       drop runs shorter than this
  pad_s       extend every run by this much on both sides
  peak_len_s  replace every run by one bout of this length centred on it (events that are a few samples long)
The actor per pair-positive sample (`<behavior>_by_track`) comes from features: the fly with the larger
`minor_ratio` for wing_threat, the larger `toward_speed` (mean over the bout) for lunge.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np

from etho.rules.sandbox import run_detector

from .extras import add_extras

WINDOWS = {"mid5": (22500, 31500), "mid10": (18000, 36000)}
NATIVE_FPS = 30
BEHAVIORS = ("wing_threat", "lunge")
ACTOR_FEATURE = {"wing_threat": "minor_ratio", "lunge": "toward_speed"}
REPO = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True as half-open (start, end)."""
    m = np.asarray(mask, bool)
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    return list(zip(np.nonzero(d == 1)[0].tolist(), np.nonzero(d == -1)[0].tolist(), strict=True))


def postprocess(mask: np.ndarray, fps: float, post: dict | None) -> np.ndarray:
    post = post or {}
    m = np.asarray(mask, bool).copy()
    n = len(m)
    if post.get("bridge_s"):
        g = int(round(post["bridge_s"] * fps))
        rs = runs(m)
        for (_, e0), (s1, _) in zip(rs, rs[1:], strict=False):
            if s1 - e0 <= g:
                m[e0:s1] = True
    if post.get("min_s"):
        k = int(round(post["min_s"] * fps))
        for s, e in runs(m):
            if e - s < k:
                m[s:e] = False
    if post.get("peak_len_s"):
        L = max(1, int(round(post["peak_len_s"] * fps)))
        out = np.zeros(n, bool)
        for s, e in runs(m):
            c = (s + e) // 2
            a = max(0, c - L // 2)
            out[a:min(n, a + L)] = True
        m = out
    if post.get("pad_s"):
        p = int(round(post["pad_s"] * fps))
        out = m.copy()
        for s, e in runs(m):
            out[max(0, s - p):min(n, e + p)] = True
        m = out
    return m


def detect_pair(spec: dict, table: dict[str, np.ndarray], fps: float, post_override: dict | None = None
                ) -> np.ndarray:
    """Pair-level mask for one behavior: sandboxed candidate(s), majority vote, post-processing."""
    cands = [spec["candidates"][i] for i in spec["use"]]
    votes = sum(run_detector(c["code"], table, fps).astype(int) for c in cands)
    mask = votes >= int(spec.get("min_votes", 1))
    return postprocess(mask, fps, post_override if post_override is not None else spec.get("post"))


def assign_actor(behavior: str, pair: np.ndarray, table: dict[str, np.ndarray]) -> np.ndarray:
    """uint8 [2, K]: per bout, the Etho track (0 = a, 1 = b) that is the actor; zero where the pair mask is zero."""
    feat = ACTOR_FEATURE[behavior]
    xa = np.nan_to_num(np.asarray(table[f"a_{feat}"], float), nan=0.0)
    xb = np.nan_to_num(np.asarray(table[f"b_{feat}"], float), nan=0.0)
    by = np.zeros((2, len(pair)), np.uint8)
    for s, e in runs(pair):
        if behavior == "lunge":  # the peak, not the mean: the 4-frame strike is the signal
            t = int(xb[s:e].max() > xa[s:e].max())
        else:
            t = int(xb[s:e].mean() > xa[s:e].mean())
        by[t, s:e] = 1
    return by


def predict_movie(table: dict[str, np.ndarray], spec: dict, window: str, fps: float,
                  post_override: dict[str, dict] | None = None) -> dict[str, np.ndarray]:
    """One movie's prediction arrays in the eval/flies_score.py format."""
    w = WINDOWS[window]
    step = int(round(NATIVE_FPS / fps))
    want = w[0] + step * np.arange(-(-(w[1] - w[0]) // step))
    src = np.asarray(table["src_frame"])
    if not np.array_equal(src, want):
        raise ValueError(f"features do not cover window {window} at {fps:g} fps (src_frame {src[:2]}..{src[-2:]})")
    out: dict[str, np.ndarray] = {"window": np.array(w), "sample_fps": np.array(float(fps))}
    feats = add_extras(table, fps) if spec.get("extras") else table  # the detectors may use the *_raw columns
    for b, bspec in spec["behaviors"].items():
        pair = detect_pair(bspec, feats, fps, (post_override or {}).get(b))
        out[b] = pair.astype(np.uint8)
        out[f"{b}_by_track"] = assign_actor(b, pair, feats)
    xy = np.stack([np.stack([table["a_cx"], table["a_cy"]], 1), np.stack([table["b_cx"], table["b_cy"]], 1)], 1)
    xy[~(np.asarray(table["a_present"]) > 0), 0] = np.nan
    xy[~(np.asarray(table["b_present"]) > 0), 1] = np.nan
    out["track_xy"] = xy.astype(float)  # native 144 px: the tracker already divides out any upscale
    out["identity_unknown"] = np.asarray(table["id_unknown"]) > 0
    out["id_epoch"] = np.asarray(table["id_epoch"])
    return out


def git_state(repo: Path = REPO) -> dict:
    def run(*a: str) -> str:
        try:
            return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                                  check=True).stdout.rstrip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
    status = run("status", "--porcelain")
    return {"run_commit": run("rev-parse", "HEAD") or None,
            "run_dirty_paths": [line[3:] for line in status.splitlines()][:50]}


def write_predictions(out_dir: Path, per_movie: dict[int, dict[str, np.ndarray]], *, config: dict,
                      cost_usd: float = 0.0, wall_clock_s: float | None = None, calls: int = 0,
                      notes: str = "", frozen_config: Path | None = None) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for m, arrs in per_movie.items():
        np.savez(out_dir / f"movie{m}.npz", **arrs)
    cfg = {**config, **git_state()}
    if frozen_config is not None:
        cfg["frozen_sha256"] = sha256_file(Path(frozen_config))
    (out_dir / "manifest.json").write_text(json.dumps(
        {"config": cfg, "cost": {"usd": cost_usd}, "wall_clock_s": wall_clock_s, "calls": calls, "notes": notes,
         "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1, default=float))
