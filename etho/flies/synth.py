"""Fly vs. Fly rule synthesis (ADR-002): an English definition -> a pair-level detector over the fly features.

Same recipe as etho/rules/synthesize.py (one Muse Spark call per behavior and candidate, the model sees the
definition, FLY_FEATURE_DOC and label-free feature statistics, never a label; the code runs in the sandbox), with
a fly-specific prompt: two identical, symmetric animals (`a_*` / `b_*` labels are arbitrary), pair-level scoring
("either fly does it"), 144 px frames, and durations in seconds so the same code runs at 30 and 10 fps.

Independent candidates are ranked by a label-free criterion: `select_medoid` keeps the candidate that agrees most
(pooled frame F1) with the others on the tracked movies themselves; `vote` keeps several and takes a majority.
Nothing here reads a label.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from etho.gateway import Gateway
from etho.rules.sandbox import SandboxError, describe, run_detector
from etho.rules.synthesize import REPAIR, extract_code
from etho.tracking.fly_features import FLY_FEATURE_DOC

from .extras import EXTRA_DOC

MAX_REPAIRS = 2
FRAME_WH = (144, 144)
STAT_SKIP = ("src_frame", "id_epoch")  # bookkeeping, not behavior features

SYSTEM = """You are an expert in computational ethology writing behavior detectors.
You write one Python function `detect(t, fps)` that marks, for each video sample, whether a behavior
is happening. You never see labeled examples: you translate the written definition into geometry
and motion over the tracking features provided. Be explicit and physically reasoned; prefer robust
thresholds expressed relative to body size or to each animal's own typical values over magic pixel
constants, express every duration in seconds (multiply by fps), and handle NaN (not detected, or
velocity not valid) explicitly."""

TEMPLATE = """Behavior to detect: **{name}**
Definition (from the ethogram literature): {definition}

The subject is {subject}; the partner is {partner}. The scored unit is the PAIR: a sample is positive if
either fly performs the behavior.

Video: two fruit flies in a small arena, filmed from above; {fps:g} samples per second; coordinates are pixels
in a {w}x{h} frame (a fly is about 25-30 px long). The tracker (SAM 3.1 masks) labels the two flies `a` and `b`
arbitrarily and the labels can swap, so the two flies are exchangeable: write the test symmetrically (use the
pair-level `max_*` / `min_*` columns, or apply the test to both `a_*` and `b_*` and OR the results).

Feature arrays in `t` (dict of 1-D numpy float arrays, one value per sample; NaN = not available). Per-fly
columns exist for each fly with the prefix `a_` and `b_` (e.g. `a_area`, `b_area`); pair columns have no prefix:
{feature_doc}

Label-free statistics of these features over the videos you will run on:
{stats}

Notes from the tracker:
- The mask is what SAM segments at 144 px. Thin structures such as wings and legs are translucent and may be
  only partly or intermittently in the mask.
- `gap` is 0 when the masks touch or overlap; when the flies touch, SAM can merge or swap them for a while.
- Velocity features are NaN where they are not valid; decide explicitly what NaN means for your rule.
{extras_note}{hints}
Available without import: `np` (numpy), `math`, and helpers:
  smooth(x, k) -> centered NaN-aware moving average over k samples
  hysteresis(x, on, off) -> bool array, True from x>=on until x<off
  bouts_clean(mask, fps, min_s=0.2, max_gap_s=0.3) -> fill short gaps, drop short bouts
No imports, no file or network access, no classes, no try/except.

Return ONLY a single ```python code block defining `def detect(t, fps):` that returns a numpy bool
array with one value per sample. Briefly justify each threshold in a comment."""

# Prompt variants. "plain" adds nothing beyond the definition; "physics" adds cues derived from the definition
# text and general fly morphology (written before looking at any label), which the dev movies choose between.
HINTS = {
    "plain": "",
    "physics": """- Seen from above, a fly that raises or extends its wings gets a wider or spikier silhouette (larger
  `minor_len` / `minor_ratio` / `area_ratio`, lower `solidity`, higher `thin_frac` or `reach_ratio`) while it
  holds a posture rather than running; the ratio columns compare a fly with its own typical value in this video.
- "Quickly" and "snaps" in a definition mean a fast movement; a posture such as raised wings is a sustained
  state, while a snap or strike is only a few samples long, so a snap needs the fast (0.1 s) velocity columns and
  must not be smoothed away. Rearing up on the hind legs shortens the apparent body length seen from above.
""",
}


@dataclass
class Candidate:
    code: str
    model: str
    prompt_hash: str
    calls: list[dict] = field(default_factory=list)


EXTRAS_NOTE = """- The `vel_ok` guard hides the tracker's velocity columns (`speed`, `toward_speed`, `approach`, ...) near an
  identity-unknown run and near abrupt mask-area changes, which is where most close interactions happen. The
  `*_raw` columns at the end of the list are not hidden by it (they re-match the two flies between samples), so
  they stay valid while the flies touch.
"""  # noqa: E501


def build_prompt(name: str, definition: str, subject: str, partner: str, stats: str, fps: float,
                 variant: str = "plain", extras: bool = False) -> str:
    docs = {**FLY_FEATURE_DOC, **(EXTRA_DOC if extras else {})}
    doc = "\n".join(f"- {k}: {v}" for k, v in docs.items())
    return TEMPLATE.format(name=name, definition=definition, subject=subject, partner=partner, fps=fps,
                           w=FRAME_WH[0], h=FRAME_WH[1], feature_doc=doc, stats=stats, hints=HINTS[variant],
                           extras_note=EXTRAS_NOTE if extras else "")


def feature_stats(tables: list[dict[str, np.ndarray]]) -> str:
    """Pooled label-free percentiles of every feature column over the given movies."""
    keys = [k for k in tables[0] if k not in STAT_SKIP]
    pooled = {k: np.concatenate([np.asarray(t[k], float) for t in tables]) for k in keys}
    return describe(pooled)


def synthesize_candidate(name: str, definition: str, *, subject: str, partner: str, tables: list[dict],
                         fps: float, gw: Gateway, model: str, public_data: bool, tag: str = "ablation",
                         effort: str = "high", max_tokens: int = 16000, variant: str = "plain",
                         candidate: int = 0, extras: bool = False) -> Candidate:
    """One independent attempt (`candidate` > 0 changes the prompt, so the cache cannot return candidate 0).
    With `extras` the detector sees the EXTRA_DOC columns (`tables` must already contain them)."""
    prompt = build_prompt(name, definition, subject, partner, feature_stats(tables), fps, variant, extras)
    if candidate:
        prompt += (f"\n\n(Independent attempt #{candidate}: reason from the definition afresh and choose your own "
                   "feature combination and thresholds.)")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    cand = Candidate("", model, hashlib.sha256((SYSTEM + prompt).encode()).hexdigest()[:16])
    probe = tables[0]
    for attempt in range(MAX_REPAIRS + 1):
        res = gw.chat("meta", model, msgs, effort=effort, tag=tag, max_tokens=max_tokens, public_data=public_data,
                      caller=f"etho.flies.synth:{name}")
        cand.calls.append({"attempt": attempt, "cost_usd": res.cost_usd, "usage": res.usage,
                           "cache_hit": res.cache_hit, "latency_s": res.latency_s})
        try:
            code = extract_code(res.text)
            run_detector(code, probe, fps)
            cand.code = code
            return cand
        except SandboxError as e:
            if attempt == MAX_REPAIRS:
                raise SandboxError(f"{name}: detector still failing after {MAX_REPAIRS} repairs: {e}") from e
            msgs = msgs + [{"role": "assistant", "content": res.text},
                           {"role": "user", "content": REPAIR.format(error=str(e)[:3000])}]
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- label-free selection

def pooled_f1(a: list[np.ndarray], b: list[np.ndarray]) -> float:
    """Pooled frame F1 of mask list `a` against `b` (symmetric in practice); 0 when both are empty."""
    tp = sum(int(np.sum(x & y)) for x, y in zip(a, b, strict=True))
    fp = sum(int(np.sum(~x & y)) for x, y in zip(a, b, strict=True))
    fn = sum(int(np.sum(x & ~y)) for x, y in zip(a, b, strict=True))
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0


def select_medoid(masks: list[list[np.ndarray]]) -> tuple[int, list[float]]:
    """Index of the candidate with the highest mean pooled F1 agreement with the others (ties -> lowest index).
    `masks[c][m]` is candidate c's mask on movie m. Candidates that flag nothing never win unless all are empty."""
    n = len(masks)
    if n == 1:
        return 0, [0.0]
    agree = [0.0] * n
    for i, j in itertools.combinations(range(n), 2):
        f = pooled_f1(masks[i], masks[j])
        agree[i] += f / (n - 1)
        agree[j] += f / (n - 1)
    nonempty = [i for i in range(n) if any(m.any() for m in masks[i])] or list(range(n))
    best = max(nonempty, key=lambda i: (round(agree[i], 12), -i))
    return best, agree


def save_candidates(path: Path, doc: dict) -> None:
    Path(path).write_text(json.dumps(doc, indent=1, default=float))
