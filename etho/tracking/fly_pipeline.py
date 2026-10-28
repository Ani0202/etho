"""Fly vs. Fly (ADR-002): movie window -> SAM 3.1 video tracks -> two-fly feature table.

  1. fly_video.prepare_fly_chunks cuts [start, stop) at `stride` into overlapping 60 s chunks.
  2. Chunks go to SAM in parallel (phrase "fruit fly": plain "fly" returned nothing, SPIKE in
     docs/DECISIONS.md). Each streamed line is decoded at once and reduced to per-object geometry
     (source pixels); masks are dropped, box-local RLE goes to raw_c###.jsonl.gz.
  3. fly_identity: SAM ids inside a chunk, relabeling after contact runs that exchanged the flies,
     Hungarian stitching across chunk overlaps, identity `unknown` whenever the flies are within one
     body length.
  4. fly_features: per-fly, pair and pair-level symmetric features (FLY_FEATURE_DOC).

`rederive_fly_run` redoes steps 3-4 (and the geometry of step 2) from the raw RLE on disk, with no
SAM call and no cost.

Product code: only the .mp4 is read. No ground-truth annotation or tracking files.
"""

from __future__ import annotations

import gzip
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
from pycocotools import mask as mask_utils

from etho.gateway import Gateway, GatewayConfig
from etho.gateway.prices import SAM_MIN_VIDEO_FRAMES, SAM_VIDEO_PER_FRAME

from . import features as F
from .fly_features import PER_FLY_RAW, finalize_fly
from .fly_identity import (
    Traj,
    body_length,
    major_len,
    relabel_swaps,
    slot_chunk,
    stitch,
    suspect_swaps,
    traj_from_rows,
    unknown_mask,
)
from .fly_video import FlyChunk, plan_chunks, prepare_fly_chunks
from .sam_parse import mask_moments, parse_line

PHRASE = "fruit fly"  # "fly" returned 0 detections on train movie 1 (50-sample probe, native and 4x)
BODY_KERNEL = 3      # px: opening that removes thin spurs (wings, legs) before the body centroid


def make_gateway(root: Path) -> Gateway:
    """A gateway whose ledger, caps and cache live under `root` (the shared checkout), never a worktree's own."""
    root = Path(root).resolve()
    if not (root / "ops").is_dir():
        raise RuntimeError(f"{root} has no ops/ directory: set ETHO_ROOT to the shared checkout")
    gw = Gateway(GatewayConfig(root=root))
    assert gw.cfg.root.resolve() == root and gw.cfg.ledger_path.parent == root / "ops"
    return gw


def estimate_sam_cost(start: int, stop: int, stride: int, *, chunk_s: float = 60.0, overlap_s: float = 1.0,
                      src_fps: float = 30.0) -> tuple[int, float]:
    """(frames billed, USD) for one movie window, upper bound (a cached chunk costs nothing)."""
    n = -(-(stop - start) // stride)
    frames = sum(max(own1 - first, SAM_MIN_VIDEO_FRAMES)
                 for first, _, own1 in plan_chunks(n, src_fps / stride, chunk_s, overlap_s))
    return frames, frames * SAM_VIDEO_PER_FRAME


def _rle(m: np.ndarray) -> dict:
    r = mask_utils.encode(np.asfortranarray(m.astype(np.uint8)))
    return {"size": r["size"], "counts": r["counts"].decode()}


def solidity(m: np.ndarray) -> float:
    """contour area / convex-hull area of a box-local binary mask (<= 1)."""
    cs, _ = cv2.findContours(np.pad(m.astype(np.uint8), 1), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return float("nan")
    c = max(cs, key=cv2.contourArea)
    ha = cv2.contourArea(cv2.convexHull(c))
    return float(min(1.0, cv2.contourArea(c) / ha)) if ha > 0 else float("nan")


def opened(m: np.ndarray) -> np.ndarray:
    return cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((BODY_KERNEL, BODY_KERNEL), np.uint8))


def thin_features(m: np.ndarray, cx_local: float, cy_local: float, major: float) -> tuple[float, float]:
    """(thin_frac, reach_ratio) of a box-local mask. thin_frac = share of mask pixels that vanish under a
    3x3 opening (thin spurs such as an extended wing or leg); reach_ratio = farthest mask pixel from the
    centroid / half the major-axis length (~1 for a compact body, larger with a spur)."""
    mm = m.astype(np.uint8)
    n = int(mm.sum())
    if n == 0:
        return float("nan"), float("nan")
    ys, xs = np.nonzero(mm)
    reach = float(np.sqrt(((xs - cx_local) ** 2 + (ys - cy_local) ** 2).max()))
    return 1.0 - float(opened(mm).sum()) / n, reach / (major / 2.0) if major > 0 else float("nan")


def body_centroid(m: np.ndarray, left: int, top: int) -> tuple[float, float]:
    """Centroid (chunk px) of the 3x3-opened mask; the whole mask if the opening erases it."""
    o = opened(m)
    ys, xs = np.nonzero(o if o.any() else m)
    if len(xs) == 0:
        return float("nan"), float("nan")
    return float(xs.mean() + left), float(ys.mean() + top)


def object_record(chunk: FlyChunk, local: int, object_id: str, box: tuple[int, int, int, int], m: np.ndarray,
                  conf: float | None = None) -> dict:
    """Geometry of one SAM object in SOURCE pixels (chunk pixels / scale), from its box-local mask alone."""
    s = float(chunk.scale)
    area_c, cx, cy, ang, el = mask_moments(m, box[0], box[1])
    area = area_c / s ** 2
    mj = major_len(area, el)
    mn = 2.0 * np.sqrt(area / (np.pi * el)) if np.isfinite(el) and el > 0 else np.nan
    thin, reach = thin_features(m, cx - box[0], cy - box[1], mj * s)
    bx, by = body_centroid(m, box[0], box[1])
    return {"g": chunk.first + local, "src_frame": chunk.src_frame(local), "track": f"c{chunk.index}:{object_id}",
            "box": tuple(int(v) for v in box), "area": area, "cx": cx / s, "cy": cy / s, "bx": bx / s, "by": by / s,
            "angle": ang, "elong": el, "major_len": mj, "minor_len": float(mn), "solidity": solidity(m),
            "thin_frac": thin, "reach_ratio": reach, "conf": conf}


def _pair_gaps(frame_objs: list[tuple[dict, np.ndarray]], scale: int) -> list[dict]:
    out = []
    for a in range(len(frame_objs)):
        for b in range(a + 1, len(frame_objs)):
            (oa, ma), (ob, mb) = frame_objs[a], frame_objs[b]
            gap = F.mask_gap(ma, oa["box"][0], oa["box"][1], mb, ob["box"][0], ob["box"][1])
            out.append({"g": oa["g"], "a": oa["track"], "b": ob["track"], "gap": gap / scale})
    return out


def track_fly_chunk(gw: Gateway, chunk: FlyChunk, phrase: str, tag: str, public_data: bool, raw_out: Path) -> dict:
    objs, pairs = [], []
    t_decode = 0.0
    with gzip.open(raw_out, "wt") as raw:  # closed on any exception, including a failed SAM call

        def on_line(line: str) -> None:
            nonlocal t_decode
            t0 = time.perf_counter()
            recs = parse_line(line, want_mask=True)
            if not recs:
                return
            local = recs[0][0].frame
            fr = [(object_record(chunk, local, of.object_id, of.box, m, of.confidence), m) for of, m in recs]
            pairs.extend(_pair_gaps(fr, chunk.scale))
            for o, m in fr:
                objs.append(o)
                raw.write(json.dumps({**o, "rle": _rle(m)}) + "\n")
            t_decode += time.perf_counter() - t0

        t0 = time.monotonic()
        res = gw.sam(phrase, tag=tag, video=Path(chunk.path), n_frames=chunk.n_samples, public_data=public_data,
                     metadata={"mask_encoding": "one_bit"}, on_line=on_line, caller="etho.tracking.fly_pipeline")
    return {"chunk": chunk.index, "objs": objs, "pairs": pairs, "wall_s": time.monotonic() - t0,
            "decode_s": t_decode, "cost_usd": res.cost_usd, "cache_hit": res.cache_hit,
            "frames_billed": res.frames_billed}


def results_from_raw(chunks: list[FlyChunk], run_dir: Path) -> list[dict]:
    """Rebuild per-chunk objects and pair gaps from raw_c###.jsonl.gz (no SAM call)."""
    results = []
    for ch in chunks:
        objs, pairs, frame = [], [], []
        cur = None
        with gzip.open(run_dir / f"raw_c{ch.index:03d}.jsonl.gz", "rt") as f:
            for line in f:
                o = json.loads(line)
                m = mask_utils.decode({"size": o["rle"]["size"], "counts": o["rle"]["counts"].encode()})
                rec = object_record(ch, o["g"] - ch.first, o["track"].split(":", 1)[1], tuple(o["box"]), m, o["conf"])
                if cur is not None and rec["g"] != cur:
                    pairs.extend(_pair_gaps(frame, ch.scale))
                    frame = []
                cur = rec["g"]
                frame.append((rec, m))
                objs.append(rec)
        pairs.extend(_pair_gaps(frame, ch.scale))
        results.append({"chunk": ch.index, "objs": objs, "pairs": pairs})
    return results


def assemble_fly(chunks: list[FlyChunk], results: list[dict], n: int, fps: float, start: int, stride: int
                 ) -> tuple[dict[str, np.ndarray], dict]:
    """Per-sample two-fly table over global samples [0, n) from per-chunk SAM objects."""
    all_objs = [o for r in results for o in r["objs"]]
    bl = body_length(all_objs) if all_objs else float("nan")
    area_med = float(np.median([o["area"] for o in all_objs])) if all_objs else 0.0
    t: dict[str, np.ndarray] = {}
    for p in ("a", "b"):
        for c in PER_FLY_RAW:
            t[f"{p}_{c}"] = np.full(n, np.nan)
        t[f"{p}_present"][:] = 0
    for c in ("gap", "n_objects"):
        t[c] = np.full(n, np.nan)
    t["src_frame"] = start + stride * np.arange(n, dtype=np.int64)
    bump = np.zeros(n + 1)           # +1 at every sample where the A/B labeling is no longer comparable
    stitches, per_chunk, relabels = [], [], []
    glob = [Traj(), Traj()]
    for ch, res in zip(chunks, results, strict=True):
        _, assigned = slot_chunk(res["objs"], ch.first, ch.own1, bl, area_med)
        for g in relabel_swaps(assigned, ch.first, ch.own1, bl):
            if g >= ch.own0:  # inside the overlap the stitch below fixes the labeling anyway
                bump[g] += 1
                relabels.append(g)
        slots = [traj_from_rows(assigned, 0), traj_from_rows(assigned, 1)]
        if ch.index == 0 or not (glob[0].pts or glob[1].pts):
            perm = (0, 1)
        else:
            st = stitch((glob[0], glob[1]), (slots[0], slots[1]), list(range(ch.first, ch.own0)), bl)
            perm = st.perm
            bump[ch.own0] += int(st.ambiguous)
            stitches.append({"chunk": ch.index, "own0": ch.own0, "perm": list(perm), "cost": st.cost,
                             "alt_cost": st.alt_cost, "ambiguous": st.ambiguous, "reason": st.reason,
                             "n_compared": st.n_compared, "n_apart": st.n_apart})
        pair = {}
        for pr in res["pairs"]:
            pair[(pr["g"], pr["a"], pr["b"])] = pr["gap"]
            pair[(pr["g"], pr["b"], pr["a"])] = pr["gap"]
        cnt: dict[int, int] = {}
        for o in res["objs"]:
            cnt[o["g"]] = cnt.get(o["g"], 0) + 1
        for g in range(ch.own0, ch.own1):
            t["n_objects"][g] = cnt.get(g, 0)
            row = assigned.get(g, [None, None])
            objs = [row[perm[0]], row[perm[1]]]
            for k, p in enumerate(("a", "b")):
                o = objs[k]
                if o is None:
                    continue
                t[f"{p}_present"][g] = 1
                for c in PER_FLY_RAW[1:]:
                    t[f"{p}_{c}"][g] = o[c]
                glob[k].add(g, o["cx"], o["cy"], o["area"])
            if objs[0] is not None and objs[1] is not None:
                gp = pair.get((g, objs[0]["track"], objs[1]["track"]))
                t["gap"][g] = gp if gp is not None else np.nan
        per_chunk.append({"chunk": ch.index, "first": ch.first, "own0": ch.own0, "own1": ch.own1,
                          "tracks_bound": sorted({o["track"] for r in assigned.values() for o in r if o}),
                          "samples_with_2": int(sum(1 for g in range(ch.own0, ch.own1)
                                                    if all(x is not None for x in assigned.get(g, [None, None])))),
                          "n_samples_owned": ch.own1 - ch.own0})
    epoch = np.cumsum(bump)[:n]
    t["id_epoch"] = epoch.astype(float)
    for s_ in stitches:
        s_["epoch"] = int(epoch[s_["own0"]])
    a_xy = np.c_[t["a_cx"], t["a_cy"]]
    b_xy = np.c_[t["b_cx"], t["b_cy"]]
    unk = unknown_mask(a_xy, b_xy, bl)
    t["id_unknown"] = unk.astype(float)
    n_runs, n_sus = suspect_swaps(a_xy, b_xy, unk)
    finalize_fly(t, fps, bl)
    both = (t["a_present"] > 0) & (t["b_present"] > 0)
    info = {"body_len_px": bl, "median_area_px2": area_med, "n_epochs": int(epoch.max()) + 1 if n else 1,
            "stitches": stitches, "relabels_at": relabels, "chunks": per_chunk,
            "frac_both_present": float(both.mean()),
            "frac_id_unknown_of_both": float(unk[both].mean()) if both.any() else float("nan"),
            "frac_id_unknown": float(unk.mean()),
            "frac_contact_of_both": float(np.nanmean(t["contact"][both])) if both.any() else float("nan"),
            "frac_vel_ok": float(t["vel_ok"].mean()), "frac_vel_ok_s": float(t["vel_ok_s"].mean()),
            "close_runs": n_runs, "suspect_swaps_left": n_sus,
            "frac_n_objects_gt2": float((t["n_objects"] > 2).mean())}
    return t, info


def write_run(out_dir: Path, table: dict[str, np.ndarray], meta: dict) -> None:
    np.savez_compressed(out_dir / "features.npz", **table)
    (out_dir / "tracking.json").write_text(json.dumps(meta, indent=1, default=float))
    rows = {k: [] for k in ("frame", "track_id", "label", "cx", "cy", "area", "present", "id_unknown", "id_epoch")}
    for k, (p, lab) in enumerate((("a", "fly_a"), ("b", "fly_b"))):
        for g in np.nonzero(table[f"{p}_present"] > 0)[0]:
            rows["frame"].append(int(table["src_frame"][g]))
            rows["track_id"].append(k)
            rows["label"].append(lab)
            rows["cx"].append(table[f"{p}_cx"][g])
            rows["cy"].append(table[f"{p}_cy"][g])
            rows["area"].append(table[f"{p}_area"][g])
            rows["present"].append(1)
            rows["id_unknown"].append(int(table["id_unknown"][g]))
            rows["id_epoch"].append(int(table["id_epoch"][g]))
    np.savez_compressed(out_dir / "tracks.npz", **{k: np.asarray(v) for k, v in rows.items()})


def track_fly_movie(src: Path, out_dir: Path, *, gw: Gateway, public_data: bool, start: int = 22500,
                    stop: int = 31500, stride: int = 1, scale: int = 1, phrase: str = PHRASE, tag: str = "ablation",
                    chunk_s: float = 60.0, overlap_s: float = 1.0, workers: int = 5,
                    chunk_dir: Path = Path("cache/fly_chunks"), src_fps: float = 30.0) -> dict:
    """`gw` and `public_data` are required: the caller decides which ledger pays and whether the data are
    public (contributor-model IDs), never a default."""
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    chunks = prepare_fly_chunks(src, chunk_dir, start=start, stop=stop, stride=stride, scale=scale,
                                chunk_s=chunk_s, overlap_s=overlap_s, src_fps=src_fps)
    t_prep = time.monotonic() - t0
    n = chunks[-1].own1
    fps = src_fps / stride
    with ThreadPoolExecutor(max(1, workers)) as ex:
        results = list(ex.map(lambda c: track_fly_chunk(gw, c, phrase, tag, public_data,
                                                        out_dir / f"raw_c{c.index:03d}.jsonl.gz"), chunks))
    t_sam = time.monotonic() - t0 - t_prep
    t1 = time.monotonic()
    table, info = assemble_fly(chunks, results, n, fps, start, stride)
    t_asm = time.monotonic() - t1
    wall = time.monotonic() - t0
    timings = {"prep_s": t_prep, "sam_and_decode_s": t_sam, "assemble_s": t_asm, "wall_s": wall,
               "video_s": n / fps, "x_realtime": wall / (n / fps), "decode_cpu_s": sum(r["decode_s"] for r in results),
               "max_chunk_wall_s": max(r["wall_s"] for r in results)}
    cost = sum(r["cost_usd"] for r in results)
    meta = {"src": str(src), "phrase": phrase, "window": {"start": start, "stop": stop, "stride": stride,
                                                          "scale": scale},
            "config": {"chunk_s": chunk_s, "overlap_s": overlap_s, "tag": tag, "public_data": public_data},
            "n_samples": n, "sample_fps": fps, "src_fps": src_fps,
            "mapping": "sample g = source frame start + stride*g", "chunk_files": [asdict(c) for c in chunks],
            "timings": timings, "cost_usd": cost, "frames_billed": sum(r["frames_billed"] for r in results),
            "cache_hits": sum(r["cache_hit"] for r in results), "identity": info}
    write_run(out_dir, table, meta)
    return {"table": table, "info": info, "timings": timings, "cost_usd": cost, "n_samples": n, "sample_fps": fps}


def rederive_fly_run(out_dir: Path) -> dict:
    """Recompute features.npz / tracks.npz / tracking.json from the raw RLE already on disk: $0, no SAM.
    Cost, billing and timings of the original SAM run are carried over from the old tracking.json."""
    old = json.loads((out_dir / "tracking.json").read_text())
    chunks = [FlyChunk(**c) for c in old["chunk_files"]]
    w = old["window"]
    results = results_from_raw(chunks, out_dir)
    n, fps = old["n_samples"], old["sample_fps"]
    table, info = assemble_fly(chunks, results, n, fps, w["start"], w["stride"])
    meta = {**old, "identity": info, "rederived_from_raw": True}
    if "config" not in meta:  # runs from before the config was recorded: read it back from the chunk plan
        meta["config"] = {"chunk_s": chunks[0].own1 / fps, "tag": "ablation", "public_data": True,
                          "overlap_s": chunks[1].overlap / fps if len(chunks) > 1 else 0.0}
    write_run(out_dir, table, meta)
    return {"table": table, "info": info}
