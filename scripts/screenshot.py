"""Screenshot an HTML file in light and dark mode.
Usage: uv run --with playwright python scripts/screenshot.py <html> <out.png>"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 1180, "height": 900}, color_scheme=scheme)
        pg.goto(src.as_uri())
        pg.screenshot(path=str(out.with_name(f"{out.stem}_{scheme}{out.suffix}")), full_page=True)
    b.close()
