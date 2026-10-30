"""Animated pieces: the opening shot (overlay video + live ethogram) with its README GIF, and the results beat.

Run: python -m video.anim   (writes data/video/opening.mp4, results_beat.mp4, docs/media/etho-tracking.gif)

OPENING SEGMENT CHOICE (justified from the labels, see tools in video/tests/test_anim.py::test_opening_segment):
the clip's first minute is mostly quiet investigation. Scanning every 4.5 s window of Etho's final labels against the
CalMS21 expert labels (labels.npz, result.json "expert") for windows that are fully active, contain all three
behaviors and change label at least twice, the best agreement is 121-126 s: Etho and the expert both go from
investigation into an attack (both mice in contact, tangled), Etho adds a short mount, and 78% of frames agree. It is
lively, shows every color of the legend, and is not a cherry-picked perfect window (agreement is below 100%).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from video import common as C
from video import draw
from video.data import expert_codes, showcase

OPEN_T0, OPEN_DUR = 121.0, 5.0
PRE_ROLL = 2.0                        # seconds of already-known labels shown to the left of the playhead
ERR_VIEW = (34.0, 48.0)               # zoom window that frames the traced error (Etho mount vs expert attack)


class FfmpegPipe:
    """Raw BGR frames in, H.264 1080p30 yuv420p out."""

    def __init__(self, out: Path, w: int = C.W, h: int = C.H, fps: int = C.FPS):
        out.parent.mkdir(parents=True, exist_ok=True)
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
             "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", str(out)],
            stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        self.p.stdin.write(frame.tobytes())

    def close(self) -> None:
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("ffmpeg failed")


def runs(codes: np.ndarray):
    """Maximal runs of equal code as (code, start, end) sample indices."""
    if len(codes) == 0:
        return
    edges = np.flatnonzero(np.diff(codes)) + 1
    starts = np.r_[0, edges]
    ends = np.r_[edges, len(codes)]
    for s, e in zip(starts, ends):
        yield int(codes[s]), int(s), int(e)


def draw_lane(img, codes, fps, view, x0, x1, y0, y1, upto=None) -> None:
    """One-row ethogram: each run colored by behavior (other = empty), clipped to the view and to `upto` seconds."""
    cv2.rectangle(img, (x0, y0), (x1, y1), C.hex2bgr(C.SURFACE), -1)
    sx = (x1 - x0) / (view[1] - view[0])
    for code, s, e in runs(codes):
        if code >= len(C.BEHAVIORS):
            continue
        t0, t1 = s / fps, e / fps
        if upto is not None:
            t1 = min(t1, upto)
        t0, t1 = max(t0, view[0]), min(t1, view[1])
        if t1 <= t0:
            continue
        draw.fill_bar(img, x0 + (t0 - view[0]) * sx, x0 + (t1 - view[0]) * sx, y0, y1, C.BEHAVIORS[code])


class OverlayReader:
    def __init__(self, path: Path, fps: float):
        self.cap = cv2.VideoCapture(str(path))
        self.fps = fps
        self.pos = -1
        self.last = None

    def frame_at(self, t: float) -> np.ndarray:
        idx = int(t * self.fps)
        if idx != self.pos:
            if idx != self.pos + 1:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, fr = self.cap.read()
            if ok:
                self.last = fr
            self.pos = idx
        return self.last


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


# ------------------------------------------------------------------------------------------------ opening shot
OPEN = dict(vx=0, vy=24, vh=640, lx0=360, lx1=1840, ly_etho=(742, 794), ly_exp=(808, 860))


def opening_static(credit: bool = True) -> np.ndarray:
    lab = lambda y, txt: (f"<div style='position:absolute;left:30px;width:310px;text-align:right;top:{y}px;font-size:44px;"
                          f"font-weight:700;color:{C.TEXT}'>{txt}</div>")
    legend = "".join(f"<span style='display:inline-block;width:30px;height:30px;border-radius:6px;background:{C.COLORS[b]};"
                     f"margin:0 12px 0 34px;vertical-align:-4px'></span><span style='font-size:40px;color:{C.TEXT2}'>{b}</span>"
                     for b in C.BEHAVIORS)
    cr = (f"<div style='position:absolute;right:30px;top:690px;font-size:30px;color:{C.MUTED}'>{C.CREDIT}</div>" if credit else "")
    body = (f"{lab(OPEN['ly_etho'][0] - 2, 'Etho')}{lab(OPEN['ly_exp'][0] - 2, 'Expert')}"
            f"<div style='position:absolute;left:330px;top:676px;white-space:nowrap'>{legend}</div>{cr}")
    return draw.text_layer(body, C.W, C.H, name="opening")


def compose_opening_frame(base: np.ndarray, overlay: np.ndarray, t: float, sc: dict, codes_e: np.ndarray,
                          static: np.ndarray, fade: float = 1.0) -> np.ndarray:
    img = base.copy()
    vh = OPEN["vh"]
    vw = int(overlay.shape[1] * vh / overlay.shape[0])
    vx = (C.W - vw) // 2
    img[OPEN["vy"]:OPEN["vy"] + vh, vx:vx + vw] = cv2.resize(overlay, (vw, vh), interpolation=cv2.INTER_AREA)
    cv2.rectangle(img, (vx - 1, OPEN["vy"] - 1), (vx + vw, OPEN["vy"] + vh), C.hex2bgr(C.LINE), 2)
    view = (OPEN_T0 - PRE_ROLL, OPEN_T0 + OPEN_DUR)
    for rng, codes in ((OPEN["ly_etho"], sc["labels"]), (OPEN["ly_exp"], codes_e)):
        draw_lane(img, codes, sc["fps"], view, OPEN["lx0"], OPEN["lx1"], rng[0], rng[1], upto=t)
    px = int(OPEN["lx0"] + (t - view[0]) / (view[1] - view[0]) * (OPEN["lx1"] - OPEN["lx0"]))
    cv2.line(img, (px, OPEN["ly_etho"][0] - 14), (px, OPEN["ly_exp"][1] + 14), C.hex2bgr(C.TEXT), 4)
    draw.blit(img, static)
    if fade < 1.0:
        img = (img.astype(np.float32) * fade).astype(np.uint8)
    return img


def render_opening(out: Path, credit: bool = True) -> list[np.ndarray]:
    sc = showcase()
    codes_e = expert_codes(sc)
    rd = OverlayReader(sc["overlay"], sc["fps"])
    static = opening_static(credit)
    base = np.full((C.H, C.W, 3), C.hex2bgr(C.PAGE), np.uint8)
    pipe = FfmpegPipe(out)
    n = int(OPEN_DUR * C.FPS)
    keep = []
    for k in range(n):
        t = OPEN_T0 + k / C.FPS
        f = min(1.0, k / 6, (n - 1 - k) / 6)           # short fade in/out so it crossfades cleanly
        fr = compose_opening_frame(base, rd.frame_at(t), t, sc, codes_e, static, fade=1.0)
        if k in (n // 2, n - 1):
            keep.append(fr)
        pipe.write((fr.astype(np.float32) * ease(f)).astype(np.uint8) if f < 1.0 else fr)
    pipe.close()
    return keep


def make_gif(src_mp4: Path, out_gif: Path, width: int = 800, fps: int = 12, max_mb: float = 4.0) -> float:
    out_gif.parent.mkdir(parents=True, exist_ok=True)
    pal = out_gif.with_suffix(".pal.png")
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    C.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src_mp4), "-vf", f"{vf},palettegen=max_colors=96:stats_mode=diff", str(pal)])
    C.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src_mp4), "-i", str(pal), "-lavfi",
           f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", "-loop", "0", str(out_gif)])
    pal.unlink(missing_ok=True)
    mb = out_gif.stat().st_size / 1e6
    if mb > max_mb:
        raise RuntimeError(f"GIF is {mb:.1f} MB (> {max_mb})")
    return mb


# ------------------------------------------------------------------------------------------------ results beat
RES = dict(x0=360, x1=1840, row_h=46, gap=14, etho_y=392, exp_y=642, tick_y=828)
RES_DUR = 20.0
SWEEP = (1.0, 9.0)
ZOOM = (10.5, 13.0)
BADGE_AT = 3.0


def traced_error(sc: dict, near: float = 38.0) -> tuple[float, float]:
    """The Etho-says-mount / expert-says-attack stretch the voice-over traces ("at thirty-eight seconds"): Etho mount
    bouts merged across gaps of <= 0.5 s, keeping the merged run that overlaps an expert attack bout and starts
    closest to `near` seconds."""
    merged: list[list[int]] = []
    for s0, s1 in sorted(sc["etho"]["mount"]):
        if merged and s0 - merged[-1][1] <= 0.5 * sc["fps"]:
            merged[-1][1] = s1
        else:
            merged.append([s0, s1])
    hits = [m for m in merged if any(min(m[1], e1) > max(m[0], e0) for e0, e1 in sc["expert"]["attack"])]
    m = min(hits, key=lambda m: abs(m[0] / sc["fps"] - near))
    return m[0] / sc["fps"], m[1] / sc["fps"]


def results_static() -> np.ndarray:
    names = lambda y, tag: "".join(
        f"<div style='position:absolute;left:30px;width:310px;text-align:right;top:{y + i * (RES['row_h'] + RES['gap']) + 2}px;"
        f"font-size:38px;color:{C.TEXT}'>{b}</div>" for i, b in enumerate(C.BEHAVIORS))
    title = lambda y, txt: f"<div style='position:absolute;left:40px;top:{y}px;font-size:48px;font-weight:700'>{txt}</div>"
    body = (f"<div style='position:absolute;left:40px;top:28px;font-size:64px;font-weight:700'>Etho beside the expert</div>"
            f"{title(RES['etho_y'] - 62, 'Etho')}{names(RES['etho_y'], 'e')}"
            f"{title(RES['exp_y'] - 62, 'Expert (CalMS21)')}{names(RES['exp_y'], 'x')}")
    return draw.text_layer(body, C.W, C.H, name="results")


def results_note_layers():
    mk = lambda txt, col: draw.text_layer(
        f"<div style='font-size:46px;font-weight:700;color:{col};white-space:nowrap;padding:8px 18px;background:rgba(13,13,13,.85);"
        f"border-radius:14px;display:inline-block'>{txt}</div>", 520, 80, name="note")
    return mk("Etho: mount", C.COLORS["mount"]), mk("Expert: attack", C.COLORS["attack"])


def nice_ticks(view: tuple[float, float]) -> list[float]:
    span = view[1] - view[0]
    for st in (1, 2, 5, 10, 15, 30, 60, 120):
        if span / st <= 10:
            break
    first = int(np.ceil(view[0] / st) * st)
    return [float(t) for t in np.arange(first, view[1] + 1e-6, st)]


def results_view(t: float, dur: float) -> tuple[float, float]:
    z = ease((t - ZOOM[0]) / (ZOOM[1] - ZOOM[0]))
    return (z * ERR_VIEW[0], dur + z * (ERR_VIEW[1] - dur))


def render_results_frame(t: float, sc: dict, static: np.ndarray, badge: np.ndarray, notes, glyphs, err) -> np.ndarray:
    dur = sc["dur"]
    img = np.full((C.H, C.W, 3), C.hex2bgr(C.PAGE), np.uint8)
    view = results_view(t, dur)
    if t < SWEEP[0]:
        upto = 0.0
    elif t < SWEEP[1]:
        upto = ease((t - SWEEP[0]) / (SWEEP[1] - SWEEP[0])) * dur
    else:
        upto = None
    rows_e = draw.Rows(RES["x0"], RES["x1"], RES["etho_y"], RES["row_h"], RES["gap"], fps=sc["fps"])
    rows_x = draw.Rows(RES["x0"], RES["x1"], RES["exp_y"], RES["row_h"], RES["gap"], fps=sc["fps"])
    ticks = nice_ticks(view)
    rows_e.gridlines(img, ticks, view)
    rows_x.gridlines(img, ticks, view)
    rows_e.draw(img, sc["etho"], view, upto=upto, min_px=3)
    rows_x.draw(img, sc["expert"], view, upto=upto, min_px=3)
    for tk in ticks:
        glyphs.put(img, draw.fmt_clock(tk), int(rows_e.x_of(tk, view)), RES["tick_y"], anchor="m")
    if upto is not None and 0 < upto < dur:
        px = int(rows_e.x_of(upto, view))
        cv2.line(img, (px, RES["etho_y"] - 10), (px, rows_x.bottom + 10), C.hex2bgr(C.TEXT), 4)
    z = ease((t - ZOOM[0]) / (ZOOM[1] - ZOOM[0]))
    if z > 0.6 or t > ZOOM[1]:
        # highlight the traced error across both blocks
        xa, xb = int(rows_e.x_of(err[0], view)), int(rows_e.x_of(err[1], view))
        pulse = 0.5 + 0.5 * np.sin((t - ZOOM[1]) * 2.6) if t > ZOOM[1] else 1.0
        over = img.copy()
        cv2.rectangle(over, (xa, RES["etho_y"] - 12), (xb, rows_x.bottom + 12), C.hex2bgr(C.GOLD), -1)
        img = cv2.addWeighted(over, 0.10 + 0.08 * pulse, img, 0.90 - 0.08 * pulse, 0)
        cv2.rectangle(img, (xa, RES["etho_y"] - 12), (xb, rows_x.bottom + 12), C.hex2bgr(C.GOLD), 4)
        if t > ZOOM[1] + 0.8:
            mount_y = rows_e.y(2)
            attack_y = rows_x.y(0)
            draw.blit(img, notes[0], xb + 18, mount_y - 12)
            draw.blit(img, notes[1], xb + 18, attack_y - 12)
    draw.blit(img, static)
    if t >= BADGE_AT:
        draw.blit(img, badge, 180, 132, opacity=ease((t - BADGE_AT) / 0.6))
    f = min(1.0, t / 0.5, (RES_DUR - t) / 0.5)
    return img if f >= 1 else (img.astype(np.float32) * f).astype(np.uint8)


def render_results(out: Path, N=None) -> list[np.ndarray]:
    from video import cards
    from video.numbers import Numbers
    N = N or Numbers()
    sc = showcase()
    cards.badge_png(N, C.CARDS / "badge_clip_score.png")
    badge = draw.read_image(C.CARDS / "badge_clip_score.png")
    static = results_static()
    notes = results_note_layers()
    glyphs = draw.Glyphs(34, C.MUTED, bold=False)
    err = traced_error(sc)
    pipe = FfmpegPipe(out)
    keep = {}
    for k in range(int(RES_DUR * C.FPS)):
        t = k / C.FPS
        fr = render_results_frame(t, sc, static, badge, notes, glyphs, err)
        for name, ts in (("sweep", 6.0), ("zoom", 16.0)):
            if abs(t - ts) < 1 / C.FPS / 2:
                keep[name] = fr
        pipe.write(fr)
    pipe.close()
    return keep


def build_all(N=None) -> None:
    C.OUT.mkdir(parents=True, exist_ok=True)
    keep = render_opening(C.OUT / "opening.mp4")
    mb = make_gif(C.OUT / "opening.mp4", C.MEDIA / "etho-tracking.gif")
    print(f"gif {mb:.2f} MB -> {C.MEDIA / 'etho-tracking.gif'}")
    cv2.imwrite(str(C.CARDS / "opening_mid.png"), keep[0])
    k2 = render_results(C.OUT / "results_beat.mp4", N)
    cv2.imwrite(str(C.CARDS / "results_sweep.png"), k2["sweep"])
    cv2.imwrite(str(C.CARDS / "results_zoom.png"), k2["zoom"])


if __name__ == "__main__":
    if not C.have_render_tools():
        sys.exit("need Chromium and ffmpeg")
    build_all()
