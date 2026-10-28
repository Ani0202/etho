"""Synthetic CalMS21-shaped data for eval tests (no network, no real labels)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from eval.common import VOCAB

A, I, M, O = (VOCAB[k] for k in ("attack", "investigation", "mount", "other"))  # noqa: E741


def mouse_paths(T: int, seed: int = 0) -> np.ndarray:
    """[T, 2 mice, 2] smooth, well-separated, moving centroid paths."""
    t = np.arange(T)
    c0 = np.stack([300 + 150 * np.cos(t / 40), 280 + 100 * np.sin(t / 40)], axis=1)
    c1 = np.stack([720 + 150 * np.cos(t / 33 + 1), 280 + 100 * np.sin(t / 33 + 1)], axis=1)
    return np.stack([c0, c1], axis=1)


def keypoints_from_paths(paths: np.ndarray, score: float = 0.9) -> tuple[np.ndarray, np.ndarray]:
    """kp [T,2,2,7] whose 7-point mean equals the path; scores [T,2,7]."""
    off = np.array([[0, -60], [-12, -40], [12, -40], [0, -30], [-15, 40], [15, 40], [0, 90]], float)
    off -= off.mean(axis=0)
    kp = paths[:, :, :, None] + off.T[None, None, :, :]
    return kp, np.full((paths.shape[0], 2, 7), score)


def write_dataset(root: Path, seqs: dict, split_name: str = "dev") -> Path:
    """seqs: {name: labels int8[T]} -> labels dir + split file; returns the split file path."""
    d = root / "labels" / ("test" if split_name == "test" else "train")
    d.mkdir(parents=True, exist_ok=True)
    for i, (name, lab) in enumerate(seqs.items()):
        kp, sc = keypoints_from_paths(mouse_paths(len(lab), i))
        np.savez_compressed(d / f"{name}.npz", labels=np.asarray(lab, np.int8), keypoints=kp.astype(np.float16),
                            scores=sc.astype(np.float16), vocab=json.dumps(VOCAB))
    split = {"dev": [], "dev_holdout": [], "test": []}
    split[split_name] = list(seqs)
    sf = root / "split.json"
    sf.write_text(json.dumps(split))
    return sf


def tracks_from_paths(paths: np.ndarray, step: int = 3, labels=("resident", "intruder"), lag: int = 0) -> dict:
    """Perfect tracks at grid frames: track k sits on mouse k at keypoint frame f + lag."""
    T = paths.shape[0]
    rows = {k: [] for k in ("frame", "track_id", "label", "cx", "cy", "area", "present")}
    for f in range(0, T, step):
        g = min(max(f + lag, 0), T - 1)
        for m in range(2):
            rows["frame"].append(f)
            rows["track_id"].append(m)
            rows["label"].append(labels[m])
            rows["cx"].append(paths[g, m, 0])
            rows["cy"].append(paths[g, m, 1])
            rows["area"].append(10000.0)
            rows["present"].append(True)
    return {k: np.asarray(v) for k, v in rows.items()}


def save_tracks(path: Path, tr: dict) -> None:
    np.savez(path, **{k: (v.astype(str) if k == "label" else v) for k, v in tr.items()})
