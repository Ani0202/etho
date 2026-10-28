"""Tier-1 adjudication: Muse Spark Open (Fireworks, low effort) judges frame strips.

Candidate windows come from the rules (every predicted bout) and from contact episodes the rules
left unlabeled. Long candidates are split into <= WINDOW_S windows. For each window the model
picks one of the behaviors (or none) from the dataset authors' definitions, with a confidence.
Confident verdicts override the rule label for that window; unsure ones keep the rule label
(tier 2 would take those).
"""

from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from etho.gateway import Gateway
from etho.tracking.pipeline import prepare_chunks

from .strips import load_masks, read_samples, render_strip

FW_MODEL = "accounts/fireworks/models/muse-spark-open"
WINDOW_S = 3.0
FRAMES_PER_STRIP = 6
CONFIDENT = 0.6

PROMPT = """You are scoring mouse social behavior in a resident-intruder assay (top-down camera, grayscale).
The RESIDENT is the black mouse, outlined in RED (it wears a head-mounted implant with a cable).
The INTRUDER is the white mouse, outlined in GREEN. The {n} tiles show frames from t={t0:.1f}s to t={t1:.1f}s,
in reading order (left to right, top to bottom).

Behavior definitions (from the dataset authors; all are performed BY THE RESIDENT toward the intruder):
{defs}

Which ONE label best describes what the resident is doing during most of this interval?
Labels: {labels}, none.
Answer with ONLY a JSON object: {{"label": "<one label>", "confidence": <0 to 1>, "reason": "<one short sentence>"}}"""


@dataclass
class Window:
    seq: str
    s0: int  # first sample (10 fps index), inclusive
    s1: int  # end sample, exclusive
    rule_label: str
    source: str  # "rule" | "contact"


def bouts(mask: np.ndarray) -> list[tuple[int, int]]:
    m = np.r_[0, mask.astype(int), 0]
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist(), strict=True))


def make_windows(seq: str, labels: np.ndarray, table: dict[str, np.ndarray], codes: dict[str, int],
                 fps: float, contact_gap_px: float = 15.0, min_s: float = 0.3) -> list[Window]:
    inv = {v: k for k, v in codes.items()}
    out = []
    step = int(round(WINDOW_S * fps))
    for name, c in codes.items():
        if name == "other":
            continue
        for a, b in bouts(labels == c):
            for s in range(a, b, step):
                out.append(Window(seq, s, min(b, s + step), name, "rule"))
    gap = np.nan_to_num(table["gap"], nan=1e9)
    cand = (gap < contact_gap_px) & (labels == codes["other"])
    for a, b in bouts(cand):
        if (b - a) / fps < min_s:
            continue
        for s in range(a, b, step):
            out.append(Window(seq, s, min(b, s + step), inv[codes["other"]], "contact"))
    return out


def parse_verdict(text: str, labels: list[str]) -> tuple[str, float, str]:
    """Defensive JSON parse (Muse Spark Open on Baseten has no JSON mode; be safe on Fireworks too)."""
    m = re.findall(r"\{[^{}]*\}", text, re.S)
    for blob in reversed(m):
        try:
            d = json.loads(blob)
        except json.JSONDecodeError:
            continue
        lab = str(d.get("label", "")).strip().lower()
        if lab in labels + ["none"]:
            try:
                conf = float(d.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            return lab, max(0.0, min(1.0, conf)), str(d.get("reason", ""))[:200]
    for lab in labels + ["none"]:
        if re.search(rf"\b{lab}\b", text.lower()):
            return lab, 0.0, "unparsed"
    return "unsure", 0.0, "unparsed"


def adjudicate(run_dir: Path, seq: str, windows: list[Window], definitions: dict, *, gw: Gateway, fps: float = 10.0,
               effort: str = "low", tag: str = "eval-llm", out_dir: Path | None = None,
               concurrency: int = 8) -> list[dict]:
    if not windows:
        return []
    info = json.loads((run_dir / seq / "tracking.json").read_text())
    chunks = [vars(c) | {"path": str(c.path)} for c in
              prepare_chunks(Path(info["src"]), Path("cache/chunks"), stride=info.get("src_stride", 3))]
    samples_per = {}
    for w in windows:
        idx = np.linspace(w.s0, max(w.s0, w.s1 - 1), FRAMES_PER_STRIP).round().astype(int).tolist()
        samples_per[(w.s0, w.s1, w.source)] = idx
    need = sorted({s for v in samples_per.values() for s in v})
    frames = read_samples(chunks, need)
    masks = load_masks(run_dir / seq, set(need))
    labels = [b for b in definitions["behaviors"]]
    defs = "\n".join(f"- {k}: {v}" for k, v in definitions["behaviors"].items())
    def judge(w: Window) -> dict:
        idx = samples_per[(w.s0, w.s1, w.source)]
        img = render_strip(frames, masks, idx, fps)
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{seq}_{w.s0}_{w.s1}.jpg").write_bytes(jpg.tobytes())
        url = "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()
        prompt = PROMPT.format(n=len(idx), t0=w.s0 / fps, t1=w.s1 / fps, defs=defs, labels=", ".join(labels))
        msgs = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                             {"type": "image_url", "image_url": {"url": url}}]}]
        res = gw.chat("fireworks", FW_MODEL, msgs, effort=effort, tag=tag, max_tokens=2048,
                      caller="etho.adjudication.tier1")
        lab, conf, reason = parse_verdict(res.text, labels)
        return {"seq": seq, "s0": w.s0, "s1": w.s1, "rule_label": w.rule_label, "source": w.source,
                "label": lab, "confidence": conf, "reason": reason, "cost_usd": res.cost_usd,
                "cache_hit": res.cache_hit, "latency_s": res.latency_s,
                "in_tok": res.usage.get("input_tokens"), "out_tok": res.usage.get("output_tokens")}

    with ThreadPoolExecutor(max(1, concurrency)) as ex:
        return list(ex.map(judge, windows))


# Dev finding (R1): at low effort tier 1's "attack"/"mount" verdicts were right only ~21-26% of the time,
# its "investigation" 58% and "none" 85%. So by default it may confirm investigation or reject a window,
# never promote one to attack/mount (DECISIONS.md, 2026-10-28).
DEFAULT_ALLOW = frozenset({"investigation", "none"})


def apply(labels: np.ndarray, verdicts: list[dict], codes: dict[str, int], confident: float = CONFIDENT,
          allow: frozenset[str] = DEFAULT_ALLOW) -> np.ndarray:
    out = labels.copy()
    for v in verdicts:
        if v["label"] in ("unsure",) or v["confidence"] < confident or v["label"] not in allow:
            continue
        code = codes["other"] if v["label"] == "none" else codes[v["label"]]
        out[v["s0"]:v["s1"]] = code
    return out
