"""English behavior definition -> a Python detector over tracking features, written by Muse Spark.

One call per behavior (effort high). The model sees the definition, the feature dictionary, and
label-free feature statistics; never labels. The returned code runs in the sandbox; on an error
the model gets the error text and may repair it, at most MAX_REPAIRS times.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import numpy as np

from etho.gateway import Gateway
from etho.tracking.features import FEATURE_DOC

from .sandbox import SandboxError, describe, run_detector

MAX_REPAIRS = 2

SYSTEM = """You are an expert in computational ethology writing behavior detectors.
You write one Python function `detect(t, fps)` that marks, for each video sample, whether a behavior
is happening. You never see labeled examples: you translate the written definition into geometry
and motion over the tracking features provided. Be explicit and physically reasoned; prefer robust
thresholds expressed relative to body size (e.g. the median sqrt of the animal's area) over magic
pixel constants, and handle NaN (animal not detected) explicitly."""

TEMPLATE = """Behavior to detect: **{name}**
Definition (from the dataset authors): {definition}

The subject is {subject}; the partner is {partner}. In the features, prefix `r_` is the subject
(resident) and `i_` is the partner (intruder).

Video sampling: {fps} samples per second. Coordinates are pixels in a {w}x{h} top-down view.

Feature arrays in `t` (dict of 1-D numpy float arrays, one value per sample; NaN = not available):
{feature_doc}

Label-free statistics of these features over the videos you will run on:
{stats}

Notes from the tracker:
- When the partner is hidden under the subject (e.g. during mounting), `i_present` can still be 1
  with a much smaller `i_area`/`i_visible_ratio`, or the partner can be missing (NaN).
- `gap` is 0 when the masks touch or overlap.
- Headings come from mask shape (and the subject's head implant when visible); they can be noisy.

Available without import: `np` (numpy), `math`, and helpers:
  smooth(x, k) -> centered NaN-aware moving average over k samples
  hysteresis(x, on, off) -> bool array, True from x>=on until x<off
  bouts_clean(mask, fps, min_s=0.2, max_gap_s=0.3) -> fill short gaps, drop short bouts
No imports, no file or network access, no classes, no try/except.

Return ONLY a single ```python code block defining `def detect(t, fps):` that returns a numpy bool
array with one value per sample. Briefly justify each threshold in a comment."""

REPAIR = """Your detector failed when run on the real features:

{error}

Fix it and return ONLY the corrected ```python code block."""


@dataclass
class Detector:
    behavior: str
    code: str
    model: str
    calls: list[dict] = field(default_factory=list)
    prompt_hash: str = ""


def extract_code(text: str) -> str:
    m = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    if m:
        return max(m, key=len).strip()
    if "def detect" in text:
        return text[text.index("def detect"):].strip()
    raise SandboxError("no python code block in the model's answer")


def build_prompt(name: str, definition: str, subject: str, partner: str, stats: str, fps: float,
                 frame_wh: tuple[int, int]) -> str:
    doc = "\n".join(f"- {k}: {v}" for k, v in FEATURE_DOC.items())
    return TEMPLATE.format(name=name, definition=definition, subject=subject, partner=partner, fps=fps,
                           w=frame_wh[0], h=frame_wh[1], feature_doc=doc, stats=stats)


def synthesize(name: str, definition: str, *, subject: str, partner: str, tables: list[dict[str, np.ndarray]],
               fps: float, frame_wh: tuple[int, int], gw: Gateway | None = None,
               model: str = "muse-spark-1.3", public_data: bool = False, tag: str = "eval-llm",
               effort: str = "high", max_tokens: int = 16000, candidate: int = 0) -> Detector:
    """`candidate` > 0 asks for an independent attempt (used for label-free ensembling)."""
    gw = gw or Gateway()
    pooled = {k: np.concatenate([np.asarray(t[k], float) for t in tables]) for k in tables[0]}
    prompt = build_prompt(name, definition, subject, partner, describe(pooled), fps, frame_wh)
    if candidate:
        prompt += (f"\n\n(Independent attempt #{candidate}: reason from the definition afresh and choose your own "
                   "feature combination and thresholds.)")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    det = Detector(name, "", model, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16])
    probe = tables[0]
    for attempt in range(MAX_REPAIRS + 1):
        res = gw.chat("meta", model, msgs, effort=effort, tag=tag, max_tokens=max_tokens,
                      public_data=public_data, caller=f"etho.rules.synthesize:{name}")
        det.calls.append({"attempt": attempt, "cost_usd": res.cost_usd, "usage": res.usage,
                          "cache_hit": res.cache_hit, "latency_s": res.latency_s})
        try:
            code = extract_code(res.text)
            run_detector(code, probe, fps)
            det.code = code
            return det
        except SandboxError as e:
            if attempt == MAX_REPAIRS:
                raise SandboxError(f"{name}: detector still failing after {MAX_REPAIRS} repairs: {e}") from e
            msgs = msgs + [{"role": "assistant", "content": res.text},
                           {"role": "user", "content": REPAIR.format(error=str(e)[:3000])}]
    raise AssertionError("unreachable")


def save(dets: list[Detector], path) -> None:
    path.write_text(json.dumps([vars(d) for d in dets], indent=1, default=float))


JOINT = """Behaviors to score (definitions from the dataset authors):
{defs}

The subject is {subject}; the partner is {partner}. In the features, prefix `r_` is the subject
(resident) and `i_` is the partner (intruder).

Video sampling: {fps} samples per second. Coordinates are pixels in a {w}x{h} top-down view.

Feature arrays in `t` (dict of 1-D numpy float arrays, one value per sample; NaN = not available):
{feature_doc}

Label-free statistics of these features over the videos you will run on:
{stats}

Notes from the tracker:
- When the partner is hidden under the subject (e.g. during mounting), `i_present` can still be 1
  with a much smaller `i_area`/`i_visible_ratio`, or the partner can be missing (NaN).
- `gap` is 0 when the masks touch or overlap.
- Headings come from mask shape (and the subject's head implant when visible); they can be noisy.

Write ONE function `classify(t, fps)` that assigns each sample exactly one label code:
{codes}
The behaviors are mutually exclusive per sample, so weigh them against each other: decide, for each
sample, which definition fits best, and use "other" when none clearly applies. Think about how the
behaviors differ physically (posture, overlap, motion intensity, who is oriented to whom).

Available without import: `np` (numpy), `math`, and helpers:
  smooth(x, k) -> centered NaN-aware moving average over k samples
  hysteresis(x, on, off) -> bool array, True from x>=on until x<off
  bouts_clean(mask, fps, min_s=0.2, max_gap_s=0.3) -> fill short gaps, drop short bouts
No imports, no file or network access, no classes, no try/except.

Return ONLY a single ```python code block defining `def classify(t, fps):` that returns a numpy int array
with one label code per sample. Briefly justify each threshold in a comment."""


def synthesize_joint(definitions: dict[str, str], codes: dict[str, int], *, subject: str, partner: str,
                     tables: list[dict[str, np.ndarray]], fps: float, frame_wh: tuple[int, int],
                     gw: Gateway | None = None, model: str = "muse-spark-1.3", public_data: bool = False,
                     tag: str = "eval-llm", effort: str = "high", max_tokens: int = 20000) -> Detector:
    """One classifier for all behaviors at once (they are mutually exclusive per sample)."""
    gw = gw or Gateway()
    pooled = {k: np.concatenate([np.asarray(t[k], float) for t in tables]) for k in tables[0]}
    doc = "\n".join(f"- {k}: {v}" for k, v in FEATURE_DOC.items())
    defs = "\n".join(f"- {k}: {v}" for k, v in definitions.items())
    code_doc = "\n".join(f"  {v} = {k}" for k, v in sorted(codes.items(), key=lambda kv: kv[1]))
    prompt = JOINT.format(defs=defs, subject=subject, partner=partner, fps=fps, w=frame_wh[0], h=frame_wh[1],
                          feature_doc=doc, stats=describe(pooled), codes=code_doc)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    det = Detector("joint", "", model, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16])
    for attempt in range(MAX_REPAIRS + 1):
        res = gw.chat("meta", model, msgs, effort=effort, tag=tag, max_tokens=max_tokens,
                      public_data=public_data, caller="etho.rules.synthesize:joint")
        det.calls.append({"attempt": attempt, "cost_usd": res.cost_usd, "usage": res.usage,
                          "cache_hit": res.cache_hit, "latency_s": res.latency_s})
        try:
            code = extract_code(res.text)
            run_detector(code, tables[0], fps, labels=len(codes))
            det.code = code
            return det
        except SandboxError as e:
            if attempt == MAX_REPAIRS:
                raise SandboxError(f"joint: classifier still failing after {MAX_REPAIRS} repairs: {e}") from e
            msgs = msgs + [{"role": "assistant", "content": res.text},
                           {"role": "user", "content": REPAIR.format(error=str(e)[:3000])}]
    raise AssertionError("unreachable")
