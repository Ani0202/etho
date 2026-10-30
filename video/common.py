"""Paths, palette and small helpers shared by the video scripts."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find_data() -> Path:
    env = os.environ.get("ETHO_DATA")
    if env:
        return Path(env)
    for p in [ROOT, *ROOT.parents]:
        if (p / "data" / "showcase").is_dir():
            return p / "data"
    return ROOT / "data"


DATA = _find_data()
OUT = DATA / "video"
SHOWCASE = DATA / "showcase" / "calms21-mouse075-live"
CAPTURE = OUT / "capture"
CARDS = OUT / "cards"
STILLS = OUT / "stills"
MEDIA = ROOT / "docs" / "media"
W, H, FPS = 1920, 1080, 30

# App palette, dark theme (etho/app/static/app.css --b0..--b2; standard behavior slots in etho/app/behaviors.py)
BEHAVIORS = ("attack", "investigation", "mount")
COLORS = {"attack": "#3987e5", "investigation": "#d95926", "mount": "#199e70"}
PAGE, SURFACE, LINE, GRID = "#0d0d0d", "#1a1a19", "#3a3936", "#2e2d2b"
TEXT, TEXT2, MUTED = "#f0efec", "#c3c2b7", "#9a998f"
ACCENT, DANGER, OK, GOLD = "#3987e5", "#ef6a5b", "#3ec28f", "#d9b53a"
FONT = "DejaVu Sans"
CREDIT = "Video: CalMS21 (Sun et al. 2021), CC BY 4.0"
CREDIT_CARD = "CalMS21, Sun et al. 2021, CC BY 4.0"


def hex2bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def chromium() -> str | None:
    for p in sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome")):
        return str(p)
    return shutil.which("chromium") or shutil.which("google-chrome")


def have_render_tools() -> bool:
    return bool(chromium() and shutil.which("ffmpeg"))


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def html_to_png(html: str, out: Path, w: int = W, h: int = H, transparent: bool = False) -> Path:
    """Render an HTML string to a PNG with headless Chromium (no Python imaging library needed)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_suffix(".html")
    src.write_text(html, encoding="utf-8")
    cmd = [chromium() or "chromium", "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=1", f"--window-size={w},{h}", "--virtual-time-budget=2000",
           f"--screenshot={out}"]
    if transparent:
        cmd.append("--default-background-color=00000000")
    cmd.append(src.as_uri())
    subprocess.run(cmd, check=True, capture_output=True, timeout=90)
    src.unlink(missing_ok=True)
    return out
