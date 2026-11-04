"""The app's real pipeline backend, plus the replay label on what it writes.

`ReplayBackend` changes no stage. After the report stage it adds the label to the top of `report.html`, and after
`finalize` it records the label in `result.json` (`replay`), so the page, the report and the audit data all say the
run is a replay. The label has two parts: the fixed first line (`LABEL`) and a sentence that says which numbers on the
page are the recorded live run's, not this run's, with the live run's real figures from the recording.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from etho.app.backend import Backend, Ctx
from etho.app.store import read_json, write_json

from . import LABEL

BANNER_STYLE = ("background:#fff4d6;color:#4a3200;border:1px solid #d9a400;border-radius:6px;padding:8px 12px;"
                "margin:0 0 12px;font:600 14px/1.4 system-ui,sans-serif")
SECOND_STYLE = "font-weight:400;font-size:13px;margin-top:4px"


def live_sentence(live: dict | None) -> str:
    """Which numbers are recorded. `live` is the index header's `live_run` (wall_s, cost_usd)."""
    figures = ""
    if live and live.get("wall_s") is not None and live.get("cost_usd") is not None:
        figures = f" ({live['wall_s']:.0f} s, ${live['cost_usd']:.2f})"
    return (f"Costs and the methods-paragraph timings are the recorded live run's{figures}. This replay made no "
            "model call and spent nothing; its wall-clock is local compute only.")


def banner_html(live: dict | None = None) -> str:
    return (f'<div class="replay-label" style="{BANNER_STYLE}"><div>{html.escape(LABEL)}</div>'
            f'<div style="{SECOND_STYLE}">{html.escape(live_sentence(live))}</div></div>')


def label_report(path: Path, live: dict | None = None) -> None:
    """Put the label first in the report body (idempotent)."""
    text = path.read_text(encoding="utf-8")
    if "replay-label" in text:
        return
    marker = '<body class="viz-root">'
    banner = banner_html(live)
    text = text.replace(marker, marker + banner, 1) if marker in text else banner + text
    path.write_text(text, encoding="utf-8")


_ESTIMATE = re.compile(r"(estimated cost \$[\d.]+) \(cap check passed\)")


class ReplayBackend(Backend):
    def __init__(self, cfg, live: dict | None = None):
        super().__init__(cfg)
        self.live = live

    def stage_validate(self, ctx: Ctx) -> None:
        """The validate stage's status line quotes the live-run cost estimate; say what it is in a replay."""
        say = ctx.say
        ctx.say = lambda m: say(_ESTIMATE.sub(r"\1 (estimate for a live run; this replay spends $0; "
                                              r"cap check passed)", m))
        try:
            super().stage_validate(ctx)
        finally:
            ctx.say = say

    def stage_report(self, ctx: Ctx) -> None:
        super().stage_report(ctx)
        label_report(ctx.outdir / "report.html", self.live)

    def finalize(self, ctx: Ctx, ledger_cost: float | None = None) -> None:
        super().finalize(ctx, ledger_cost)
        p = ctx.project.base / "result.json" if ctx.job.kind == "analyze" else ctx.outdir / "revision.json"
        d = read_json(p)
        if isinstance(d, dict):
            d["replay"] = {"label": LABEL, "note": live_sentence(self.live)}
            write_json(p, d)
