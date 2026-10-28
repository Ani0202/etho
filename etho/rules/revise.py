"""Correction loop: a user fixes a few bouts; Muse Spark revises the detector code.

The model sees the current detector, the definition, and each corrected window with a summary of
the tracking features inside it (what the detector "saw") plus what it predicted vs what the user
says. It returns revised code, which must pass the sandbox on real features before it is accepted.
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np

from etho.gateway import Gateway

from .sandbox import SandboxError, run_detector
from .synthesize import MAX_REPAIRS, REPAIR, SYSTEM, Detector, extract_code

KEY_FEATURES = ("gap", "dist", "r_head_to_i", "i_head_to_r", "r_facing", "i_facing", "r_speed", "i_speed",
                "approach", "r_visible_ratio", "i_visible_ratio", "r_area", "i_area", "r_elong", "i_elong")

REVISE = """You wrote this detector for **{name}** (definition: {definition}):

```python
{code}
```

A researcher reviewed the output and corrected these windows (times in seconds; feature summaries are
median [min..max] over the window):
{corrections}

Revise the detector so it handles cases like these correctly, while staying faithful to the written
definition. Generalize (adjust the logic or thresholds); do not hard-code these specific time windows.
Keep the same function signature and rules (no imports, no I/O). Return ONLY the full revised ```python code block,
with a short comment at the top listing what you changed and why."""


def summarize_window(table: dict[str, np.ndarray], s0: int, s1: int) -> str:
    parts = []
    for k in KEY_FEATURES:
        if k not in table:
            continue
        v = np.asarray(table[k][s0:s1], float)
        f = v[np.isfinite(v)]
        if len(f) == 0:
            parts.append(f"{k}=NaN")
        else:
            parts.append(f"{k}={np.median(f):.3g} [{f.min():.3g}..{f.max():.3g}]")
    return ", ".join(parts)


def format_corrections(corrections: list[dict], tables: dict[str, dict[str, np.ndarray]], fps: float) -> str:
    lines = []
    for i, c in enumerate(corrections, 1):
        t = tables[c["seq"]]
        what = (f"detector said {c['behavior']} but it is NOT ({c['correct_label']})" if c["kind"] == "fp"
                else f"detector missed it: this IS {c['behavior']} (detector said {c['predicted_label']})")
        lines.append(f"{i}. t={c['s0'] / fps:.1f}-{c['s1'] / fps:.1f}s: {what}. Features: "
                     f"{summarize_window(t, c['s0'], c['s1'])}")
    return "\n".join(lines)


NARROW = """Your revision was checked against the researcher's corrections and the full videos:
- corrections handled: {fixed_new}/{n} (the previous version handled {fixed_old}/{n})
- samples marked {name} across all videos changed by {change:+.0%} ({pos_old} -> {pos_new})
{problem}
Make a narrower, targeted change that handles the corrected cases without shifting the overall rate by more than
{max_change:.0%}. Return ONLY the full revised ```python code block."""

MAX_RATE_CHANGE = 0.20
MAX_FEEDBACK_ROUNDS = 2


def check_revision(old_code: str, new_code: str, corrections: list[dict], tables: dict[str, dict[str, np.ndarray]],
                   fps: float) -> dict:
    """Self-check using only the user's corrections: how many corrected windows each version gets right,
    and how much the revision shifts the detector's overall positive rate."""
    def run(code):
        return {s: run_detector(code, t, fps) for s, t in tables.items()}

    def fixed(m):
        n = 0
        for c in corrections:
            frac = float(np.mean(m[c["seq"]][c["s0"]:c["s1"]]))
            n += (frac < 0.5) if c["kind"] == "fp" else (frac >= 0.5)
        return n

    o, r = run(old_code), run(new_code)
    po, pr = sum(int(v.sum()) for v in o.values()), sum(int(v.sum()) for v in r.values())
    return {"fixed_old": fixed(o), "fixed_new": fixed(r), "n": len(corrections), "pos_old": po, "pos_new": pr,
            "change": (pr - po) / max(po, 1)}


def revise(det: Detector, definition: str, corrections: list[dict], tables: dict[str, dict[str, np.ndarray]],
           *, fps: float = 10.0, gw: Gateway | None = None, model: str = "muse-spark-1.3",
           public_data: bool = False, tag: str = "eval-llm", effort: str = "high",
           max_tokens: int = 16000, self_check: bool = True) -> Detector:
    gw = gw or Gateway()
    mine = [c for c in corrections if c["behavior"] == det.behavior]
    if not mine:
        return det
    prompt = REVISE.format(name=det.behavior, definition=definition, code=det.code,
                           corrections=format_corrections(mine, tables, fps))
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    new = Detector(det.behavior, "", model, prompt_hash=det.prompt_hash)
    probe = tables[mine[0]["seq"]]
    repairs, feedback = 0, 0
    while True:
        res = gw.chat("meta", model, msgs, effort=effort, tag=tag, max_tokens=max_tokens,
                      public_data=public_data, caller=f"etho.rules.revise:{det.behavior}")
        call = {"attempt": len(new.calls), "cost_usd": res.cost_usd, "usage": res.usage, "cache_hit": res.cache_hit,
                "latency_s": res.latency_s, "n_corrections": len(mine)}
        new.calls.append(call)
        try:
            code = extract_code(res.text)
            run_detector(code, probe, fps)
        except SandboxError as e:
            repairs += 1
            if repairs > MAX_REPAIRS:
                break
            msgs = msgs + [{"role": "assistant", "content": res.text},
                           {"role": "user", "content": REPAIR.format(error=str(e)[:3000])}]
            continue
        if not self_check:
            new.code = code
            return new
        chk = check_revision(det.code, code, mine, tables, fps)
        call["check"] = chk
        good = chk["fixed_new"] > chk["fixed_old"] and abs(chk["change"]) <= MAX_RATE_CHANGE
        if good:
            new.code = code
            return new
        feedback += 1
        if feedback > MAX_FEEDBACK_ROUNDS:
            break
        problem = ("- it does not handle more of the corrected cases than before."
                   if chk["fixed_new"] <= chk["fixed_old"]
                   else "- that rate change would add or remove many detections the researcher did not ask about.")
        msgs = msgs + [{"role": "assistant", "content": res.text},
                       {"role": "user", "content": NARROW.format(name=det.behavior, problem=problem,
                                                                 max_change=MAX_RATE_CHANGE, **chk)}]
    # No revision passed the self-check: keep the original detector (record why).
    kept = replace(det, calls=det.calls + [{"revision_rejected": True, "candidates": len(new.calls),
                                             "last_check": new.calls[-1].get("check"),
                                             "cost_usd": sum(c["cost_usd"] for c in new.calls)}])
    return kept


__all__ = ["revise", "format_corrections", "summarize_window", json.__name__]
