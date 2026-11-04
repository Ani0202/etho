"""The app's real pipeline backend, plus the replay label on what it writes.

`ReplayBackend` changes no stage. After the report stage it adds the label to the top of `report.html`, and after
`finalize` it records the label in `result.json` (`replay`), so the page, the report and the audit data all say the
run is a replay.
"""

from __future__ import annotations

import html
from pathlib import Path

from etho.app.backend import Backend, Ctx
from etho.app.store import read_json, write_json

from . import LABEL

BANNER_STYLE = ("background:#fff4d6;color:#4a3200;border:1px solid #d9a400;border-radius:6px;padding:8px 12px;"
                "margin:0 0 12px;font:600 14px/1.4 system-ui,sans-serif")


def banner_html() -> str:
    return f'<div class="replay-label" style="{BANNER_STYLE}">{html.escape(LABEL)}</div>'


def label_report(path: Path) -> None:
    """Put the label first in the report body (idempotent)."""
    text = path.read_text(encoding="utf-8")
    if "replay-label" in text:
        return
    marker = '<body class="viz-root">'
    text = text.replace(marker, marker + banner_html(), 1) if marker in text else banner_html() + text
    path.write_text(text, encoding="utf-8")


class ReplayBackend(Backend):
    def stage_report(self, ctx: Ctx) -> None:
        super().stage_report(ctx)
        label_report(ctx.outdir / "report.html")

    def finalize(self, ctx: Ctx, ledger_cost: float | None = None) -> None:
        super().finalize(ctx, ledger_cost)
        p = ctx.project.base / "result.json" if ctx.job.kind == "analyze" else ctx.outdir / "revision.json"
        d = read_json(p)
        if isinstance(d, dict):
            d["replay"] = {"label": LABEL, "note": "costs are what the recorded calls were billed when they were "
                           "made; this run made no model call and spent nothing"}
            write_json(p, d)
