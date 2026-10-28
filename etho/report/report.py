"""Etho report (architecture stage 6): ethogram timeline, per-behavior metrics, methods paragraph,
and the audit trail, as one self-contained HTML file.

Colors: the dataviz reference palette's first three categorical slots (validated all-pairs, light
and dark). Aqua is below 3:1 on the light surface, so every row is direct-labeled and a table view
is included (the relief rule). Each bout has a hover tooltip (SVG <title>).
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from etho.gateway import Gateway

BEHAVIOR_ORDER = ("attack", "investigation", "mount")  # fixed slot order: color follows the behavior
SLOTS = {"attack": 1, "investigation": 2, "mount": 3}

CSS = """
.viz-root { color-scheme: light; --surface-1:#fcfcfb; --page:#f9f9f7; --text-primary:#0b0b0b;
  --text-secondary:#52514e; --muted:#898781; --grid:#e4e3df;
  --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a;
  font-family: system-ui, -apple-system, Segoe UI, sans-serif; background:var(--page); color:var(--text-primary);
  margin:0; padding:24px; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) .viz-root {
  color-scheme: dark; --surface-1:#1a1a19; --page:#0d0d0d; --text-primary:#f0efec; --text-secondary:#c3c2b7;
  --grid:#2e2d2b; --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; } }
:root[data-theme="dark"] .viz-root { color-scheme: dark; --surface-1:#1a1a19; --page:#0d0d0d;
  --text-primary:#f0efec; --text-secondary:#c3c2b7; --grid:#2e2d2b;
  --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; }
.card { background:var(--surface-1); border-radius:8px; padding:16px 20px; margin:0 0 16px; max-width:1100px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:15px; margin:0 0 10px; color:var(--text-secondary); }
.sub { color:var(--text-secondary); font-size:13px; }
table { border-collapse:collapse; font-size:13px; } th, td { text-align:right; padding:4px 10px; }
th:first-child, td:first-child { text-align:left; } th { color:var(--text-secondary); font-weight:600; }
td { font-variant-numeric: tabular-nums; } .sw { display:inline-block; width:10px; height:10px; border-radius:2px;
  margin-right:6px; vertical-align:-1px; }
.methods { font-size:14px; line-height:1.5; max-width:900px; } code, pre { font-size:12px; }
details summary { cursor:pointer; color:var(--text-secondary); }
"""


@dataclass
class ReportInput:
    title: str
    labels: np.ndarray          # per-sample int codes (0 attack, 1 investigation, 2 mount, 3 other)
    fps: float
    meta: dict = field(default_factory=dict)   # video, phrase, models, timings, costs, detectors...


def bouts(mask: np.ndarray) -> list[tuple[int, int]]:
    m = np.r_[0, mask.astype(int), 0]
    d = np.diff(m)
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist(), strict=True))


def metrics(labels: np.ndarray, fps: float, codes: dict[str, int]) -> dict[str, dict]:
    n = len(labels)
    out = {}
    for b in BEHAVIOR_ORDER:
        bs = bouts(labels == codes[b])
        durs = [(e - s) / fps for s, e in bs]
        out[b] = {"bouts": len(bs), "total_s": round(sum(durs), 1),
                  "pct_time": round(100 * sum(durs) / (n / fps), 1) if n else 0.0,
                  "latency_s": round(bs[0][0] / fps, 1) if bs else None,
                  "mean_bout_s": round(float(np.mean(durs)), 2) if durs else None,
                  "bouts_list": bs}
    return out


def ethogram_svg(m: dict, n: int, fps: float, width: int = 1040) -> str:
    row_h, gap, left, top = 22, 10, 110, 8
    plot_w = width - left - 16
    dur = n / fps
    h = top + len(BEHAVIOR_ORDER) * (row_h + gap) + 28
    parts = [f'<svg role="img" aria-label="Ethogram" viewBox="0 0 {width} {h}" width="100%" '
             f'style="max-width:{width}px">']
    # recessive time grid every minute (or 10 s for short videos)
    step = 60 if dur > 120 else 10
    for t in np.arange(0, dur + 1e-9, step):
        x = left + plot_w * t / dur
        parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{h - 24}" stroke="var(--grid)" '
                     'stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{h - 8}" font-size="11" fill="var(--muted)" text-anchor="middle">'
                     f'{int(t // 60)}:{int(t % 60):02d}</text>')
    for i, b in enumerate(BEHAVIOR_ORDER):
        y = top + i * (row_h + gap)
        parts.append(f'<text x="{left - 10}" y="{y + row_h * 0.7:.1f}" font-size="13" fill="var(--text-primary)" '
                     f'text-anchor="end">{b}</text>')
        for s, e in m[b]["bouts_list"]:
            x = left + plot_w * s / n
            w = max(1.5, plot_w * (e - s) / n)
            tip = f"{b}: {s / fps:.1f}-{e / fps:.1f} s ({(e - s) / fps:.1f} s)"
            parts.append(f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{row_h}" rx="2" '
                         f'fill="var(--series-{SLOTS[b]})"><title>{tip}</title></rect>')
    parts.append("</svg>")
    return "".join(parts)


def methods_paragraph(inp: ReportInput, m: dict, gw: Gateway | None, public_data: bool) -> tuple[str, float]:
    facts = {"video": inp.meta.get("video"), "duration_s": round(len(inp.labels) / inp.fps, 1),
             "sample_fps": inp.fps, "pipeline": inp.meta.get("pipeline"),
             "metrics": {b: {k: v for k, v in d.items() if k != "bouts_list"} for b, d in m.items()},
             "costs_usd": inp.meta.get("costs_usd"), "timings_s": inp.meta.get("timings_s")}
    prompt = ("Write a concise methods paragraph (120-180 words) for a research report, describing how this "
              "behavioral scoring was produced, using ONLY the facts below. Do not invent any number, model, or "
              "step that is not in the facts; if something is missing, omit it. Plain prose, no lists.\n\n"
              f"FACTS:\n{json.dumps(facts, indent=1, default=str)}")
    if gw is None:
        return "", 0.0
    model = "muse-spark-1.3-contributor" if public_data else "muse-spark-1.3"
    res = gw.chat("meta", model, [{"role": "user", "content": prompt}], effort="low", tag="eval-llm",
                  max_tokens=4000, public_data=public_data, caller="etho.report.methods")
    return res.text.strip(), res.cost_usd


def build(inp: ReportInput, out: Path, *, codes: dict[str, int], gw: Gateway | None = None,
          public_data: bool = False) -> Path:
    m = metrics(inp.labels, inp.fps, codes)
    methods, mcost = methods_paragraph(inp, m, gw, public_data)
    rows = "".join(
        f'<tr><td><span class="sw" style="background:var(--series-{SLOTS[b]})"></span>{b}</td>'
        f'<td>{d["bouts"]}</td><td>{d["total_s"]}</td><td>{d["pct_time"]}</td>'
        f'<td>{d["latency_s"] if d["latency_s"] is not None else "-"}</td>'
        f'<td>{d["mean_bout_s"] if d["mean_bout_s"] is not None else "-"}</td></tr>' for b, d in m.items())
    audit = json.dumps(inp.meta, indent=1, default=str)
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(inp.title)}</title>
<style>{CSS}</style></head><body class="viz-root">
<div class="card"><h1>{html.escape(inp.title)}</h1>
<div class="sub">{html.escape(str(inp.meta.get("video", "")))} | {len(inp.labels) / inp.fps / 60:.1f} min scored at
{inp.fps:g} fps | training-free: no labeled examples given to the models, no model training</div></div>
<div class="card"><h2>Ethogram</h2>{ethogram_svg(m, len(inp.labels), inp.fps)}</div>
<div class="card"><h2>Per-behavior metrics</h2><table>
<tr><th>behavior</th><th>bouts</th><th>total s</th><th>% of video</th><th>latency to first s</th>
<th>mean bout s</th></tr>
{rows}</table></div>
<div class="card"><h2>Methods</h2><div class="methods">{html.escape(methods) or "(not generated)"}</div></div>
<div class="card"><details><summary>Audit trail (models, prompts, detector code, costs, timings)</summary>
<pre>{html.escape(audit)}</pre></details></div>
</body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    inp.meta["methods_cost_usd"] = mcost
    return out
