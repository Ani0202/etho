"""Video adapter for the fly path (ADR-002): a frame window, a stride, optional upscale, 60 s chunks.

Why not `pipeline.prepare_chunks`: that one cuts the *whole* file at a fixed 10 fps with no spatial
scaling and its chunks do not overlap. The fly path needs (a) a fixed frame window, (b) 30 fps
(stride 1) or 10 fps (stride 3), (c) a 4x lanczos upscale because the movies are 144x144 px, and (d)
a short overlap between consecutive chunks so identities can be stitched (fly_identity.py). The
frozen CalMS21 modules are not touched.

Sample/frame mapping is exact and recorded: global sample g (0-based inside the window) is source
frame `start + stride * g`. A chunk file contains global samples [own0 - overlap, own1) (no leading
overlap for chunk 0); each chunk *owns* [own0, own1), so every sample has exactly one owner.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .pipeline import count_frames

ADAPTER_VERSION = 1


@dataclass(frozen=True)
class FlyChunk:
    index: int
    path: str
    first: int        # global sample index of the first sample in the file (own0 - overlap)
    n_samples: int    # samples in the file
    own0: int         # global samples [own0, own1) belong to this chunk
    own1: int
    start: int        # source frame of global sample 0 (the window start)
    stride: int
    scale: int

    def src_frame(self, local: int) -> int:
        """Source frame of the chunk-local sample index `local`."""
        return self.start + self.stride * (self.first + local)

    @property
    def overlap(self) -> int:
        return self.own0 - self.first


def window_samples(start: int, stop: int, stride: int) -> int:
    """Samples in the source-frame window [start, stop) at `stride`."""
    return max(0, -(-(stop - start) // stride))


def plan_chunks(n_samples: int, fps: float, chunk_s: float = 60.0, overlap_s: float = 1.0
                ) -> list[tuple[int, int, int]]:
    """(first, own0, own1) per chunk. Chunk i>0 starts `overlap` samples before it owns anything."""
    per = max(1, int(round(chunk_s * fps)))
    ov = int(round(overlap_s * fps))
    plan = []
    for own0 in range(0, n_samples, per):
        own1 = min(own0 + per, n_samples)
        plan.append((max(0, own0 - ov), own0, own1))
    return plan


def upscale(frame: np.ndarray, scale: int) -> np.ndarray:
    if scale == 1:
        return frame
    h, w = frame.shape[:2]
    return cv2.resize(frame, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)


class _Writer:
    def __init__(self, path: Path, w: int, h: int, fps: float, crf: int):
        self.p = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
             "-r", f"{fps}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        self.p.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self) -> None:
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("ffmpeg failed while writing a chunk")


def prepare_fly_chunks(src: Path, out_dir: Path, *, start: int = 22500, stop: int = 31500, stride: int = 1,
                       scale: int = 4, chunk_s: float = 60.0, overlap_s: float = 1.0, src_fps: float = 30.0,
                       crf: int = 16) -> list[FlyChunk]:
    """Cut source frames [start, stop) every `stride`-th frame into overlapping chunk files.
    Cached by input identity under `out_dir/<key>/chunks.json`."""
    ident = (f"v{ADAPTER_VERSION}|{src.resolve()}|{src.stat().st_size}|{start}|{stop}|{stride}|{scale}|"
             f"{chunk_s}|{overlap_s}|{src_fps}|{crf}")
    d = out_dir / hashlib.sha256(ident.encode()).hexdigest()[:16]
    done = d / "chunks.json"
    if done.exists():
        return [FlyChunk(**c) for c in json.loads(done.read_text())["chunks"]]
    d.mkdir(parents=True, exist_ok=True)
    n = window_samples(start, stop, stride)
    fps = src_fps / stride
    plan = plan_chunks(n, fps, chunk_s, overlap_s)
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {src}")
    for _ in range(start):  # sequential skip: exact, and 144 px frames decode in ~0.2 ms
        if not cap.grab():
            raise RuntimeError(f"{src} has fewer than {start} frames")
    chunks = [FlyChunk(i, str(d / f"c{i:03d}.mp4"), first, own1 - first, own0, own1, start, stride, scale)
              for i, (first, own0, own1) in enumerate(plan)]
    writers: dict[int, _Writer] = {}
    for g in range(n):
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"{src}: ran out of frames at source frame {start + stride * g}")
        live = [c for c in chunks if c.first <= g < c.own1]
        if live:
            up = upscale(frame, scale)
            for c in live:
                if c.index not in writers:
                    writers[c.index] = _Writer(Path(c.path), up.shape[1], up.shape[0], fps, crf)
                writers[c.index].write(up)
        for c in chunks:
            if c.own1 - 1 == g and c.index in writers:
                writers.pop(c.index).close()
        for _ in range(stride - 1):  # skip to the next sample
            cap.grab()
    cap.release()
    for w in writers.values():
        w.close()
    for c in chunks:  # verify what ffmpeg actually wrote
        got = count_frames(Path(c.path))
        if got != c.n_samples:
            raise RuntimeError(f"chunk {c.index}: wrote {got} frames, expected {c.n_samples}")
    done.write_text(json.dumps({"src": str(src), "start": start, "stop": stop, "stride": stride, "scale": scale,
                                "fps": fps, "n_samples": n, "overlap_s": overlap_s, "chunk_s": chunk_s,
                                "chunks": [asdict(c) for c in chunks]}, indent=1))
    return chunks
