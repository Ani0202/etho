"""Screenshot a URL of the running app in light and dark mode.

  uv run --with playwright python scripts/screenshot_app.py http://127.0.0.1:8765/ out.png \
      [--wait CSS_SELECTOR] [--js "document.querySelector('...').click()"] [--width 1180]

Writes out_light.png and out_dark.png (full page). Prints console errors, so layout and script bugs show up.
"""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("url")
ap.add_argument("out", type=Path)
ap.add_argument("--wait", default=None)
ap.add_argument("--js", action="append", default=[], help="JS to run after load (repeatable)")
ap.add_argument("--width", type=int, default=1180)
ap.add_argument("--height", type=int, default=900)
ap.add_argument("--pause", type=float, default=0.8)
a = ap.parse_args()

with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": a.width, "height": a.height}, color_scheme=scheme)
        errs = []
        pg.on("console", lambda m, errs=errs: errs.append(m.text) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e, errs=errs: errs.append(f"pageerror: {e}"))
        pg.goto(a.url, wait_until="networkidle")
        if a.wait:
            pg.wait_for_selector(a.wait, timeout=15000)
        for js in a.js:
            pg.evaluate(js)
            pg.wait_for_timeout(int(a.pause * 1000))
        pg.wait_for_timeout(int(a.pause * 1000))
        path = a.out.with_name(f"{a.out.stem}_{scheme}{a.out.suffix}")
        pg.screenshot(path=str(path), full_page=True)
        print(path, "console:", errs or "clean")
    b.close()
