"""ADR-002 SAM probe on Fly vs. Fly: does SAM 3.1 *video* find two flies at 144 px, native vs 4x upscale?

TRAIN movie 1 only. 50-sample consecutive windows (the 50-frame billing minimum, ~$0.01 per request).
Reports, per (window, scale): fraction of samples with exactly 2 masks, per-object-id persistence, mask
areas in source pixels, and writes overlay PNGs for a visual wing check.

  uv run python scripts/fly_probe.py --movie 1 --starts 22500 27000 --scales 1 4 --out <dir> [--phrase fly]

ETHO_ROOT (the shared checkout) is required: its ledger pays and its data/ holds the movies.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from etho.tracking.fly_pipeline import PHRASE, make_gateway
from etho.tracking.fly_video import prepare_fly_chunks, upscale
from etho.tracking.sam_parse import parse_line

COLORS = [(0, 0, 255), (0, 200, 0), (255, 128, 0), (255, 0, 255)]


def probe(gw, data: Path, movie: int, start: int, scale: int, phrase: str, out: Path, n: int = 50) -> dict:
    src = data / f"movie{movie}" / f"movie{movie}.mp4"
    chunks = prepare_fly_chunks(src, out / "chunks", start=start, stop=start + n, stride=1, scale=scale,
                                chunk_s=3600, overlap_s=0)
    c = chunks[0]
    per: dict[int, list] = defaultdict(list)
    masks: dict[tuple[int, str], np.ndarray] = {}
    res = gw.sam(phrase, tag="ablation", video=c.path, n_frames=c.n_samples, public_data=True,
                 metadata={"mask_encoding": "one_bit"}, caller="scripts/fly_probe.py")
    for line in res.lines():
        for of, m in parse_line(line, want_mask=True):
            per[of.frame].append(of)
            masks[(of.frame, of.object_id)] = m
    counts = np.array([len(per.get(i, [])) for i in range(c.n_samples)])
    ids = sorted({o.object_id for v in per.values() for o in v}, key=int)
    id_stats = {}
    for oid in ids:
        fr = [i for i in range(c.n_samples) if any(o.object_id == oid for o in per.get(i, []))]
        areas = [o.area for v in per.values() for o in v if o.object_id == oid]
        cxy = np.array([[o.cx, o.cy] for i in fr for o in per[i] if o.object_id == oid])
        jump = np.hypot(*np.diff(cxy, axis=0).T) if len(cxy) > 1 else np.array([0.0])
        id_stats[oid] = {"samples": len(fr), "first": fr[0], "last": fr[-1],
                         "median_area_src_px": float(np.median(areas)) / scale ** 2,
                         "max_step_src_px": float(jump.max()) / scale}
    tag = f"m{movie}_s{start}_x{scale}_{phrase.replace(' ', '_')}"
    rep = {"movie": movie, "start": start, "scale": scale, "phrase": phrase, "n_samples": c.n_samples,
           "cost_usd": res.cost_usd, "frames_billed": res.frames_billed, "latency_s": round(res.latency_s, 1),
           "cache_hit": res.cache_hit, "frac_exactly_2": float((counts == 2).mean()),
           "frac_0": float((counts == 0).mean()), "frac_1": float((counts == 1).mean()),
           "frac_3plus": float((counts >= 3).mean()), "n_ids": len(ids), "ids": id_stats}
    # overlays: raw | mask contour, per sample, upscaled to a common 576 px for viewing
    cap = cv2.VideoCapture(str(src))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    frames = []
    for _ in range(c.n_samples):
        ok, f = cap.read()
        frames.append(f)
    out.mkdir(parents=True, exist_ok=True)
    pick = [0, 12, 25, 38, 49]
    tiles = []
    for i in pick:
        raw = upscale(frames[i], 4)
        ov = raw.copy()
        for o in per.get(i, []):
            m = masks[(i, o.object_id)].astype(np.uint8)
            if scale != 4:  # draw in the 4x frame
                m = cv2.resize(m, None, fx=4 / scale, fy=4 / scale, interpolation=cv2.INTER_NEAREST)
                l_, t_ = int(o.box[0] * 4 / scale), int(o.box[1] * 4 / scale)
            else:
                l_, t_ = o.box[0], o.box[1]
            cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            for cn in cnts:
                cv2.drawContours(ov, [cn + np.array([[l_, t_]])], -1, COLORS[int(o.object_id) % 4], 1)
            cv2.putText(ov, o.object_id, (int(o.cx * 4 / scale), int(o.cy * 4 / scale) - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLORS[int(o.object_id) % 4], 2)
        cv2.putText(ov, f"s{i} n={len(per.get(i, []))}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        tiles.append(np.hstack([raw, ov]))
    cv2.imwrite(str(out / f"{tag}.png"), np.vstack(tiles[:3]))
    cv2.imwrite(str(out / f"{tag}_b.png"), np.vstack(tiles[3:]))
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1))
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--movie", type=int, default=1)
    ap.add_argument("--starts", type=int, nargs="+", default=[22500])
    ap.add_argument("--scales", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--phrase", default=PHRASE)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.movie not in (1, 2, 3, 4, 5):
        raise SystemExit("probe on TRAIN movies only")
    if not os.environ.get("ETHO_ROOT"):
        raise SystemExit("set ETHO_ROOT to the shared checkout")
    root = Path(os.environ["ETHO_ROOT"]).resolve()
    gw = make_gateway(root)
    data = root / "data/flyvsfly/Aggression"
    for s in a.starts:
        for sc in a.scales:
            r = probe(gw, data, a.movie, s, sc, a.phrase, a.out)
            print(json.dumps({k: v for k, v in r.items() if k != "ids"}))
            for oid, st in r["ids"].items():
                print("   id", oid, st)


if __name__ == "__main__":
    main()
