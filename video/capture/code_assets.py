"""Code-beat assets for the demo rough cut (1:04-1:21): architecture diagram, three syntax-highlighted excerpts,
commit-date overlay, the pytest summary line, and a clip that pans across them.

No model calls. Needs pygments, Playwright (Chromium) and ffmpeg.

    python video/capture/code_assets.py [--pytest-line "662 passed in 90s"]

Writes to data/video/capture/: code_architecture.png, code_gateway.png, code_tier1.png, code_sandbox.png,
code_tests.png and code.mp4 (about 17 s, 1920x1080, 30 fps).
"""
from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "video" / "capture"
TMP = ROOT / "data" / "video" / "tmp"
W, H = 1920, 1080
FONT_PX = 24

# (key, file, title, caption, [(first_line, last_line), ...], anchor text expected on the first excerpt line)
EXCERPTS = [
    ("gateway", "etho/gateway/gateway.py", "The gateway: cache first, reserve against the cap, then settle",
     "Every model call: a cache hit costs $0; otherwise the spend cap is reserved before the request is sent.",
     [(291, 298), (315, 326)], "def _llm_locked"),
    ("tier1", "etho/adjudication/tier1.py", "The cascade decision: what the frame-strip check may change",
     "Tier 1 sees a 6-frame strip at low effort; low-confidence or out-of-policy verdicts are set aside.",
     [(154, 156), (167, 181)], "with _FW_SLOTS"),
    ("sandbox", "etho/rules/sandbox.py", "The sandbox for model-written rules: AST check, then bubblewrap",
     "No imports, no dunder names, no network, read-only filesystem, empty environment.",
     [(167, 168), (250, 267)], "a = [shutil.which"),
]

PAGE = """<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#11131a;color:#e6e6ef;font-family:'DejaVu Sans',Arial,sans-serif}
.slide{width:%(w)dpx;height:%(h)dpx;box-sizing:border-box;padding:34px 60px;display:flex;
  flex-direction:column;background:#11131a}
h1{font-size:40px;margin:0 0 6px;font-weight:700;color:#fff}
.cap{font-size:26px;color:#aab;margin:0 0 18px}
.path{font-family:'DejaVu Sans Mono',monospace;font-size:26px;color:#7fd1ff;margin-bottom:10px}
.code{background:#1b1e29;border-radius:14px;padding:22px 26px;font-family:'DejaVu Sans Mono',monospace;
  font-size:%(fs)dpx;line-height:1.45;flex:1;overflow:hidden}
.code pre{margin:0;font:inherit;white-space:pre}
.ln{color:#5b6078;display:inline-block;width:64px;text-align:right;margin-right:26px;user-select:none}
.gap{color:#5b6078}
.foot{margin-top:16px;font-size:26px;color:#cfd2e6;display:flex;justify-content:space-between}
.foot b{color:#ffd166}
%(css)s
</style><div class="slide">%(body)s</div>"""


def read_ranges(rel: str, ranges: list[tuple[int, int]]) -> list[tuple[int, str] | None]:
    """Numbered source lines for the ranges, with None marking an omitted gap between ranges."""
    lines = (ROOT / rel).read_text().splitlines()
    out: list[tuple[int, str] | None] = []
    for i, (a, b) in enumerate(ranges):
        if i:
            out.append(None)
        out += [(n, lines[n - 1]) for n in range(a, b + 1)]
    return out


def highlight(rows: list[tuple[int, str] | None]) -> tuple[str, str]:
    """(HTML for the code block, CSS for the token classes) using Pygments' monokai style."""
    from pygments import highlight as pyg_highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import PythonLexer
    fmt = HtmlFormatter(style="monokai", nowrap=True)
    css = fmt.get_style_defs(".code")
    out = []
    for r in rows:
        if r is None:
            out.append('<span class="ln"></span><span class="gap">...</span>')
            continue
        n, text = r
        out.append(f'<span class="ln">{n}</span>' + pyg_highlight(text, PythonLexer(), fmt).rstrip("\n"))
    return "\n".join(out), css


def commit_info(rel: str) -> dict:
    """First and last commit dates (ISO) and commit count for a file, following renames."""
    r = subprocess.run(["git", "log", "--follow", "--format=%ad", "--date=short", "--", rel],
                       cwd=ROOT, capture_output=True, text=True, check=True)
    dates = [d for d in r.stdout.split() if d]
    return {"first": dates[-1] if dates else "", "last": dates[0] if dates else "", "n": len(dates)}


def repo_info() -> dict:
    r = subprocess.run(["git", "log", "--format=%ad", "--date=short"], cwd=ROOT, capture_output=True, text=True,
                       check=True)
    dates = [d for d in r.stdout.split() if d]
    return {"first": dates[-1], "last": dates[0], "n": len(dates)}


def nice_date(iso: str) -> str:
    import datetime
    d = datetime.date.fromisoformat(iso)
    return d.strftime("%b ") + str(d.day)


def date_span(info: dict) -> str:
    a, b = nice_date(info["first"]), nice_date(info["last"])
    return f"{a}" if a == b else f"{a} to {b}"


def slide_html(key: str, rel: str, title: str, caption: str, ranges, info: dict) -> str:
    body_html, css = highlight(read_ranges(rel, ranges))
    body = (f'<h1>{html.escape(title)}</h1><div class="cap">{html.escape(caption)}</div>'
            f'<div class="path">{html.escape(rel)}</div><div class="code"><pre>{body_html}</pre></div>'
            f'<div class="foot"><span>Written during the hackathon: first commit <b>{nice_date(info["first"])}</b>, '
            f'last <b>{nice_date(info["last"])}, 2026</b></span><span>{info["n"]} commits</span></div>')
    return PAGE % {"w": W, "h": H, "fs": FONT_PX, "css": css, "body": body}


def tests_html(pytest_line: str, repo: dict) -> str:
    body = (f'<h1>Built this week, and tested</h1>'
            f'<div class="cap">Every module ships with tests that run with no network.</div>'
            f'<div class="code" style="display:flex;flex-direction:column;justify-content:center;font-size:30px">'
            f'<pre><span class="gap">$</span> uv run pytest -q -W ignore -o addopts=""\n\n'
            f'<span style="color:#a6e22e;font-size:44px;font-weight:700">{html.escape(pytest_line)}</span></pre>'
            f'<div style="margin-top:40px;font-size:34px;color:#e6e6ef">Repository history: <b style="color:#ffd166">'
            f'{nice_date(repo["first"])} to {nice_date(repo["last"])}, 2026</b> ({repo["n"]} commits)</div></div>'
            f'<div class="foot"><span>Event window: Oct 26 to Nov 4, 2026</span>'
            f'<span>All tests run offline, with the gateway mocked</span></div>')
    return PAGE % {"w": W, "h": H, "fs": FONT_PX, "css": "", "body": body}


def pan_expr(n: int, hold: float, transit: float, width: int = W) -> str:
    """ffmpeg crop x expression: hold on slide 0, then ease across `n-1` slide boundaries (smoothstep)."""
    terms = []
    for i in range(1, n):
        t0 = i * hold + (i - 1) * transit
        u = f"clip((t-{t0:.3f})/{transit:.3f},0,1)"
        terms.append(f"{width}*({u}*{u}*(3-2*{u}))")
    return "+".join(terms)


def pan_duration(n: int, hold: float, transit: float) -> float:
    return n * hold + (n - 1) * transit


def parse_pytest_summary(text: str) -> str:
    """The last pytest summary line, e.g. '662 passed in 91.20s'. Raises if none found."""
    for line in reversed(text.strip().splitlines()):
        m = re.search(r"(\d+ (?:passed|failed|error)[^=]*)", line)
        if m and re.search(r"\bin \d", line):
            return m.group(1).strip()
    raise ValueError("no pytest summary line found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytest-file", default=str(TMP / "pytest.txt"), help="output of a real pytest run")
    ap.add_argument("--pytest-line", default=None, help="override the summary line")
    ap.add_argument("--hold", type=float, default=2.6)
    ap.add_argument("--transit", type=float, default=1.0)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    line = a.pytest_line or parse_pytest_summary(Path(a.pytest_file).read_text())
    pages: list[tuple[str, str]] = []
    for key, rel, title, cap, ranges, _anchor in EXCERPTS:
        pages.append((f"code_{key}", slide_html(key, rel, title, cap, ranges, commit_info(rel))))
    pages.append(("code_tests", tests_html(line, repo_info())))
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        # architecture diagram, rendered to PNG
        pg.goto((ROOT / "docs" / "architecture.svg").as_uri())
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(OUT / "code_architecture.png"))
        for name, doc in pages:
            f = TMP / f"{name}.html"
            f.write_text(doc)
            pg.goto(f.as_uri())
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(OUT / f"{name}.png"))
        b.close()
    names = ["code_architecture", "code_gateway", "code_tier1", "code_sandbox", "code_tests"]
    from PIL import Image
    strip = Image.new("RGB", (W * len(names), H))
    for i, n in enumerate(names):
        strip.paste(Image.open(OUT / f"{n}.png").convert("RGB").resize((W, H)), (i * W, 0))
    strip.save(TMP / "code_strip.png")
    dur = pan_duration(len(names), a.hold, a.transit)
    crop = f"crop={W}:{H}:x='{pan_expr(len(names), a.hold, a.transit)}':y=0,format=yuv420p"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30",
                    "-i", str(TMP / "code_strip.png"), "-t", f"{dur:.2f}", "-vf", crop,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart",
                    str(OUT / "code.mp4")], check=True)
    print("pytest line:", line, "| code.mp4", f"{dur:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
