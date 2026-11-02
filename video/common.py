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
# The Nov 2 live run of the same 0-300 s clip (job j46d77dd8aee9, scored by eval/showcase_check.py).
LIVE_JOB = DATA / "app_nov2" / "jobs" / "j46d77dd8aee9"
# Which run the results beat and the score badge show: "live" (the Nov 2 live run) or "showcase" (the Oct 30 run).
# The opening shot always shows the showcase run, and every expert label comes from the showcase package (same clip).
SOURCE = os.environ.get("ETHO_VIDEO_SOURCE", "live")
if SOURCE not in ("live", "showcase"):
    raise ValueError(f"ETHO_VIDEO_SOURCE must be 'live' or 'showcase', not {SOURCE!r}")
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


def _truncated(png: Path, transparent: bool) -> bool:
    """Headless Chromium occasionally paints only the top ~1017 px and leaves a white band at the bottom (seen about
    one run in three on the who card). Detect it: the last row is pure white while the row 64 px higher is not."""
    if transparent:
        return False
    import cv2
    g = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    return g is not None and g.shape[0] > 128 and bool((g[-1] == 255).all()) and not bool((g[-64] == 255).all())


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
    for _ in range(4):
        subprocess.run(cmd, check=True, capture_output=True, timeout=90)
        if not _truncated(out, transparent):
            break
    else:
        raise RuntimeError(f"headless Chromium kept truncating the screenshot: {out}")
    src.unlink(missing_ok=True)
    return out
