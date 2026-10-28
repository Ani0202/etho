"""Tier-2 adjudication: short video clips judged by Muse Spark Open on Baseten (effort high).

Escalated windows are those where the rules and tier 1 disagree (tier-1 confidence turned out to be
uninformative on dev, so "unsure" alone is not a usable trigger). Each clip is the window, cropped
around both animals, with the same resident-red / intruder-green outlines as the strips, at 10 fps.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from etho.gateway import Gateway
from etho.tracking.pipeline import prepare_chunks

from .strips import COLORS, load_masks, read_samples
from .tier1 import parse_verdict

BT_MODEL = "meta-models/muse-spark-open-1026"
BT_CONCURRENCY = 4
_BT_SLOTS = threading.BoundedSemaphore(BT_CONCURRENCY)

PROMPT = """You are scoring mouse social behavior in a resident-intruder assay (top-down camera, grayscale).
The RESIDENT is the black mouse, outlined in RED (it wears a head-mounted implant with a cable).
The INTRUDER is the white mouse, outlined in GREEN. The clip covers t={t0:.1f}s to t={t1:.1f}s at 10 fps.

Behavior definitions (from the dataset authors; all are performed BY THE RESIDENT toward the intruder):
{defs}

Which ONE label best describes what the resident is doing during most of this clip?
Labels: {labels}, none.
Answer with ONLY a JSON object: {{"label": "<one label>", "confidence": <0 to 1>, "reason": "<one short sentence>"}}"""


def render_clip(frames: dict[int, np.ndarray], masks: dict[int, list[dict]], samples: list[int], out: Path,
                height: int = 360) -> Path:
    boxes = [m["box"] for s in samples for m in masks.get(s, [])]
    H, W = next(iter(frames.values())).shape[:2]
    if boxes:
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad = int(0.15 * max(x1 - x0, y1 - y0)) + 20
        x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    else:
        x0, y0, x1, y1 = 0, 0, W, H
    scale = height / (y1 - y0)
    w = int((x1 - x0) * scale) // 2 * 2
    h = height // 2 * 2
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s",
                          f"{w}x{h}", "-r", "10", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                          "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    for s in samples:
        img = frames.get(s)
        img = np.zeros((H, W, 3), np.uint8) if img is None else img.copy()
        for m in masks.get(s, []):
            full = np.zeros((H, W), np.uint8)
            bl, bt = m["box"][0], m["box"][1]
            mh, mw = m["mask"].shape
            full[bt:bt + mh, bl:bl + mw] = m["mask"][: H - bt, : W - bl]
            cs, _ = cv2.findContours(full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(img, cs, -1, COLORS[m["role"]], 2)
        p.stdin.write(cv2.resize(img[y0:y1, x0:x1], (w, h)).tobytes())
    p.stdin.close()
    p.wait()
    return out


def adjudicate_clips(run_dir: Path, seq: str, windows: list[dict], definitions: dict, *, gw: Gateway,
                     fps: float = 10.0, effort: str = "high", tag: str = "eval-llm") -> list[dict]:
    if not windows:
        return []
    info = json.loads((run_dir / seq / "tracking.json").read_text())
    chunks = [vars(c) | {"path": str(c.path)} for c in
              prepare_chunks(Path(info["src"]), Path("cache/chunks"), stride=info.get("src_stride", 3))]
    need = sorted({s for w in windows for s in range(w["s0"], w["s1"])})
    frames = read_samples(chunks, need)
    masks = load_masks(run_dir / seq, set(need))
    labels = list(definitions["behaviors"])
    defs = "\n".join(f"- {k}: {v}" for k, v in definitions["behaviors"].items())
    clip_dir = run_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)

    def judge(w: dict) -> dict:
        clip = render_clip(frames, masks, list(range(w["s0"], w["s1"])), clip_dir / f"{seq}_{w['s0']}_{w['s1']}.mp4")
        url = "data:video/mp4;base64," + base64.b64encode(clip.read_bytes()).decode()
        prompt = PROMPT.format(t0=w["s0"] / fps, t1=w["s1"] / fps, defs=defs, labels=", ".join(labels))
        msgs = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                             {"type": "video_url", "video_url": {"url": url}}]}]
        with _BT_SLOTS:
            res = gw.chat("baseten", BT_MODEL, msgs, effort=effort, tag=tag, max_tokens=4096, public_data=True,
                          caller="etho.adjudication.tier2")
        lab, conf, reason = parse_verdict(res.text, labels)
        return {**{k: w[k] for k in ("seq", "s0", "s1", "rule_label")}, "tier1_label": w["label"],
                "label": lab, "confidence": conf, "reason": reason, "cost_usd": res.cost_usd,
                "cache_hit": res.cache_hit, "latency_s": res.latency_s,
                "in_tok": res.usage.get("input_tokens"), "out_tok": res.usage.get("output_tokens")}

    with ThreadPoolExecutor(BT_CONCURRENCY) as ex:
        return list(ex.map(judge, windows))


def disagreements(verdicts: list[dict]) -> list[dict]:
    """Windows where tier 1's label differs from the rule's (tier-1 'none' vs rule 'other' agree)."""
    out = []
    for v in verdicts:
        t1 = "other" if v["label"] == "none" else v["label"]
        if t1 != v["rule_label"] and v["label"] != "unsure":
            out.append(v)
    return out


__all__ = ["adjudicate_clips", "disagreements", "render_clip", tempfile.__name__]
