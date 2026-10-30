"""The v5 beat timeline (docs/DEMO_PLAN.md, "Script (v5)" table). Checked against the plan by tests."""
from __future__ import annotations

import re
from dataclasses import dataclass

from video.common import ROOT

HARD_LIMIT_S = 180.0
PLAN = ROOT / "docs" / "DEMO_PLAN.md"


@dataclass(frozen=True)
class Beat:
    key: str
    title: str          # the plan's name for the beat
    start: float
    end: float

    @property
    def dur(self) -> float:
        return self.end - self.start


def _t(s: str) -> float:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


# (key, plan title, start, end). Keep identical to the DEMO_PLAN table; test_timeline.py enforces it.
BEATS = [
    Beat("hook", "Hook", _t("0:00"), _t("0:20")),
    Beat("who", "Who it's for, the gap", _t("0:20"), _t("0:35")),
    Beat("live_run", "Live run", _t("0:35"), _t("1:04")),
    Beat("code", "Code beat", _t("1:04"), _t("1:21")),
    Beat("results", "Results and traced error", _t("1:21"), _t("1:50")),
    Beat("correction", "Correction loop", _t("1:50"), _t("2:00")),
    Beat("evidence", "Evidence card", _t("2:00"), _t("2:26")),
    Beat("fly", "Second species", _t("2:26"), _t("2:39")),
    Beat("close", "Close", _t("2:39"), _t("2:54")),
]


def parse_plan_table(text: str | None = None) -> list[tuple[str, float, float]]:
    """Rows of the plan's beat table as (title, start_s, end_s)."""
    text = text if text is not None else PLAN.read_text(encoding="utf-8")
    rows = []
    for m in re.finditer(r"^\| (?!Beat)([^|]+?) \| (\d+:\d\d)-(\d+:\d\d) \| \d+ \| [\d.]+ s \|$", text, re.M):
        rows.append((m.group(1).strip(), _t(m.group(2)), _t(m.group(3))))
    return rows


def total() -> float:
    return BEATS[-1].end


def beat(key: str) -> Beat:
    return next(b for b in BEATS if b.key == key)


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"
