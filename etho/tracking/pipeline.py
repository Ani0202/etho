"""Video -> per-sample animal features, as fast as the SAM API allows.

1. One ffmpeg pass cuts the source into 10 fps, 60 s H.264 chunks (keyframe-aligned).
2. Chunks go to SAM 3.1 in parallel (one "mouse"-style concept call each, one_bit masks).
3. Each streamed frame line is decoded immediately (numba decoder, GIL released), reduced to
   per-object geometry and pairwise distances, and the masks are dropped (box-local RLE kept).
4. Identities are assigned per chunk (ADR-001), so chunk boundaries need no stitching beyond
   the identity rule itself.
5. Output: a per-sample feature table (features.FEATURE_DOC) plus an eval-schema tracks file.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from pycocotools import mask as mask_utils

from etho.gateway import Gateway

from . import features as F
from .identity import IdentityResult, assign_identities, role_at
from .sam_parse import parse_line

ROLES = ("r", "i")  # resident, intruder (dark, bright)


@dataclass
class Chunk:
    index: int
    path: Path
    start_sample: int
    n_samples: int


@dataclass
class TrackRun:
    table: dict[str, np.ndarray]
    n_samples: int
    sample_fps: float
    src_stride: int | None
    chunks: list[dict] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    cost_usd: float = 0.0


def count_frames(p: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_packets", "-of", "csv=p=0", str(p)], capture_output=True, text=True,
                         check=True).stdout
    return int(out.strip())


def prepare_chunks(src: Path, out_dir: Path, *, stride: int | None = 3, fps: int = 10,
                   chunk_s: int = 60) -> list[Chunk]:
    """Cut `src` into `fps` chunks of `chunk_s` seconds in one ffmpeg pass. `stride` keeps every
    stride-th source frame exactly (CalMS21: 30 fps / 3, so sample k = source frame 3k); with
    stride=None the fps filter resamples by time (uploads with unknown frame rates)."""
    ident = f"{src.resolve()}|{src.stat().st_size}|{stride}|{fps}|{chunk_s}"
    key = hashlib.sha256(ident.encode()).hexdigest()[:16]
    d = out_dir / key
    done = d / "chunks.json"
    if done.exists():
        return [Chunk(c["index"], Path(c["path"]), c["start_sample"], c["n_samples"])
                for c in json.loads(done.read_text())]
    d.mkdir(parents=True, exist_ok=True)
    vf = (f"select=not(mod(n\\,{stride})),setpts=N/{fps}/TB" if stride else f"fps={fps}")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vf", vf, "-r", str(fps), "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-force_key_frames", f"expr:gte(t,n_forced*{chunk_s})", "-f", "segment",
                    "-segment_time", str(chunk_s), "-reset_timestamps", "1",
                    "-segment_format_options", "movflags=+faststart", str(d / "c%03d.mp4")], check=True)
    chunks, start = [], 0
    for i, p in enumerate(sorted(d.glob("c*.mp4"))):
        n = count_frames(p)
        chunks.append(Chunk(i, p, start, n))
        start += n
    done.write_text(json.dumps([{"index": c.index, "path": str(c.path), "start_sample": c.start_sample,
                                 "n_samples": c.n_samples} for c in chunks]))
    return chunks


class FrameReader:
    """Sequential grayscale reader; SAM lines arrive in ascending frame order."""

    def __init__(self, path: Path):
        self.cap = cv2.VideoCapture(str(path))
        self.i, self.cur = -1, None

    def get(self, k: int) -> np.ndarray | None:
        while self.i < k:
            ok, f = self.cap.read()
            if not ok:
                return None
            self.i, self.cur = self.i + 1, cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        return self.cur if self.i == k else None


def _rle(m: np.ndarray) -> dict:
    r = mask_utils.encode(np.asfortranarray(m.astype(np.uint8)))
    return {"size": r["size"], "counts": r["counts"].decode()}


def track_chunk(gw: Gateway, chunk: Chunk, phrase: str, tag: str, public_data: bool, raw_out: Path) -> dict:
    """SAM on one chunk, decoding as the stream arrives. Returns objects + pairwise rows."""
    reader = FrameReader(chunk.path)
    objs, pairs = [], []
    t_decode = 0.0
    raw = gzip.open(raw_out, "wt")

    def on_line(line: str) -> None:
        nonlocal t_decode
        t0 = time.perf_counter()
        recs = parse_line(line, want_mask=True)
        if not recs:
            return
        k = recs[0][0].frame
        gray = reader.get(k)
        frame_objs = []
        for of, m in recs:
            left, top = of.box[0], of.box[1]
            mm = m.astype(bool)
            inten = float("nan")
            if gray is not None:
                g = gray[top:top + m.shape[0], left:left + m.shape[1]]
                sub = mm[:g.shape[0], :g.shape[1]]
                inten = float(g[sub].mean()) if sub.any() else float("nan")
            hx, hy = F.head_point(m, left, top, of.cx, of.cy, of.angle)
            o = {"frame": chunk.start_sample + k, "track": f"c{chunk.index}:{of.object_id}", "box": of.box,
                 "area": of.area, "cx": of.cx, "cy": of.cy, "angle": of.angle, "elong": of.elongation,
                 "intensity": inten, "head_x": hx, "head_y": hy}
            frame_objs.append((o, m))
        for a, (oa, ma) in enumerate(frame_objs):
            for b, (ob, mb) in enumerate(frame_objs):
                if a == b:
                    continue
                row = {"frame": oa["frame"], "a": oa["track"], "b": ob["track"],
                       "a_head_to_b": F.point_to_mask(oa["head_x"], oa["head_y"], mb, ob["box"][0], ob["box"][1]),
                       "a_center_to_b": F.point_to_mask(oa["cx"], oa["cy"], mb, ob["box"][0], ob["box"][1])}
                if a < b:
                    row["gap"] = F.mask_gap(ma, oa["box"][0], oa["box"][1], mb, ob["box"][0], ob["box"][1])
                pairs.append(row)
        for o, m in frame_objs:
            objs.append(o)
            raw.write(json.dumps({**o, "rle": _rle(m)}) + "\n")
        t_decode += time.perf_counter() - t0

    t0 = time.monotonic()
    res = gw.sam(phrase, tag=tag, video=chunk.path, n_frames=chunk.n_samples, public_data=public_data,
                 metadata={"mask_encoding": "one_bit"}, on_line=on_line, caller="etho.tracking.pipeline")
    raw.close()
    return {"chunk": chunk.index, "objs": objs, "pairs": pairs, "wall_s": time.monotonic() - t0,
            "decode_s": t_decode, "cost_usd": res.cost_usd, "cache_hit": res.cache_hit,
            "frames_billed": res.frames_billed}


def assemble(chunks: list[Chunk], results: list[dict], n: int, fps: float) -> tuple[dict, list[dict]]:
    """Per-sample feature table from per-chunk objects, with identities per chunk."""
    cols = ["present", "cx", "cy", "area", "angle", "elong", "intensity", "head_x", "head_y"]
    t = {f"{r}_{c}": np.full(n, np.nan) for r in ROLES for c in cols}
    for r in ROLES:
        t[f"{r}_present"][:] = 0
    for c in ("dist", "gap", "r_head_to_i", "i_head_to_r", "n_objects"):
        t[c] = np.full(n, np.nan)
    info = []
    for ch, res in zip(chunks, results, strict=True):
        ident: IdentityResult = assign_identities(res["objs"], ch.n_samples)
        info.append({"chunk": ch.index, "identity_by": ident.identity_by, "midpoint": ident.midpoint,
                     "tracks": [vars(s) for s in ident.summaries], "relabeled": ident.relabeled})
        by_frame: dict[int, list[dict]] = {}
        for o in res["objs"]:
            by_frame.setdefault(o["frame"], []).append(o)
        pair = {(p["frame"], p["a"], p["b"]): p for p in res["pairs"]}
        for f, os_ in by_frame.items():
            t["n_objects"][f] = len(os_)
            who = {}
            parts = {}
            for o in os_:
                role = role_at(ident, o["track"], f - ch.start_sample)
                if role == "resident":
                    who["r"] = o
                elif role == "intruder":
                    who["i"] = o
                elif role.endswith(":part"):
                    parts["r" if role.startswith("resident") else "i"] = o
            for r, o in who.items():
                t[f"{r}_present"][f] = 1
                for c in cols[1:]:
                    t[f"{r}_{c}"][f] = o[c]
                if r in parts:  # an attached part (e.g. the head implant) marks the head
                    t[f"{r}_head_x"][f], t[f"{r}_head_y"][f] = parts[r]["cx"], parts[r]["cy"]
            if "r" in who and "i" in who:
                a, b = who["r"]["track"], who["i"]["track"]
                t["dist"][f] = float(np.hypot(who["r"]["cx"] - who["i"]["cx"], who["r"]["cy"] - who["i"]["cy"]))
                g = pair.get((f, a, b), {}).get("gap")
                g = pair.get((f, b, a), {}).get("gap") if g is None else g
                t["gap"][f] = g if g is not None else np.nan
                if "r" in parts:
                    t["r_head_to_i"][f] = pair.get((f, parts["r"]["track"], b), {}).get("a_center_to_b", np.nan)
                else:
                    t["r_head_to_i"][f] = pair.get((f, a, b), {}).get("a_head_to_b", np.nan)
                t["i_head_to_r"][f] = pair.get((f, b, a), {}).get("a_head_to_b", np.nan)
    F.finalize(t, fps)
    return t, info


def track_video(src: Path, out_dir: Path, *, gw: Gateway | None = None, phrase: str = "mouse",
                tag: str = "eval-sam", public_data: bool = False, stride: int | None = 3, fps: int = 10,
                chunk_s: int = 60, workers: int = 8, chunk_dir: Path = Path("cache/chunks")) -> TrackRun:
    gw = gw or Gateway()
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    chunks = prepare_chunks(src, chunk_dir, stride=stride, fps=fps, chunk_s=chunk_s)
    t_prep = time.monotonic() - t0
    n = sum(c.n_samples for c in chunks)
    with ThreadPoolExecutor(max(1, workers)) as ex:
        results = list(ex.map(lambda c: track_chunk(gw, c, phrase, tag, public_data,
                                                    out_dir / f"raw_c{c.index:03d}.jsonl.gz"), chunks))
    t_sam = time.monotonic() - t0 - t_prep
    t1 = time.monotonic()
    table, info = assemble(chunks, results, n, fps)
    t_asm = time.monotonic() - t1
    wall = time.monotonic() - t0
    run = TrackRun(table, n, fps, stride, info,
                   {"prep_s": t_prep, "sam_and_decode_s": t_sam, "assemble_s": t_asm, "wall_s": wall,
                    "video_s": n / fps, "x_realtime": wall / (n / fps),
                    "decode_cpu_s": sum(r["decode_s"] for r in results),
                    "max_chunk_wall_s": max(r["wall_s"] for r in results)},
                   sum(r["cost_usd"] for r in results))
    np.savez_compressed(out_dir / "features.npz", **table)
    (out_dir / "tracking.json").write_text(json.dumps(
        {"src": str(src), "n_samples": n, "sample_fps": fps, "src_stride": stride, "phrase": phrase,
         "timings": run.timings, "cost_usd": run.cost_usd, "chunks": info,
         "cache_hits": sum(r["cache_hit"] for r in results)}, indent=1, default=float))
    if stride:  # eval-schema tracks (30 fps frame index) for eval/identity.py
        rows = {k: [] for k in ("frame", "track_id", "label", "cx", "cy", "area", "present")}
        for r, lab in (("r", "resident"), ("i", "intruder")):
            pres = table[f"{r}_present"] > 0
            for k in np.nonzero(pres)[0]:
                rows["frame"].append(int(k) * stride)
                rows["track_id"].append(0 if lab == "resident" else 1)
                rows["label"].append(lab)
                rows["cx"].append(table[f"{r}_cx"][k])
                rows["cy"].append(table[f"{r}_cy"][k])
                rows["area"].append(table[f"{r}_area"][k])
                rows["present"].append(1)
        np.savez_compressed(out_dir / "tracks.npz", **{k: np.asarray(v) for k, v in rows.items()})
    return run
