"""Record Etho's live run (one real job) for the demo video, headlessly. Reuses the helpers in capture_app.py.

Flow, recorded continuously from the home page: the dataset authors' definitions are pasted into the upload form
(clear the field, paste from the clipboard), the video is chosen through the file chooser, Score is clicked, the whole
run is shown with its progress and clock, then the planner card, the overlay video playing for ~10 s, and the
per-stage timing and cost card.

    # rehearsal against the mock server (spends nothing):
    uv run python -m tests.mock_server --port 8766 --seconds 40
    data/video/tmp/pwvenv/bin/python video/capture/capture_live.py --base http://127.0.0.1:8766 \
        --video <path printed by the mock server> --out /tmp/rehearsal --lock /tmp/rehearsal/lock.json

    # the real run (submits ONE paid job; refuses to run twice because of the lock file):
    data/video/tmp/pwvenv/bin/python video/capture/capture_live.py --base http://127.0.0.1:8770 \
        --video data/demo/mouse075_0-300s_h264.mp4 --real

Outputs in --out: live_run_raw.mp4 (1920x1080, 30 fps, H.264), live_run_planner.png, live_run_timing.png.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MAIN = Path("/home/ubuntu/work/etho")
DEFS = ROOT / "configs" / "calms21_definitions.json"
RUN_TIMEOUT_S = 25 * 60


def load_definitions(path: Path = DEFS) -> dict:
    """The dataset authors' definitions exactly as stored: {'subject', 'partner', 'behaviors': [(name, text), ...]}."""
    d = json.loads(Path(path).read_text())
    return {"subject": d["subject"], "partner": d["partner"], "behaviors": list(d["behaviors"].items())}


def job_id_from_url(url: str) -> str | None:
    m = re.search(r"/p/([^/?#]+)", url)
    return m.group(1) if m else None


def claim_lock(lock: Path, base: str) -> None:
    """Refuse to submit a second job. The lock is written BEFORE Score is clicked, so a crash after the click cannot
    lead to a silent re-run either."""
    lock = Path(lock)
    if lock.exists():
        raise SystemExit(f"refusing to run: {lock} exists, a job was already submitted from this script")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"base": base, "claimed_at": time.time(), "job_id": None}))


def record_job_id(lock: Path, job_id: str, url: str) -> None:
    d = json.loads(Path(lock).read_text())
    d.update(job_id=job_id, url=url)
    Path(lock).write_text(json.dumps(d))


def form_matches(values: dict, want: dict) -> list[str]:
    """Differences between what the form holds and what was supposed to be pasted (empty list = identical)."""
    bad = []
    if values["subject"] != want["subject"]:
        bad.append("subject")
    if values["partner"] != want["partner"]:
        bad.append("partner")
    if [tuple(b) for b in values["behaviors"]] != [tuple(b) for b in want["behaviors"]]:
        bad.append("behaviors")
    return bad


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default=str(MAIN / "data" / "video" / "capture"))
    ap.add_argument("--lock", default=None, help="lock file; default data/video/tmp/live_run_submitted.json in the main checkout")
    ap.add_argument("--real", action="store_true", help="required for any base other than a local mock port")
    a = ap.parse_args(argv)
    if a.base.rstrip("/").endswith(":8770") and not a.real:
        raise SystemExit("--real is required for :8770")
    video = Path(a.video).resolve()
    if not video.exists():
        raise SystemExit(f"no such video: {video}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    lock = Path(a.lock) if a.lock else MAIN / "data" / "video" / "tmp" / "live_run_submitted.json"
    want = load_definitions()

    os.environ["ETHO_CAPTURE_OUT"] = str(out)
    sys.path.insert(0, str(HERE))
    import capture_app as ca
    from playwright.sync_api import sync_playwright
    ca.BASE = a.base.rstrip("/")
    ca.TMP = out / "tmp"
    ca.TMP.mkdir(parents=True, exist_ok=True)

    def center_idx(pg, sel, i=0):
        return pg.evaluate("""([sel, i]) => { const e = document.querySelectorAll(sel)[i];
            const b = e.getBoundingClientRect(); return [b.x + b.width / 2, b.y + Math.min(b.height / 2, 40)]; }""", [sel, i])

    def top_idx(pg, sel, i=0):
        return pg.evaluate("([sel, i]) => document.querySelectorAll(sel)[i].getBoundingClientRect().top + scrollY", [sel, i])

    def card_png(pg, selector, name):
        """PNG of one card (cursor hidden), clipped from the full page so a card taller than the viewport is whole."""
        box = pg.evaluate("""sel => { const b = document.querySelector(sel).getBoundingClientRect();
            return {x: Math.max(0, b.x - 8), y: Math.max(0, b.top + scrollY - 8), width: b.width + 16, height: b.height + 16}; }""", selector)
        pg.evaluate("document.getElementById('__cur') && (document.getElementById('__cur').style.visibility = 'hidden')")
        pg.screenshot(path=str(out / name), clip=box, full_page=True)
        pg.evaluate("document.getElementById('__cur') && (document.getElementById('__cur').style.visibility = 'visible')")

    with sync_playwright() as pw:
        s = ca.Session(pw, "live_run_raw")
        pg = s.page
        s.ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=ca.BASE)
        submitted = False
        job = None
        try:
            s.goto("/")
            pg.wait_for_selector("#form:not([hidden])", timeout=20000)
            s.t_start()
            s.wait(2200)
            log("home page up")

            def reveal(sel, i=0):
                """Scroll so the element sits in the upper-middle of the viewport."""
                s.scroll_to(top_idx(pg, sel, i) - 300, 900)
                s.wait(950)

            def paste(sel, i, text):
                reveal(sel, i)
                x, y = center_idx(pg, sel, i)
                s.move_to(x, y, steps=25)
                pg.mouse.click(x, y)
                s.wait(250)
                pg.keyboard.press("Control+a")
                pg.keyboard.press("Delete")
                s.wait(650)                                   # the emptied field is visible
                pg.evaluate("t => navigator.clipboard.writeText(t)", text)
                pg.keyboard.press("Control+v")
                s.wait(1100)                                  # the pasted text is visible

            s.scroll_to(top_idx(pg, "#form") - 40, 1400)
            s.wait(1200)
            paste("#subject", 0, want["subject"])
            paste("#partner", 0, want["partner"])
            for i, (name, text) in enumerate(want["behaviors"]):
                paste(".beh input", i, name)
                paste(".beh textarea", i, text)
            log("definitions pasted")

            got = pg.evaluate("""() => ({subject: document.getElementById('subject').value,
                partner: document.getElementById('partner').value,
                behaviors: [...document.querySelectorAll('.beh')].map(r => [r.querySelector('input').value, r.querySelector('textarea').value])})""")
            bad = form_matches(got, want)
            if bad:
                raise RuntimeError(f"form does not match the definitions file before submit: {bad}; nothing submitted")

            # upload through the real file chooser
            s.scroll_to(top_idx(pg, "#drop") - 220, 1200)
            s.wait(900)
            x, y = center_idx(pg, "#drop")
            s.move_to(x, y, steps=25)
            s.wait(400)
            with pg.expect_file_chooser() as fc:
                pg.mouse.click(x, y)
            fc.value.set_files(str(video))
            pg.wait_for_function("() => /MB, \\S/.test(document.getElementById('picked').textContent)", timeout=60000)
            s.wait(2500)                                      # file name, size, duration, cost estimate
            log("file chosen:", pg.inner_text("#picked"))

            # Score
            s.scroll_to(top_idx(pg, "#go") - 600, 1100)
            s.wait(900)
            x, y = center_idx(pg, "#go")
            s.move_to(x, y, steps=25)
            s.wait(900)
            claim_lock(lock, ca.BASE)
            log("clicking Score (ONE submit)")
            submitted = True
            pg.mouse.click(x, y)
            pg.wait_for_url(re.compile(r"/p/[^/?#]+"), timeout=180000)
            job = job_id_from_url(pg.url)
            record_job_id(lock, job, pg.url.split("?")[0])
            log("JOB", job, pg.url)
            pg.add_style_tag(content=f"html{{zoom:{ca.ZOOM}}}")

            # the whole run: progress and clock, until the results page replaces it
            t_run = time.time()
            last = ""
            outcome = None
            while time.time() - t_run < RUN_TIMEOUT_S:
                st = pg.evaluate("""() => ({planner: !!document.getElementById('planner'),
                    h1: (document.querySelector('h1') || {}).textContent || '',
                    status: (document.querySelector('.status-big') || {}).textContent || '',
                    clock: (document.querySelector('.clock') || {}).textContent || ''})""")
                if st["planner"]:
                    outcome = "done"
                    break
                if re.search(r"failed|cancelled|Not available", st["h1"], re.I):
                    outcome = "failed"
                    break
                line = f"{st['clock']} {st['status']}"
                if line != last and (int(time.time() - t_run) % 5 == 0):
                    log("progress:", line)
                    last = line
                s.wait(1000)
            log("outcome:", outcome)
            if outcome != "done":
                s.wait(4000)
                s.shot("live_run_failure.png")
                return job

            s.wait(2500)
            pg.evaluate("scrollTo(0,0)")
            s.wait(1800)                                      # header and KPI tiles
            s.scroll_to(s.card_y("Perception plan"), 1600)
            s.wait(5500)
            card_png(pg, "#planner", "live_run_planner.png")
            s.scroll_to(s.card_y("Ethogram synced") - 10, 1600)
            s.wait(500)
            try:
                pg.wait_for_function("() => document.querySelector('video').readyState >= 3", timeout=30000)
            except Exception:
                log("overlay video not ready after 30 s; playing anyway")
            vx, vy = pg.evaluate("(() => { const b = document.querySelector('video').getBoundingClientRect(); "
                                 "return [b.x + 60, b.y + b.height - 20]; })()")
            s.move_to(vx, vy)
            s.wait(300)
            pg.evaluate("document.querySelector('video').play()")
            s.wait(10500)                                     # overlay playing ~10 s
            s.scroll_to(s.card_y("Per-stage timing", margin=24), 1600)
            s.wait(2500)
            card_png(pg, ".stagecard", "live_run_timing.png")
            s.wait(4500)
            pg.evaluate("document.querySelector('video').pause()")
            facts = pg.evaluate("""() => [...document.querySelectorAll('.tile')].map(t => t.innerText.replace(/\\n+/g, ' | '))""")
            log("TILES", json.dumps(facts))
            return job
        except Exception:
            log("ERROR", "submitted=" + str(submitted), "job=" + str(job))
            traceback.print_exc()
            try:
                s.shot("live_run_error.png")
            except Exception:
                pass
            return job
        finally:
            try:
                p = s.finish(trim_end=0.2)
                log("recording ->", p)
            except Exception:
                traceback.print_exc()


if __name__ == "__main__":
    main()
