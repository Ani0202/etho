"""Perception planner (architecture stage 1).

1. Muse Spark looks at 4 sample frames and proposes up to 3 short SAM noun phrases, the expected
   number of animals, and how the individuals can be told apart.
2. Each phrase is checked with SAM image calls on 2 frames (does it find the expected count?).
3. Because SAM's image endpoint is more permissive than its video tracking (spike: "black mouse"
   worked on an image but not in video), the best phrase must also pass a 5 s video probe: the
   expected count in >= 90% of sampled frames. Otherwise the next phrase is tried.
Every step is recorded for the audit trail.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from etho.gateway import Gateway, image_part
from etho.tracking.sam_lines import parse_sam_line

PROMPT = """These are 4 frames from a lab video for animal behavior scoring. A segmentation model (SAM 3.1) will
track the animals from a short English noun phrase (one phrase finds all instances of that concept).

Answer with ONLY a JSON object:
{"animals": "<what animals you see>", "expected_count": <number of animals to track>,
 "phrases": ["<best short noun phrase>", "<alternative>", "<alternative>"],
 "identity_cue": "<how individuals can be told apart: e.g. 'coat color: one black, one white', or 'none visible'>",
 "notes": "<anything that may confuse tracking: reflections, cables, occlusion>"}
Prefer generic nouns ("mouse", "fly") over attribute phrases ("black mouse"): attribute phrases often fail in video."""


@dataclass
class Plan:
    phrase: str
    expected_count: int
    identity_cue: str
    identity_method: str  # "intensity" | "persistence"
    steps: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0


def sample_frames(video: Path, n: int = 4) -> list[Path]:
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    d = Path(tempfile.mkdtemp(prefix="etho_plan_"))
    out = []
    for i, frac in enumerate([0.1, 0.35, 0.6, 0.85][:n]):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * frac))
        ok, f = cap.read()
        if ok:
            p = d / f"f{i}.jpg"
            cv2.imwrite(str(p), f)
            out.append(p)
    return out


def parse_json(text: str) -> dict:
    for blob in reversed(re.findall(r"\{.*\}", text, re.S)):
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    return {}


def count_objects(sam_text_lines) -> list[int]:
    return [len(list(parse_sam_line(line, "video"))) for line in sam_text_lines]


def plan(video: Path, *, gw: Gateway | None = None, public_data: bool = False, tag: str = "eval-sam",
         probe_s: float = 5.0) -> Plan:
    gw = gw or Gateway()
    frames = sample_frames(video)
    model = "muse-spark-1.3-contributor" if public_data else "muse-spark-1.3"
    content = [{"type": "text", "text": PROMPT}] + [image_part(p) for p in frames]
    res = gw.chat("meta", model, [{"role": "user", "content": content}], effort="medium", tag="eval-llm",
                  max_tokens=4096, public_data=public_data, caller="etho.perception.planner")
    prop = parse_json(res.text)
    steps = [{"step": "propose", "model": model, "answer": prop, "cost_usd": res.cost_usd}]
    cost = res.cost_usd
    expected = int(prop.get("expected_count") or 2)
    phrases = [p for p in (prop.get("phrases") or []) if isinstance(p, str) and p.strip()][:3] or ["animal"]
    cue = str(prop.get("identity_cue", ""))
    method = "intensity" if re.search(r"colou?r|black|white|dark|light|bright", cue, re.I) else "persistence"
    # Image check: SAM on 2 frames per phrase.
    scored = []
    for rank, ph in enumerate(phrases):
        counts = []
        for f in frames[:2]:
            r = gw.sam(ph, tag=tag, image=f, public_data=public_data, caller="etho.perception.planner")
            cost += r.cost_usd
            counts.append(len([x for line in r.lines() for x in parse_sam_line(line, "image")]))
        steps.append({"step": "image_check", "phrase": ph, "counts": counts})
        scored.append((sum(c >= expected for c in counts), -abs(sum(counts) / len(counts) - expected), -rank, ph))
    # Video probe on the best phrases, in order, until one passes.
    probe = _probe_clip(video, probe_s)
    n_probe = _nframes(probe)
    for *_, ph in sorted(scored, reverse=True):  # ties keep the model's own preference order
        r = gw.sam(ph, tag=tag, video=probe, n_frames=n_probe, public_data=public_data,
                   metadata={"mask_encoding": "one_bit"}, caller="etho.perception.planner")
        cost += r.cost_usd
        per = count_objects(list(r.lines()))
        frac = sum(c >= expected for c in per) / max(n_probe, 1)
        steps.append({"step": "video_probe", "phrase": ph, "frames": n_probe, "frac_with_expected": round(frac, 3)})
        if frac >= 0.9:
            return Plan(ph, expected, cue, method, steps, cost)
    best = sorted(scored, reverse=True)[0][-1]
    steps.append({"step": "fallback", "phrase": best, "note": "no phrase passed the video probe"})
    return Plan(best, expected, cue, method, steps, cost)


def _nframes(p: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_packets", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def _probe_clip(video: Path, seconds: float, fps: int = 10) -> Path:
    d = Path(tempfile.mkdtemp(prefix="etho_probe_"))
    out = d / "probe.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-t", str(seconds), "-vf", f"fps={fps}",
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


__all__ = ["plan", "Plan", base64.__name__]
