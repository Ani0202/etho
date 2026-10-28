"""User corrections: payload validation and conversion to the format `etho.rules.revise.revise` takes.

The page sends what the researcher did in their own terms:
  - "wrong":  this bout (behavior, start, end) is not that behavior; the right label is X or "none".
  - "missed": the behavior happened in this time range but nothing (or something else) was scored.
`revise` wants {seq, s0, s1, behavior, kind: "fp"|"fn", predicted_label, correct_label} per detector.
Marking a bout as a different behavior is two facts (a false positive of one detector and a missed
bout of another), so it becomes two corrections.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

SEQ = "video"
MAX_WINDOW_S = 120.0


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["wrong", "missed"]
    behavior: str = Field(min_length=1, max_length=40)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    correct_label: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _shape(self):
        if self.end_s <= self.start_s:
            raise ValueError("end must be after start")
        if self.end_s - self.start_s > MAX_WINDOW_S:
            raise ValueError(f"a correction window can be at most {MAX_WINDOW_S:g} s")
        if self.kind == "wrong" and not self.correct_label:
            raise ValueError("a wrong bout needs the correct label (a behavior, or 'none')")
        if self.kind == "missed" and self.correct_label is not None:
            raise ValueError("a missed bout takes no correct_label")
        return self


class RevisePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corrections: list[Correction] = Field(min_length=1, max_length=200)


class CorrectionError(ValueError):
    pass


def validate(payload: dict, *, names: list[str], duration_s: float, fps: float, max_total: int,
             existing: int = 0) -> list[Correction]:
    """Parse and check against this project's behaviors and video length."""
    try:
        p = RevisePayload.model_validate(payload)
    except Exception as e:  # pydantic.ValidationError: keep the message short and readable
        errs = getattr(e, "errors", lambda: [])()
        msg = "; ".join(f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in errs[:3]) or str(e)
        raise CorrectionError(msg) from e
    if existing + len(p.corrections) > max_total:
        raise CorrectionError(f"at most {max_total} corrections per video (you have {existing})")
    allowed = set(names)
    for i, c in enumerate(p.corrections, 1):
        if c.behavior not in allowed:
            raise CorrectionError(f"correction {i}: unknown behavior {c.behavior!r}")
        if c.kind == "wrong":
            if c.correct_label != "none" and c.correct_label not in allowed:
                raise CorrectionError(f"correction {i}: unknown label {c.correct_label!r}")
            if c.correct_label == c.behavior:
                raise CorrectionError(f"correction {i}: the correct label is the same as the scored one")
        if c.start_s >= duration_s:
            raise CorrectionError(f"correction {i}: starts after the end of the video ({duration_s:.1f} s)")
        if c.end_s > duration_s + 1.0 / fps:
            raise CorrectionError(f"correction {i}: ends after the end of the video ({duration_s:.1f} s)")
    return p.corrections


def window(c: Correction, fps: float, n: int) -> tuple[int, int]:
    s0 = min(n - 1, max(0, int(round(c.start_s * fps))))
    s1 = min(n, max(s0 + 1, int(round(c.end_s * fps))))
    return s0, s1


def to_revise_format(cs: list[Correction], labels: np.ndarray, names: list[str], fps: float) -> list[dict]:
    """`labels` are the final per-sample codes the researcher was looking at (to name what was predicted)."""
    n = len(labels)
    other = len(names)

    def predicted(s0: int, s1: int) -> str:
        seg = labels[s0:s1]
        vals, counts = np.unique(seg, return_counts=True)
        code = int(vals[np.argmax(counts)])
        return names[code] if code < other else "none"

    out = []
    for c in cs:
        s0, s1 = window(c, fps, n)
        if c.kind == "wrong":
            out.append({"seq": SEQ, "s0": s0, "s1": s1, "behavior": c.behavior, "kind": "fp",
                        "predicted_label": c.behavior, "correct_label": c.correct_label})
            if c.correct_label != "none":  # it was really another behavior: that detector missed it
                out.append({"seq": SEQ, "s0": s0, "s1": s1, "behavior": c.correct_label, "kind": "fn",
                            "predicted_label": c.behavior, "correct_label": c.correct_label})
        else:
            out.append({"seq": SEQ, "s0": s0, "s1": s1, "behavior": c.behavior, "kind": "fn",
                        "predicted_label": predicted(s0, s1), "correct_label": c.behavior})
    return out


def satisfied(labels: np.ndarray, corrections: list[dict], names: list[str]) -> dict:
    """How many corrections the *final* labels (after tier 1) satisfy: a false positive is satisfied when
    under half the window is still that behavior, a missed bout when at least half is."""
    codes = {n: i for i, n in enumerate(names)}
    ok = 0
    for c in corrections:
        frac = float(np.mean(labels[c["s0"]:c["s1"]] == codes[c["behavior"]]))
        ok += (frac < 0.5) if c["kind"] == "fp" else (frac >= 0.5)
    return {"satisfied": ok, "n": len(corrections)}
