"""Render a candidate window as one tiled image: N frames cropped around both animals, outlined.

Resident = red outline, intruder = green outline (from the tracker's stored RLE masks).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import cv2
import numpy as np
from pycocotools import mask as mask_utils

COLORS = {"resident": (0, 0, 255), "intruder": (0, 255, 0)}


def load_masks(run_dir: Path, samples: set[int]) -> dict[int, list[dict]]:
    """Per sample: [{role, box, mask}] for resident/intruder only, from raw RLE + identity roles."""
    info = json.loads((run_dir / "tracking.json").read_text())
    roles = {}
    for ch in info["chunks"]:
        for tr in ch["tracks"]:
            roles[tr["track"]] = tr["role"]
        for track, runs in ch.get("relabeled", {}).items():
            roles.setdefault(("runs", track), runs)
    out: dict[int, list[dict]] = {}
    for p in sorted(run_dir.glob("raw_c*.jsonl.gz")):
        with gzip.open(p, "rt") as f:
            for line in f:
                o = json.loads(line)
                if o["frame"] not in samples:
                    continue
                role = roles.get(o["track"], "unknown")
                if role not in COLORS:
                    continue
                r = o["rle"]
                m = mask_utils.decode({"size": r["size"], "counts": r["counts"].encode()})
                out.setdefault(o["frame"], []).append({"role": role, "box": o["box"], "mask": m})
    return out


def read_samples(chunks: list[dict], samples: list[int]) -> dict[int, np.ndarray]:
    """Read the given 10 fps sample indices sequentially from the chunk files."""
    want = sorted(set(samples))
    out = {}
    for ch in chunks:
        lo, hi = ch["start_sample"], ch["start_sample"] + ch["n_samples"]
        need = [s for s in want if lo <= s < hi]
        if not need:
            continue
        cap = cv2.VideoCapture(ch["path"])
        k = lo
        for s in need:
            while k <= s:
                ok, fr = cap.read()
                if not ok:
                    break
                if k == s:
                    out[s] = fr
                k += 1
        cap.release()
    return out


def render_strip(frames: dict[int, np.ndarray], masks: dict[int, list[dict]], samples: list[int], fps: float,
                 tile_h: int = 300, cols: int = 3) -> np.ndarray:
    # crop: union of both animals' boxes over the window, with margin
    boxes = [m["box"] for s in samples for m in masks.get(s, [])]
    H, W = next(iter(frames.values())).shape[:2]
    if boxes:
        x0, t = min(b[0] for b in boxes), min(b[1] for b in boxes)
        r, b = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad = int(0.15 * max(r - x0, b - t)) + 20
        x0, t, r, b = max(0, x0 - pad), max(0, t - pad), min(W, r + pad), min(H, b + pad)
    else:
        x0, t, r, b = 0, 0, W, H
    tiles = []
    for s in samples:
        img = frames.get(s)
        if img is None:
            img = np.zeros((H, W, 3), np.uint8)
        img = img.copy()
        for m in masks.get(s, []):
            full = np.zeros((H, W), np.uint8)
            bl, bt = m["box"][0], m["box"][1]
            mh, mw = m["mask"].shape
            full[bt:bt + mh, bl:bl + mw] = m["mask"][: H - bt, : W - bl]
            cs, _ = cv2.findContours(full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, cs, -1, COLORS[m["role"]], 2)
        crop = img[t:b, x0:r]
        scale = tile_h / crop.shape[0]
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), tile_h))
        cv2.putText(crop, f"t={s / fps:.1f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        tiles.append(crop)
    w = max(x.shape[1] for x in tiles)
    tiles = [cv2.copyMakeBorder(x, 2, 2, 2, 2 + w - x.shape[1], cv2.BORDER_CONSTANT, value=(40, 40, 40))
             for x in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    return np.vstack(rows)
