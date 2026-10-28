"""Overlay video: resident/intruder outlines from the stored RLE masks and the current behavior label
burned in, encoded to H.264 mp4 with ffmpeg.

Streaming only: raw tracker output (box-local RLE masks, one JSON line per object, ascending frame
order) is decoded one frame at a time and dropped, so memory stays flat for any video length.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
from pycocotools import mask as mask_utils

from .behaviors import PALETTE

ROLE_BGR = {"resident": (0, 0, 255), "intruder": (0, 255, 0)}  # same convention as the tier-1 strips


def _hex_bgr(h: str) -> tuple[int, int, int]:
    return int(h[5:7], 16), int(h[3:5], 16), int(h[1:3], 16)


def track_roles(seq_dir: Path) -> dict[str, str]:
    info = json.loads((seq_dir / "tracking.json").read_text())
    return {tr["track"]: tr["role"] for ch in info["chunks"] for tr in ch["tracks"]}


def iter_frame_masks(seq_dir: Path, roles: dict[str, str] | None = None) -> Iterator[tuple[int, list[tuple]]]:
    """Yield (sample index, [(role, (left, top), mask)]) in ascending order; resident/intruder only."""
    roles = roles if roles is not None else track_roles(seq_dir)
    cur, objs = None, []
    for p in sorted(seq_dir.glob("raw_c*.jsonl.gz")):
        with gzip.open(p, "rt") as f:
            for line in f:
                o = json.loads(line)
                role = roles.get(o["track"], "unknown")
                if role not in ROLE_BGR:
                    continue
                if o["frame"] != cur:
                    if cur is not None and objs:
                        yield cur, objs
                    cur, objs = o["frame"], []
                r = o["rle"]
                m = mask_utils.decode({"size": r["size"], "counts": r["counts"].encode()})
                objs.append((role, (o["box"][0], o["box"][1]), m))
    if cur is not None and objs:
        yield cur, objs


def draw_frame(img: np.ndarray, objs: list[tuple], label: str, color: tuple[int, int, int], t_s: float,
               scale: float = 1.0) -> None:
    th = max(2, int(round(2 * scale)))
    for role, (left, top), m in objs:
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE, offset=(left, top))
        cv2.drawContours(img, cs, -1, ROLE_BGR[role], th)
    font, fs = cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale
    text = f"{label}"
    (tw, tht), _ = cv2.getTextSize(text, font, fs, th)
    pad = int(8 * scale)
    cv2.rectangle(img, (pad, pad), (pad + tw + 2 * pad, pad + tht + 2 * pad), color, -1)
    cv2.putText(img, text, (2 * pad, pad + tht + pad), font, fs, (255, 255, 255), th, cv2.LINE_AA)
    cv2.putText(img, f"{t_s:.1f}s", (img.shape[1] - int(90 * scale), int(28 * scale)), font, 0.6 * scale,
                (0, 255, 255), max(1, th - 1), cv2.LINE_AA)


def render_overlay(chunk_paths: list[Path], seq_dir: Path, labels: np.ndarray, names: list[str], fps: float,
                   out: Path, *, max_width: int = 960, progress=None) -> dict:
    """Write `out` (H.264, faststart). `labels` are int codes (names by index, "other" = len(names))."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    masks = iter_frame_masks(seq_dir)
    nxt = next(masks, None)
    first = True
    proc = None
    errlog = tempfile.TemporaryFile()
    n_written = 0
    total = len(labels)
    try:
        for cp in chunk_paths:
            cap = cv2.VideoCapture(str(cp))
            while n_written < total:
                ok, img = cap.read()
                if not ok:
                    break
                i = n_written
                h, w = img.shape[:2]
                if first:
                    sc = min(1.0, max_width / w)
                    ow, oh = int(w * sc) // 2 * 2, int(h * sc) // 2 * 2
                    proc = subprocess.Popen(
                        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s",
                         f"{ow}x{oh}", "-r", f"{fps:g}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
                         "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errlog)
                    first = False
                objs = []
                while nxt is not None and nxt[0] < i:
                    nxt = next(masks, None)
                if nxt is not None and nxt[0] == i:
                    objs = nxt[1]
                    nxt = next(masks, None)
                code = int(labels[i])
                label = names[code] if code < len(names) else "other"
                color = _hex_bgr(PALETTE[names.index(label) % len(PALETTE)]) if label in names else (90, 90, 90)
                draw_frame(img, objs, label, color, i / fps, scale=max(1.0, h / 480))
                if (ow, oh) != (w, h):
                    img = cv2.resize(img, (ow, oh), interpolation=cv2.INTER_AREA)
                proc.stdin.write(img.tobytes())
                n_written += 1
                if progress and n_written % 200 == 0:
                    progress(n_written, total)
            cap.release()
        if proc is None:
            raise RuntimeError("no video frames could be read for the overlay")
        proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            errlog.seek(0)
            raise RuntimeError(f"ffmpeg failed: {errlog.read().decode(errors='replace')[:500]}")
        tmp.replace(out)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        errlog.close()
        tmp.unlink(missing_ok=True)
    return {"frames": n_written, "fps": fps, "path": str(out)}
