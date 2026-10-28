"""Phase-1 spike: SAM 3.1 video tracking of two mice on CalMS21 clips.

For each clip and phrase: run SAM through the gateway, render an overlay video, and measure
identity against the CalMS21 pose keypoints (MARS tracker; mouse 0 = black resident, 1 = white
intruder). This is a spike script, not product code; it reads labels only for validation.

Usage: uv run python scripts/spike_sam.py data/spike/clipA_contact.mp4 [phrases...]
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from etho.gateway import Gateway
from etho.tracking.sam_parse import parse_line

COLORS = [(0, 0, 255), (0, 255, 0), (255, 128, 0), (255, 0, 255), (0, 255, 255), (255, 255, 0)]


def read_frames(path: Path) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    return frames


def write_video(frames: list[np.ndarray], out: Path, fps: int = 10) -> None:
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          "-crf", "23", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()


def run(clip: Path, phrases: list[str], gw: Gateway) -> dict:
    meta = json.loads(clip.with_suffix(".json").read_text())
    kp_all = np.load(f"data/calms21/seq/{meta['split']}/{meta['source']}.npz")["keypoints"].astype(np.float32)
    frames = read_frames(clip)
    n = len(frames)
    src_idx = meta["start_frame"] + meta["stride"] * np.arange(n)
    kp = kp_all[src_idx]  # n,2(mouse),2(xy),7
    kp_c = kp.mean(axis=3)  # n,2,2 centroid
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    report = {"clip": clip.name, "source": meta, "frames": n, "phrases": {}}
    overlay = [f.copy() for f in frames]
    for pi, phrase in enumerate(phrases):
        res = gw.sam(phrase, tag="dev", video=clip, n_frames=n, public_data=True,
                     caller="scripts/spike_sam.py")
        per_frame: dict[int, list] = defaultdict(list)
        for line in res.lines():
            for of, m in parse_line(line, want_mask=True):
                x0, t, _r, _b = of.box
                g = gray[of.frame][t:t + m.shape[0], x0:x0 + m.shape[1]]
                mm = m[: g.shape[0], : g.shape[1]].astype(bool)
                of.mean_intensity = float(g[mm].mean()) if mm.any() else float("nan")
                per_frame[of.frame].append(of)
                # overlay: tint mask, draw id
                color = COLORS[(int(of.object_id) + 3 * pi) % len(COLORS)]
                roi = overlay[of.frame][t:t + mm.shape[0], x0:x0 + mm.shape[1]]
                roi[mm] = (0.55 * roi[mm] + 0.45 * np.array(color)).astype(np.uint8)
                cv2.putText(overlay[of.frame], f"{phrase}:{of.object_id}", (int(of.cx), int(of.cy)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        # ---- metrics ----
        ids = sorted({o.object_id for v in per_frame.values() for o in v}, key=int)
        counts = np.array([len(per_frame.get(i, [])) for i in range(n)])
        # For each frame and SAM object, which CalMS21 mouse is nearest (by centroid)?
        nearest = defaultdict(list)  # object_id -> list of (frame, mouse_idx, dist)
        for i in range(n):
            for o in per_frame.get(i, []):
                d = np.linalg.norm(kp_c[i] - np.array([o.cx, o.cy]), axis=1)
                nearest[o.object_id].append((i, int(d.argmin()), float(d.min()), float(d.max())))
        id_stats = {}
        for oid, rows in nearest.items():
            mice = np.array([r[1] for r in rows])
            maj = int(np.bincount(mice, minlength=2).argmax())
            # flips: frames where nearest mouse differs from majority while the two mice are separated
            sep = np.array([r[3] > 60 for r in rows])
            flips = int(((mice != maj) & sep).sum())
            inten = [o.mean_intensity for i in range(n) for o in per_frame.get(i, []) if o.object_id == oid]
            id_stats[oid] = {"frames": len(rows), "majority_mouse": ["resident(black)", "intruder(white)"][maj],
                             "frames_matching_majority": int((mice == maj).sum()),
                             "mismatch_while_separated": flips, "separated_frames": int(sep.sum()),
                             "median_dist_px": round(float(np.median([r[2] for r in rows])), 1),
                             "mean_intensity": round(float(np.nanmean(inten)), 1),
                             "median_area": int(np.median([o.area for i in range(n) for o in per_frame.get(i, [])
                                                           if o.object_id == oid]))}
        report["phrases"][phrase] = {
            "cost_usd": res.cost_usd, "frames_billed": res.frames_billed, "latency_s": round(res.latency_s, 1),
            "usage": res.usage, "object_ids": ids, "frames_with_any": int((counts > 0).sum()),
            "objects_per_frame_hist": dict(zip(*(a.tolist() for a in np.unique(counts, return_counts=True)),
                                               strict=True)),
            "ids": id_stats,
        }
    # keypoint centroids for reference (white dot resident, black dot intruder)
    for i in range(n):
        for mi, col in ((0, (255, 255, 255)), (1, (0, 0, 0))):
            cv2.circle(overlay[i], (int(kp_c[i, mi, 0]), int(kp_c[i, mi, 1])), 5, col, -1)
        cv2.putText(overlay[i], f"f{i} src{src_idx[i]}", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    out_dir = Path("data/spike/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = clip.stem + "__" + "+".join(p.replace(" ", "_") for p in phrases)
    write_video(overlay, out_dir / f"{tag}.mp4")
    # contact sheet: the 8 frames with the smallest inter-mouse distance (hardest for identity)
    dist = np.linalg.norm(kp_c[:, 0] - kp_c[:, 1], axis=1)
    pick = sorted(np.argsort(dist)[:: max(1, n // 40)][:8]) if n > 8 else range(n)
    pick = sorted(np.argsort(dist)[:8])
    thumbs = [cv2.resize(overlay[i], (512, 285)) for i in pick]
    rows = [np.hstack(thumbs[k:k + 2]) for k in range(0, len(thumbs), 2)]
    cv2.imwrite(str(out_dir / f"{tag}__closest.jpg"), np.vstack(rows))
    report["closest_frames"] = [int(i) for i in pick]
    (out_dir / f"{tag}.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    clip = Path(sys.argv[1])
    phrases = sys.argv[2:] or ["black mouse", "white mouse"]
    print(json.dumps(run(clip, phrases, Gateway()), indent=1))
