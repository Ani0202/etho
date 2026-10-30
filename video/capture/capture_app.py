"""Record screen-capture clips of the local read-only Etho app for the demo rough cut.

No model calls: the app runs with ETHO_APP_READ_ONLY=1 (video/capture/serve.sh). Needs Playwright (any Python with
`playwright` and the Chromium in ~/.cache/ms-playwright), ffmpeg, and a running app on 127.0.0.1:8795.

    python video/capture/capture_app.py [live_run trace correction stills]

Outputs go to data/video/capture/ (gitignored): <beat>.mp4 (1920x1080, 30 fps, H.264) and <beat>.png.
The UI is shown in presentation mode at 1.4x CSS zoom so it reads on a shared screen.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os
OUT = Path(os.environ.get("ETHO_CAPTURE_OUT") or ROOT / "data" / "video" / "capture")
TMP = ROOT / "data" / "video" / "tmp"
BASE = "http://127.0.0.1:8795"
PID = "sc-calms21-mouse075-live"
W, H = 1920, 1080
ZOOM = 1.4   # 1.5 clips the planner table's last column; presentation mode already enlarges the type

# A visible cursor (Playwright's recordings have none) that pulses on click.
CURSOR_JS = """
(() => {
  const z = %(zoom)s;
  const d = document.createElement('div');
  d.id = '__cur';
  d.style.cssText = 'position:fixed;left:0;top:0;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%%;' +
    'background:rgba(255,200,0,.55);border:3px solid #fff;box-shadow:0 0 0 2px #222;z-index:2147483647;pointer-events:none;' +
    'transition:transform .12s;display:none';
  document.addEventListener('DOMContentLoaded', () => document.documentElement.appendChild(d));
  addEventListener('mousemove', e => { d.style.display = 'block'; d.style.left = (e.clientX / z) + 'px'; d.style.top = (e.clientY / z) + 'px'; }, true);
  addEventListener('mousedown', () => { d.style.transform = 'scale(.6)'; }, true);
  addEventListener('mouseup', () => { d.style.transform = 'scale(1)'; }, true);
})();
"""

SMOOTH_JS = """
([target, ms]) => new Promise(res => {
  const y0 = window.scrollY, t0 = performance.now();
  const max = document.documentElement.scrollHeight - innerHeight;
  const y1 = Math.max(0, Math.min(max, target));
  const ease = t => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
  (function step(now) {
    const t = Math.min(1, (now - t0) / ms);
    scrollTo(0, y0 + (y1 - y0) * ease(t));
    t < 1 ? requestAnimationFrame(step) : res(window.scrollY);
  })(t0);
})
"""


class Session:
    """One recorded browser context. t_start() marks where the useful part of the recording begins."""

    def __init__(self, pw, name: str):
        self.name = name
        self.dir = TMP / f"rec_{name}"
        for f in self.dir.glob("*.webm") if self.dir.exists() else []:
            f.unlink()
        self.browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        self.ctx = self.browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                            record_video_dir=str(self.dir), record_video_size={"width": W, "height": H})
        self.ctx.add_init_script(CURSOR_JS % {"zoom": ZOOM})
        self.t_ctx = time.time()
        self.page = self.ctx.new_page()
        self.t0 = 0.0

    def goto(self, path: str = f"/p/{PID}"):
        pg = self.page
        pg.goto(f"{BASE}{path}{'&' if '?' in path else '?'}present=1")
        pg.add_style_tag(content=f"html{{zoom:{ZOOM}}}")
        pg.wait_for_selector("h2")
        pg.wait_for_timeout(2500)
        pg.evaluate("scrollTo(0,0)")

    def t_start(self):
        self.t0 = time.time() - self.t_ctx

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def card_y(self, h2_text: str, margin: int = 90) -> float:
        return self.page.evaluate(
            """([t, m]) => { const h = [...document.querySelectorAll('h2,h3')].find(e => e.textContent.includes(t));
               const c = h.closest('.card') || h; return c.getBoundingClientRect().top + scrollY - m; }""", [h2_text, margin])

    def scroll_to(self, y: float, ms: int = 1400):
        self.page.evaluate(SMOOTH_JS, [y, ms])

    def move_to(self, x: float, y: float, steps: int = 30):
        self.page.mouse.move(x, y, steps=steps)

    def shot(self, name: str):
        """Hero PNG without the capture cursor."""
        self.page.evaluate("document.getElementById('__cur') && (document.getElementById('__cur').style.visibility = 'hidden')")
        self.page.screenshot(path=str(OUT / name))
        self.page.evaluate("document.getElementById('__cur') && (document.getElementById('__cur').style.visibility = 'visible')")

    def finish(self, trim_end: float = 0.0, hero: Path | None = None) -> Path:
        t1 = time.time() - self.t_ctx
        self.page.close()
        vid = self.page.video.path()
        self.ctx.close()
        self.browser.close()
        out = OUT / f"{self.name}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{self.t0:.2f}", "-i", str(vid),
               "-t", f"{t1 - self.t0 - trim_end:.2f}", "-vf", f"fps=30,scale={W}:{H}", "-c:v", "libx264", "-preset", "medium",
               "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(out)]
        subprocess.run(cmd, check=True)
        return out


def bout_center(page, label: str):
    """Centre (x, y) in viewport px of the ethogram bout whose aria-label equals `label`, scrolling it into view first."""
    return page.evaluate(
        """l => { const r = [...document.querySelectorAll('rect.bout')].find(e => e.getAttribute('aria-label') === l);
                  const b = r.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; }""", label)


def center_of(page, selector: str, text: str | None = None):
    """Viewport centre of an element via getBoundingClientRect (Locator.bounding_box ignores the CSS zoom)."""
    return page.evaluate(
        """([sel, t]) => { const e = [...document.querySelectorAll(sel)].find(x => !t || x.textContent.trim() === t);
                          const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; }""", [selector, text])


TRACED_BOUT = "Etho: mount 37.5 to 38.0 seconds"   # the traced error: expert attack; frame check said "attack" (0.96), set aside by the restriction


def live_run(pw):
    """Stand-in for the live run (read-only mode cannot run a job): planner card, timing/cost card, overlay playing."""
    s = Session(pw, "live_run_standin")
    s.goto()
    pg = s.page
    s.t_start()
    s.wait(1800)                                   # header, KPI tiles
    s.scroll_to(s.card_y("Perception plan"), 1600)
    s.wait(5500)                                   # planner card: phrases proposed, the one SAM confirmed
    s.shot("live_run.png")
    s.scroll_to(s.card_y("Ethogram synced") - 10, 1600)
    s.wait(500)
    vx, vy = pg.evaluate("(() => { const b = document.querySelector('video').getBoundingClientRect(); return [b.x + 60, b.y + b.height - 20]; })()")
    s.move_to(vx, vy)
    s.wait(300)
    pg.evaluate("document.querySelector('video').play()")   # a real click on the native controls proved unreliable
    s.wait(7000)                                   # overlay video playing, ethogram head moving
    s.scroll_to(s.card_y("Per-stage timing"), 1600)
    s.wait(6500)                                   # per-stage timing and cost
    pg.evaluate("document.querySelector('video').pause()")
    out = s.finish(trim_end=0.2)
    return out


def abs_top(page, selector: str, text: str | None = None) -> float:
    return page.evaluate(
        """([sel, t]) => { const e = [...document.querySelectorAll(sel)].find(x => !t || x.textContent.includes(t));
                          return e.getBoundingClientRect().top + scrollY; }""", [selector, text])


RING_JS = """
([x, y, w, h]) => { const d = document.createElement('div'); d.id = '__ring';
  d.style.cssText = `position:fixed;left:${(x - 14) / %(zoom)s}px;top:${(y - 14) / %(zoom)s}px;width:${(w + 28) / %(zoom)s}px;height:${(h + 28) / %(zoom)s}px;` +
    'border:4px solid #e11d48;border-radius:12px;box-shadow:0 0 0 6px rgba(225,29,72,.25);z-index:2147483646;pointer-events:none';
  document.documentElement.appendChild(d); }
"""


def trace(pw):
    """The 37.5-38.0 s Etho mount bout: expert says attack, tier 1 said attack (0.96), the restriction set it aside."""
    s = Session(pw, "trace")
    s.goto()
    pg = s.page
    s.t_start()
    s.wait(600)
    s.scroll_to(abs_top(pg, "svg") - 150, 1700)    # ethogram: Etho rows over the expert rows
    s.wait(2500)
    x, y = bout_center(pg, TRACED_BOUT)
    box = pg.evaluate("""l => { const r = [...document.querySelectorAll('rect.bout')].find(e => e.getAttribute('aria-label') === l).getBoundingClientRect();
                              return [r.x, r.y, r.width, r.height]; }""", TRACED_BOUT)
    pg.evaluate(RING_JS % {"zoom": ZOOM}, box)     # capture-only ring: the bout is only ~14 px wide
    s.wait(1500)
    s.move_to(x, y)
    s.wait(700)
    pg.mouse.click(x, y)                           # opens the trace panel; the video seeks to the bout
    s.wait(1200)
    s.scroll_to(abs_top(pg, "svg") - 150, 900)     # the click scrolls to the trace; come back to the ethogram
    pg.mouse.move(x + 400, y + 300, steps=10)
    s.wait(1500)
    s.shot("trace.png")                            # hero 1: ethogram with the expert row, bout ringed
    pg.evaluate("document.getElementById('__ring').remove()")
    s.scroll_to(abs_top(pg, ".trace") - 90, 1700)
    s.wait(1500)
    s.shot("trace_panel.png")                      # hero 2: the set-aside verdict, then "Decided by"
    s.wait(5500)
    return s.finish()


def correction(pw):
    s = Session(pw, "correction")
    s.goto()
    pg = s.page
    s.t_start()
    s.wait(500)
    s.scroll_to(s.card_y("Ethogram synced") + 250, 1500)
    s.wait(1200)
    x, y = bout_center(pg, TRACED_BOUT)
    s.move_to(x, y)
    s.wait(500)
    pg.mouse.click(x, y)
    s.wait(900)
    s.scroll_to(s.card_y("Correct a few bouts", margin=60), 1700)
    s.wait(1200)
    sel = pg.locator("#selpanel select")
    sel.select_option("attack")                    # "this bout is actually: attack"
    s.wait(900)
    bx, by = center_of(pg, "#selpanel button", "Mark wrong")
    s.move_to(bx, by)
    s.wait(500)
    pg.mouse.click(bx, by)                         # adds to the pending list
    s.wait(1500)
    s.scroll_to(s.card_y("Correct a few bouts", margin=40), 600)
    s.move_to(*center_of(pg, "#revbtn"))           # hover the disabled "Revise rules"
    s.wait(4500)
    s.shot("correction.png")
    s.wait(800)
    return s.finish()


def stills(pw):
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": W, "height": H})
    for name, path in (("home", "/"), ("showcase_page", f"/p/{PID}")):
        pg = ctx.new_page()
        pg.goto(f"{BASE}{path}?present=1")
        pg.add_style_tag(content=f"html{{zoom:{ZOOM}}}")
        pg.wait_for_selector("h2, h1")
        pg.wait_for_timeout(2500)
        pg.screenshot(path=str(OUT / f"still_{name}.png"))
        pg.close()
    b.close()


BEATS = {"live_run": live_run, "trace": trace, "correction": correction, "stills": stills}


def main(argv):
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    names = argv or list(BEATS)
    with sync_playwright() as pw:
        for n in names:
            r = BEATS[n](pw)
            print(n, "->", r)
    live = OUT / "live_run_standin.mp4"
    if live.exists():                              # the assembler reads live_run.mp4
        (OUT / "live_run.mp4").write_bytes(live.read_bytes())


if __name__ == "__main__":
    main(sys.argv[1:])
