"""Qualitative fruit-fly clip for the "second species" beat (DEMO_PLAN 2:26-2:39). No model calls, no spending.

Everything is re-used from disk: the source movie, the stored SAM 3.1 tracking of Fly vs. Fly training movie 1
(box-local RLE masks, `raw_c*.jsonl.gz`) and Etho's predicted behavior labels from the frozen gate run
(`data/runs/F1/pred_gate_wing/movie1.npz`, the arm that produced the NO-GO verdict of ADR-004; the `final` and
`frozen_dryrun` runs have identical arrays for movie 1). The wing gate was NO-GO, so no F1 or any accuracy number
is drawn: the clip carries a "Qualitative preview: not scored" label and an Etho-only ethogram row.

Window choice (documented from the predicted labels only; this script never reads the expert annotation): for
movie 1 the predictions contain no wing threat at all, and two lunge bouts, 0.14 s at 50.1 s and 0.40 s at 247.6 s
(seconds into the 5-minute `mid5` window that starts at source frame 22500). `pick_window` takes the longest
predicted bout, so it selects the 0.40 s lunge, with 2.5 s of lead-in and 4.5 s of aftermath. The lunge is shown
at 0.5x speed (labelled on screen); at full speed it would be on screen for 12 frames.

    uv run python -m video.fly_clip            # writes data/video/capture/fly.mp4 (1920x1080, 30 fps, 14 s)
    uv run python -m video.fly_clip --stills   # also writes a few PNGs next to it for checking
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np

from video import common as C
from video import draw

ETHO_ROOT = Path(os.environ.get("ETHO_ROOT", C.DATA.parent))
RUN = "data/runs/F1/movie1"
PRED = "data/runs/F1/pred_gate_wing/movie1.npz"
SRC = "data/flyvsfly/Aggression/movie1/movie1.mp4"

CREDIT = "Video: Fly vs. Fly (Eyjolfsdottir et al. 2014), CC0"
BADGE = "Qualitative preview: not scored"
PRE_S, POST_S, SPEED = 2.5, 4.5, 0.5          # source seconds before / after the bout start; playback speed
NAMES = {0: "No behavior detected", 1: "Wing threat", 2: "Lunge"}
# App behavior slots for the two fly behaviors (slot 0 solid, slot 1 diagonal lines); see video/draw.py patterns.
SLOT = {1: "attack", 2: "investigation"}
FLY_BGR = [(235, 200, 90), (90, 200, 235)]    # neutral identity colors for the two flies (BGR)
VID_X, VID_Y, VID_S = 40, 40, 960
PX, PW = 1040, 840                            # right panel x and width


def hex_bgr(h: str) -> tuple[int, int, int]:
    return C.hex2bgr(h)


def runs_of(a: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of each run of non-zero entries."""
    d = np.diff(np.r_[0, (np.asarray(a) != 0).astype(int), 0])
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist()))


def frame_codes(wing: np.ndarray, lunge: np.ndarray) -> np.ndarray:
    """0 none, 1 wing threat, 2 lunge (a lunge wins a tie: it is the shorter, more specific event)."""
    return np.where(np.asarray(lunge) != 0, 2, np.where(np.asarray(wing) != 0, 1, 0)).astype(np.uint8)


def pick_window(codes: np.ndarray, fps: float, pre_s: float = PRE_S, post_s: float = POST_S) -> tuple[int, int, tuple[int, int]]:
    """(start, stop, bout): the window around the longest predicted bout (earliest on ties), clamped to the array."""
    bouts = runs_of(codes)
    if not bouts:
        raise ValueError("no predicted bout to center the clip on")
    bout = max(bouts, key=lambda b: (b[1] - b[0], -b[0]))
    s = max(0, bout[0] - int(round(pre_s * fps)))
    e = min(len(codes), bout[0] + int(round(post_s * fps)))
    return s, e, bout


def load_masks(run: Path, g0: int, g1: int) -> dict[int, list[tuple[tuple[int, int], np.ndarray]]]:
    """{sample g: [((x0, y0), mask)]} for g in [g0, g1), each sample taken from the chunk that owns it."""
    from pycocotools import mask as mask_utils
    info = json.loads((run / "tracking.json").read_text())
    out: dict[int, list] = {}
    for ch in info["chunk_files"]:
        if ch["own1"] <= g0 or ch["own0"] >= g1:
            continue
        lo, hi = max(g0, ch["own0"]), min(g1, ch["own1"])
        with gzip.open(run / f"raw_c{ch['index']:03d}.jsonl.gz", "rt") as f:
            for line in f:
                o = json.loads(line)
                if not (lo <= o["g"] < hi):
                    continue
                r = o["rle"]
                m = mask_utils.decode({"size": r["size"], "counts": r["counts"].encode()})
                out.setdefault(o["g"], []).append(((o["box"][0], o["box"][1]), m))
    return out


def nearest_track(xy: np.ndarray, cx: float, cy: float) -> int:
    d = np.hypot(xy[:, 0] - cx, xy[:, 1] - cy)
    d = np.where(np.isnan(d), np.inf, d)
    return int(np.argmin(d))


def draw_masks(canvas: np.ndarray, objs: list, track_xy: np.ndarray, active: set[int], hot_bgr, scale: float) -> None:
    """Outline each fly in its identity color; a fly Etho tags as performing the behavior gets a thick behavior-colored outline."""
    for (x0, y0), m in objs:
        ys, xs = np.nonzero(m)
        if not len(xs):
            continue
        k = nearest_track(track_xy, x0 + xs.mean(), y0 + ys.mean())
        big = cv2.resize(m.astype(np.float32), None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR) > 0.5
        cs, _ = cv2.findContours(big.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE,
                                 offset=(int(round(x0 * scale)), int(round(y0 * scale))))
        hot = k in active
        cv2.drawContours(canvas, cs, -1, hot_bgr if hot else FLY_BGR[k % 2], 6 if hot else 3, cv2.LINE_AA)


def _t(html: str, w: int, h: int, name: str) -> np.ndarray:
    return draw.text_layer(html, w, h, name=name)


def _div(css: str, body: str) -> str:
    return f"<div style='font-family:\"{C.FONT}\";{css}'>{body}</div>"


class Panel:
    """Static page pieces and the per-label tag layers, rendered once; `frame` adds the dynamic parts."""

    def __init__(self, codes: np.ndarray, fps: float, t0_movie: float):
        self.n, self.fps, self.codes, self.t0 = len(codes), fps, codes, t0_movie
        img = self.static = np.zeros((C.H, C.W, 3), np.uint8)
        img[:] = hex_bgr(C.PAGE)
        cv2.rectangle(img, (VID_X - 2, VID_Y - 2), (VID_X + VID_S + 1, VID_Y + VID_S + 1), hex_bgr(C.LINE), 2)
        draw.blit(img, _t(_div(f"font-weight:700;font-size:44px;line-height:60px;color:{C.GOLD};border:3px solid {C.GOLD};"
                               f"border-radius:10px;padding:8px 0;text-align:center", BADGE), PW, 100, "fly-badge"), PX, 40)
        draw.blit(img, _t(_div("font-weight:700;font-size:44px;line-height:56px", "Fruit flies, same pipeline")
                          + _div(f"font-size:32px;line-height:44px;color:{C.TEXT2}", "One-sentence definitions, no retraining"),
                          PW, 110, "fly-sub"), PX, 170)
        draw.blit(img, _t(_div(f"font-size:30px;line-height:40px;color:{C.MUTED}", "Etho's behavior tag"), PW, 44, "fly-tagcap"), PX, 330)
        draw.blit(img, _t(_div("font-weight:700;font-size:36px;line-height:50px", "Etho"), 110, 56, "fly-etho"), PX, 620)
        self.sx0, self.sx1, self.sy0, self.sy1 = PX + 120, PX + PW, 612, 682
        cv2.rectangle(img, (self.sx0, self.sy0), (self.sx1, self.sy1), hex_bgr(C.SURFACE), -1)
        cv2.rectangle(img, (self.sx0, self.sy0), (self.sx1, self.sy1), hex_bgr(C.LINE), 2)
        gl = draw.Glyphs(24, C.MUTED)
        for s in range(0, int(self.n / fps) + 1):
            x = int(self.sx0 + (self.sx1 - self.sx0) * s * fps / self.n)
            cv2.line(img, (x, self.sy1), (x, self.sy1 + 10), hex_bgr(C.MUTED), 2)
            if s % 2 == 0:
                gl.put(img, f"{s}s", x, self.sy1 + 14, "m")
        for k, (code, lab) in enumerate(((1, "wing threat"), (2, "lunge"))):
            x = PX + 120 + k * 330
            draw.fill_bar(img, x, x + 44, 770, 806, SLOT[code])
            draw.blit(img, _t(_div("font-size:30px;line-height:40px", lab), 260, 44, f"fly-leg{code}"), x + 58, 768)
        for c, s, e in [(c, s, e) for c in (1, 2) for s, e in runs_of(codes == c)]:
            x0 = self.sx0 + (self.sx1 - self.sx0) * s / self.n
            x1 = self.sx0 + (self.sx1 - self.sx0) * e / self.n
            draw.fill_bar(img, x0, max(x1, x0 + 4), self.sy0 + 4, self.sy1 - 4, SLOT[c])
        draw.blit(img, _t(_div(f"font-size:30px;line-height:42px;color:{C.TEXT2}",
                               "Tracking: SAM 3.1 &nbsp;&middot;&nbsp; detectors written by Muse Spark"), PW, 48, "fly-stack"), PX, 880)
        draw.blit(img, _t(_div(f"font-weight:700;font-size:32px;line-height:44px;color:{C.GOLD}", f"{SPEED:g}x slow motion"), 500, 48, "fly-slow"), PX, 984)
        draw.blit(img, _t(_div(f"font-size:30px;line-height:40px;color:{C.TEXT2}", "movie 1, time"), 400, 44, "fly-timecap"), PX, 932)
        draw.blit(img, _t(_div(f"font-size:28px;line-height:40px;color:{C.MUTED}", CREDIT), 1840, 44, "fly-credit"), VID_X, 1022)
        self.tags = {}
        for code, lab in NAMES.items():
            bg = hex_bgr(C.COLORS[SLOT[code]]) if code else hex_bgr(C.SURFACE)
            layer = np.zeros((130, PW, 3), np.uint8)
            layer[:] = bg
            draw.blit(layer, _t(_div(f"display:flex;align-items:center;height:130px;padding-left:28px;font-weight:700;"
                                     f"font-size:{72 if code else 46}px;color:{C.TEXT if code else C.MUTED}", lab), PW, 130, f"fly-tag{code}"))
            self.tags[code] = layer
        self.gl_time = draw.Glyphs(34, C.TEXT, True)

    def frame(self, i: int, video: np.ndarray) -> np.ndarray:
        img = self.static.copy()
        img[VID_Y:VID_Y + VID_S, VID_X:VID_X + VID_S] = video
        img[390:520, PX:PX + PW] = self.tags[int(self.codes[min(i, self.n - 1)])]
        x = int(self.sx0 + (self.sx1 - self.sx0) * (i + 0.5) / self.n)
        cv2.line(img, (x, self.sy0 - 8), (x, self.sy1 + 4), (255, 255, 255), 4)
        t = self.t0 + i / self.fps
        self.gl_time.put(img, f"{int(t // 60)}:{t % 60:04.1f}", PX + PW, 936, "r")
        return img


def render(out: Path, stills: bool = False) -> dict:
    root = ETHO_ROOT
    z = np.load(root / PRED)
    fps = float(z["sample_fps"])
    codes = frame_codes(z["wing_threat"], z["lunge"])
    g0, g1, bout = pick_window(codes, fps)
    src0 = int(z["window"][0])                       # source frame of sample 0
    masks = load_masks(root / RUN, g0, g1)
    track_xy, lunge_by_track = z["track_xy"], z["lunge_by_track"]
    panel = Panel(codes[g0:g1], fps, (src0 + g0) / fps)

    cap = cv2.VideoCapture(str(root / SRC))
    cap.set(cv2.CAP_PROP_POS_FRAMES, src0 + g0)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{C.W}x{C.H}", "-r", str(C.FPS),
         "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(tmp)], stdin=subprocess.PIPE)
    n_src, rep, shown = g1 - g0, int(round(1 / SPEED)), 0
    still_at = {n_src // 6, bout[0] - g0 + 4, n_src * 3 // 4}
    try:
        for i in range(n_src):
            ok, fr = cap.read()
            if not ok:
                raise RuntimeError(f"source video ended at sample {g0 + i}")
            g = g0 + i
            vid = cv2.resize(fr, (VID_S, VID_S), interpolation=cv2.INTER_CUBIC)
            active = {t for t in range(lunge_by_track.shape[0]) if lunge_by_track[t, g]}
            hot = hex_bgr(C.COLORS[SLOT[2 if codes[g] == 2 else 1]])
            draw_masks(vid, masks.get(g, []), track_xy[g], active, hot, VID_S / fr.shape[1])
            full = panel.frame(i, vid)
            for _ in range(rep):
                proc.stdin.write(full.tobytes())
                shown += 1
            if stills and i in still_at:
                cv2.imwrite(str(out.with_name(f"fly_still_{i:04d}.png")), full)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed")
        tmp.replace(out)
    finally:
        cap.release()
        if proc.poll() is None:
            proc.kill()
        tmp.unlink(missing_ok=True)
    return {"out": str(out), "frames": shown, "seconds": shown / C.FPS, "samples": [g0, g1], "bout_samples": list(bout),
            "bout_window_s": [bout[0] / fps, bout[1] / fps]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=C.CAPTURE / "fly.mp4")
    ap.add_argument("--stills", action="store_true")
    a = ap.parse_args()
    print(json.dumps(render(a.out, a.stills), indent=1))


if __name__ == "__main__":
    main()
