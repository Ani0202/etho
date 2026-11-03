"""Title, who-it's-for, evidence and close cards (1920x1080 PNG + fading mp4) and the results-beat score badge.

Every number comes from `video.numbers.Numbers`; the card text is generated from it and tested for traceability.
Layout rule: content stays above y=860 so the burned-in captions (bottom 200 px) never cover it.
Run: python -m video.cards   (prints the source map)
"""
from __future__ import annotations

import html as H
import re
import sys

import cv2

from video import common as C
from video import draw
from video.data import showcase
from video.numbers import Numbers

SAFE_BOTTOM = 860

CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:1920px;height:1080px;overflow:hidden}}
body{{background:radial-gradient(1400px 900px at 18% 8%,#1b1c20 0%,{C.PAGE} 70%);color:{C.TEXT};
 font-family:'{C.FONT}',sans-serif;position:relative}}
.abs{{position:absolute}}
.brand{{position:absolute;left:80px;top:46px;font-weight:700;font-size:44px;letter-spacing:.08em;color:{C.TEXT2}}}
.brand i{{display:inline-block;width:18px;height:18px;border-radius:5px;background:{C.ACCENT};
 margin-right:16px;vertical-align:middle}}
.h1{{font-weight:700;font-size:78px;line-height:1.1}}
.body{{font-size:48px;line-height:1.3;color:{C.TEXT2}}}
.muted{{color:{C.MUTED}}}
.foot{{font-size:34px;line-height:1.25;color:{C.MUTED}}}
.sw{{display:inline-block;width:34px;height:34px;border-radius:7px;margin-right:16px;vertical-align:-3px}}
.tile{{position:absolute;background:{C.SURFACE};border:2px solid {C.LINE};border-radius:26px;padding:30px 40px}}
sup{{font-size:.55em;vertical-align:.6em}}
"""


def page(body: str) -> str:
    return f"<!doctype html><meta charset=utf-8><style>{CSS}</style><body>{body}</body>"


def sw(b: str) -> str:
    return f"<span class=sw style='background:{C.COLORS[b]}'></span>"


# ------------------------------------------------------------------------------------------------------ title
def title_html(N: Numbers) -> str:
    head = H.escape(N.s("headline_text").replace("Training-free: ", "", 1))
    foot = H.escape(N.s("footnote_text"))
    return page(f"""
<div class=brand><i></i>ETHO</div>
<div class=abs style='left:80px;top:100px;font-weight:700;font-size:240px;line-height:1;
letter-spacing:-.02em'>Etho</div>
<div class=abs style='left:92px;top:350px;font-weight:700;font-size:84px;color:{C.ACCENT}'>Push the Frontier</div>
<div class=abs style='left:92px;top:470px;width:1700px;font-size:52px;line-height:1.25;color:{C.TEXT}'>
 Zero-shot animal behavior scoring: write the behaviors in plain English, get a scored ethogram.</div>
<div class=abs style='left:92px;top:620px;width:1700px;font-size:44px;line-height:1.3;color:{C.TEXT2}'>
 <b style='color:{C.TEXT}'>Training-free</b>&sup1;: {head}</div>
<div class=abs style='left:92px;top:745px;width:1700px' ><div class=foot>&sup1; {foot}</div></div>
""")


# ------------------------------------------------------------------------------------------------------ who
WHO_ROWS = dict(x0=360, x1=1820, y0=320, row_h=80, gap=36)


def who_rows() -> draw.Rows:
    return draw.Rows(fps=showcase()["fps"], **WHO_ROWS)


def who_html(N: Numbers) -> str:
    sc = showcase()
    r = who_rows()
    labels = "".join(
        f"<div class=abs style='left:40px;width:300px;text-align:right;top:{r.y(i) + 12}px;"
        f"font-size:44px;color:{C.TEXT}'>{b}</div>"
        for i, b in enumerate(r.names))
    ticks = "".join(
        f"<div class=abs style='left:{int(r.x_of(t, (0, sc['dur']))) - 80}px;width:160px;text-align:center;"
        f"top:{r.bottom + 22}px;"
        f"font-size:36px;color:{C.MUTED}'>{draw.fmt_clock(t)}</div>" for t in range(0, int(sc["dur"]) + 1, 60))
    return page(f"""
<div class=brand><i></i>ETHO</div>
<div class=abs style='left:80px;top:130px;width:1760px'>
 <div class=h1>Behavior is still scored by hand.</div>
 <div class=body style='margin-top:18px'>A human expert's labels for one clip, one row per behavior:</div></div>
{labels}{ticks}
<div class=abs style='left:80px;top:{r.bottom + 100}px;width:1760px;font-size:44px;color:{C.TEXT}'>
 Etho needs one sentence per behavior.</div>
<div class=abs style='left:80px;top:{r.bottom + 160}px;font-size:36px;color:{C.MUTED}'>{H.escape(C.CREDIT_CARD)}</div>
""")


def who_png(N: Numbers, out) -> None:
    sc = showcase()
    base = C.html_to_png(who_html(N), out)
    img = draw.read_image(base)[..., :3].copy()
    r = who_rows()
    view = (0.0, sc["dur"])
    r.gridlines(img, [float(t) for t in range(0, int(sc["dur"]) + 1, 60)], view)
    r.draw(img, sc["expert"], view)
    cv2.imwrite(str(out), img)


# ------------------------------------------------------------------------------------------------------ badge
def badge_html(N: Numbers) -> str:
    clip, allv = N.s("clip_macro"), N.s("macro_f1")
    n = N.s("n_test_videos")
    per = " &middot; ".join(f"{b} {N.s('f1_' + b)}" for b in C.BEHAVIORS)
    return (f"<div style='position:absolute;box-sizing:border-box;left:0;top:0;width:1560px;height:190px;"
            f"border-radius:34px;"
            f"background:rgba(20,20,19,.94);border:3px solid {C.LINE};padding:22px 44px'>"
            f"<div style='font-size:62px;font-weight:700;white-space:nowrap'>"
            f"this clip <span style='color:{C.GOLD}'>{clip}</span>"
            f" <span style='color:{C.MUTED}'>&middot;</span> all {n} test videos "
            f"<span style='color:{C.ACCENT}'>{allv}</span></div>"
            f"<div style='font-size:40px;color:{C.TEXT2};margin-top:14px;white-space:nowrap'>"
            f"all {n}, per behavior: {per}</div></div>")


def badge_png(N: Numbers, out) -> None:
    body = badge_html(N)
    css = "html,body{margin:0;background:transparent}"
    C.html_to_png(f"<!doctype html><meta charset=utf-8><style>{css}"
                  f"body{{font-family:'{C.FONT}',sans-serif;color:{C.TEXT}}}</style><body>{body}</body>",
                  out, 1560, 190, transparent=True)


# ------------------------------------------------------------------------------------------------------ evidence
def evidence_html(N: Numbers) -> str:
    foot = H.escape(N.s("footnote_text"))
    chips = "".join(
        f"<div style='display:flex;justify-content:space-between;font-size:44px;line-height:68px;white-space:nowrap'>"
        f"<span>{sw(b)}<span style='color:{C.TEXT2}'>{b}</span></span>"
        f"<b style='margin-left:36px'>{N.s('f1_' + b)}</b></div>"
        for b in C.BEHAVIORS)
    col = "flex:1;padding:0 20px"
    lab = f"font-size:44px;color:{C.TEXT2};line-height:1.25"
    big = "font-size:92px;font-weight:700;line-height:1.1"
    sub = f"font-size:44px;color:{C.TEXT2};line-height:1.25"
    return page(f"""
<div class=brand style='top:30px'><i></i>ETHO</div>
<div class=tile id=tEtho style='left:60px;top:96px;width:1190px;height:360px;padding-top:22px'>
 <div style='{lab}'>Etho, macro F1, no training&sup1;</div>
 <div style='display:flex;align-items:center;gap:30px;margin-top:20px'>
  <div style='font-size:200px;font-weight:700;line-height:1;color:{C.ACCENT}'>{N.s('macro_f1')}</div>
  <div style='flex:1'>{chips}</div></div></div>
<div class=tile id=tSup style='left:1280px;top:96px;width:580px;height:360px;padding-top:22px'>
 <div style='{lab}'>Supervised, trained on labeled data and pose keypoints</div>
 <div style='font-size:86px;font-weight:700;line-height:1.1;margin-top:16px;color:{C.MUTED};
white-space:nowrap'>{N.s('sup_lo')}&ndash;{N.s('sup_hi')}</div>
 <div style='{sub};color:{C.MUTED};white-space:nowrap'>{N.s('sup_1dconv')} &middot; {N.s('sup_taskprog')} &middot;
{N.s('sup_mabe_top1')}</div></div>
<div class=tile id=tRun style='left:60px;top:472px;width:1800px;height:316px;display:flex;padding:20px 20px'>
 <div style='{col}'><div style='{lab}'>Etho speed</div><div style='{big}'>{N.s('speed_pooled')}&times;</div>
  <div style='{sub}'>real time (worst {N.s('speed_worst')}&times;)</div></div>
 <div style='{col}'><div style='{lab}'>Manual scoring takes</div>
  <div style='{big};color:{C.MUTED}'>{N.s('manual_lo')}&ndash;{N.s('manual_hi')}&times;
  </div>
  <div style='{sub}'>the video's length (Segalin et al. 2021)</div></div>
 <div style='{col}'><div style='{lab}'>Model cost</div><div style='{big}'>{N.s('cost_per_hour')}</div>
  <div style='{sub}'>per hour of video</div></div></div>
<div class=abs style='left:60px;top:804px;width:1800px;font-size:34px;color:{C.MUTED};
line-height:1.2'>&sup1; {foot}</div>
""")



EVIDENCE_TILES = ("tEtho", "tSup", "tRun")


def evidence_focus_html(N: Numbers, focus: str) -> str:
    """The evidence card with one tile in focus and the other two dimmed, so the eye follows the voice-over
    (accuracy, then supervised baselines, then speed and cost). Same text and numbers as `evidence_html`."""
    dim = ",".join(f"#{t}" for t in EVIDENCE_TILES if t != focus)
    return evidence_html(N).replace("</body>", f"<style>{dim}{{opacity:.32}}</style></body>", 1)

# ------------------------------------------------------------------------------------------------------ close
def close_html(N: Numbers) -> str:
    def chip(name, role):
        return (f"<div style='flex:1;vertical-align:top;background:{C.SURFACE};"
                f"border:2px solid {C.LINE};border-radius:26px;padding:26px 34px'>"
                f"<div style='font-size:60px;font-weight:700'>{name}</div>"
                f"<div style='font-size:44px;color:{C.TEXT2};margin-top:6px'>{role}</div></div>")
    ph = ("display:inline-block;margin-right:40px;border:3px dashed {c};border-radius:22px;padding:18px 34px;"
          "font-size:44px;color:{c}").format(c=C.GOLD)
    return page(f"""
<div class=brand><i></i>ETHO</div>
<div class=abs style='left:80px;top:130px;font-size:52px;color:{C.TEXT2}'>Etho runs on Meta's models</div>
<div class=abs style='left:80px;top:215px;width:1760px;display:flex;gap:40px'>
 {chip('SAM 3.1', 'tracks both animals')}{chip('Muse Spark', 'plans, writes the rules')}
 {chip('Muse Spark Open', 'checks borderline moments')}</div>
<div class=abs style='left:80px;top:450px;width:1760px;font-size:76px;font-weight:700;line-height:1.15'>
 For every lab that still scores behavior by hand.</div>
<div class=abs style='left:80px;top:650px;white-space:nowrap'>
 <span style='{ph}'>{H.escape(C.REPO_URL_SHOWN)}</span><span
 style='{ph}'>{H.escape(C.LIVE_URL_SHOWN)}</span></div>
<div class=abs style='left:80px;top:790px;font-size:36px;color:{C.MUTED}'>Open source, Apache-2.0. Benchmark data:
 {H.escape(C.CREDIT_CARD)}.</div>
""")


# ------------------------------------------------------------------------------------------------ architecture
ARCH_SCALE = 0.79    # 1080 * 0.79 = 853 px: the diagram's bottom edge stays above the caption zone (860)


def architecture_html() -> str:
    """docs/architecture.svg (1920x1080, white) scaled to sit on the dark page with its bottom above y=860."""
    svg = re.sub(r"<\?xml[^>]*\?>", "", (C.ROOT / "docs" / "architecture.svg").read_text(encoding="utf-8"))
    w, h = int(1920 * ARCH_SCALE), int(1080 * ARCH_SCALE)
    return (f"<!doctype html><meta charset=utf-8><body style='margin:0;width:1920px;height:1080px;background:{C.PAGE}'>"
            f"<div style='position:absolute;left:{(1920 - w) // 2}px;top:0;width:{w}px;height:{h}px'>"
            f"<div style='transform:scale({ARCH_SCALE});transform-origin:0 0;width:1920px;height:1080px'>{svg}</div>"
            f"</div></body>")


# ------------------------------------------------------------------------------------------------ model stack
STACK = (("SAM 3.1", "Tracking", "masks and identity for both animals, every frame"),
         ("Muse Spark 1.3", "Planner, rules, report", "picks what to track; turns each definition into a Python rule"),
         ("Muse Spark Open", "Frame-strip check", "double-checks borderline moments from strips of frames"))


def model_stack_html(N: Numbers) -> str:
    rows = "".join(
        f"<div class=tile style='position:relative;margin-bottom:24px;padding:22px 40px'>"
        f"<div style='font-size:60px;font-weight:700;line-height:1.1'>{H.escape(m)}"
        f"<span style='font-size:46px;font-weight:400;color:{C.ACCENT};margin-left:28px'>{H.escape(role)}</span></div>"
        f"<div style='font-size:44px;color:{C.TEXT2};margin-top:6px'>{H.escape(what)}</div></div>"
        for m, role, what in STACK)
    return page(f"""
<div class=brand><i></i>ETHO</div>
<div class=abs style='left:80px;top:130px;font-size:60px;font-weight:700'>Built on Meta's models</div>
<div class=abs style='left:80px;top:220px;width:1760px'>{rows}</div>
""")


# ---------------------------------------------------------------------------------------------------- closing
def closing_html(N: Numbers) -> str:
    link = ("display:inline-block;border:3px solid {c};border-radius:22px;padding:16px 34px;"
            "font-size:52px;font-weight:700;color:{c}").format(c=C.ACCENT)
    lab = f"display:inline-block;width:150px;font-size:40px;color:{C.MUTED}"
    return page(f"""
<div class=brand><i></i>ETHO</div>
<div class=abs style='left:80px;top:190px;width:1760px;font-size:96px;font-weight:700;line-height:1.12'>
 For every lab that still scores behavior by hand.</div>
<div class=abs style='left:80px;top:460px;font-size:60px;color:{C.TEXT2}'>Open source. Try it live.</div>
<div class=abs style='left:80px;top:565px'><span style='{lab}'>Code</span><span
 style='{link}'>{H.escape(C.REPO_URL_SHOWN)}</span></div>
<div class=abs style='left:80px;top:690px'><span style='{lab}'>Live</span><span
 style='{link}'>{H.escape(C.LIVE_URL_SHOWN)}</span></div>
<div class=abs style='left:80px;top:800px;font-size:36px;color:{C.MUTED}'>Apache-2.0. Benchmark data:
 {H.escape(C.CREDIT_CARD)}.</div>
""")


CARDS = {"title": title_html, "evidence": evidence_html,
         "evidence_etho": lambda N: evidence_focus_html(N, "tEtho"),
         "evidence_sup": lambda N: evidence_focus_html(N, "tSup"),
         "evidence_run": lambda N: evidence_focus_html(N, "tRun"), "close": close_html, "model_stack": model_stack_html,
         "closing": closing_html}

# docs/video_assets/<name>.png <- card name (committed copies for the video app; numbers come from Numbers)
DOCS_ASSETS = {"title": "title", "results": "evidence", "model_stack": "model_stack", "closing": "closing"}


def export_docs(N: Numbers, out=None) -> list:
    """Render the cards Aniket's video app uses into docs/video_assets/ (1920x1080 PNG) and the score badge."""
    out = C.Path(out or C.ROOT / "docs" / "video_assets")
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for dst, card in DOCS_ASSETS.items():
        C.html_to_png(CARDS[card](N), out / f"{dst}.png")
        made.append(out / f"{dst}.png")
    badge_png(N, out / "badge_clip_score.png")
    made.append(out / "badge_clip_score.png")
    return made


def card_text(html: str) -> str:
    """Visible text of a card (what a viewer reads), for the traceability test."""
    t = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", H.unescape(t)).strip()


# ------------------------------------------------------------------------------------------------------ clips
def png_to_clip(png, out, dur: float, fade: float = 0.5) -> None:
    """Still image -> mp4 with fade in/out (H.264, 1080p30, yuv420p)."""
    C.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(C.FPS), "-t", f"{dur}", "-i", str(png),
           "-vf", f"fade=t=in:st=0:d={fade},fade=t=out:st={dur - fade}:d={fade},format=yuv420p",
           "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-r", str(C.FPS), str(out)])


def build_all(N: Numbers, clip_s: float = 4.0) -> dict[str, "C.Path"]:
    C.CARDS.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, fn in CARDS.items():
        png = C.CARDS / f"{name}.png"
        C.html_to_png(fn(N), png)
        made[name] = png
    who_png(N, C.CARDS / "who.png")
    made["who"] = C.CARDS / "who.png"
    badge_png(N, C.CARDS / "badge_clip_score.png")
    made["badge"] = C.CARDS / "badge_clip_score.png"
    for name, png in made.items():
        if name != "badge":
            png_to_clip(png, C.CARDS / f"{name}.mp4", clip_s)
    return made


if __name__ == "__main__":
    if not C.have_render_tools():
        sys.exit("need Chromium (playwright cache) and ffmpeg")
    N = Numbers()
    if "--docs-assets" in sys.argv:
        for f in export_docs(N):
            print(f)
        print(N.source_map())
        sys.exit(0)
    build_all(N)
    print(N.source_map())
    print("cards in", C.CARDS)
