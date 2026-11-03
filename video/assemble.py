"""Silent rough-cut assembler: follows the v5 beat timeline exactly (video/timeline.py = DEMO_PLAN table).

Run: python -m video.assemble [--no-cards]    (builds cards and animations first unless --no-cards)
Outputs (data/video/): rough_cut_v1.mp4, stills/<beat>.png, beats.md.

App beats take data/video/capture/<name>.(mp4|png); a missing capture becomes a labeled placeholder slate.
Pieces are joined with short crossfades; each piece is extended by the fade length so the total stays exactly the
sum of the beat durations (176 s). The output has a silent AAC track so editors can import it; the voice-over is
recorded later.
"""
from __future__ import annotations

import html as H
import re
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from video import common as C
from video import timeline as T

XFADE = 0.4
# Captions are converted to ASS with PlayRes 1920x1080 so FontSize is in real pixels (52 px, >= the 48 px floor).
CAP_FONT_PX = 52
ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {C.W}
PlayResY: {C.H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, \
Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, \
MarginV, Encoding
Style: Default,{C.FONT},{CAP_FONT_PX},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,3,12,0,2,\
150,150,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def srt_to_ass(srt: Path, ass: Path) -> int:
    n = 0
    lines = []
    for blk in re.split(r"\n\s*\n", srt.read_text(encoding="utf-8").strip()):
        m = re.match(r"\d+\s*\n(\d+):(\d\d):(\d\d),(\d{3}) --> (\d+):(\d\d):(\d\d),(\d{3})\s*\n(.*)", blk, re.S)
        if not m:
            continue
        g = m.groups()
        def t(h, mi, se, ms):
            return f"{int(h)}:{mi}:{se}.{ms[:2]}"

        text = g[8].strip().replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{t(*g[0:4])},{t(*g[4:8])},Default,,0,0,0,,{text}")
        n += 1
    ass.write_text(ASS_HEAD + "\n".join(lines) + "\n", encoding="utf-8")
    return n


@dataclass
class Piece:
    beat: str
    label: str          # what is on screen
    src: Path | None    # image or video; None never happens (placeholders get a slate PNG)
    dur: float
    real: bool          # False = placeholder slate
    note: str = ""


# How each beat is split into pieces: (label, capture key or built asset, seconds). Sums must equal the beat length.
PLAN = {
    "hook": [("opening shot: tracked mice + live ethogram", "opening", 5.0), ("title card", "card:title", 15.0)],
    "who": [("who it's for: CalMS21 human annotation", "card:who", 15.0)],
    "live_run": [("live run (app capture)", "cap:live_run", 29.0)],
    "code": [("architecture diagram", "arch", 7.0), ("code, commit dates, pytest line (capture)", "cap:code", 10.0)],
    "results": [("Etho vs expert ethogram, score badge, zoom to the traced error", "results_beat", 20.0),
                ("trace panel (capture)", "cap:trace", 9.0)],
    "correction": [("correction loop (app capture)", "cap:correction", 10.0)],
    # tiles highlighted in step with the voice-over sentences (accuracy, supervised, speed/cost), then the whole card
    "evidence": [("evidence card, accuracy tile in focus", "card:evidence_etho", 4.6),
                 ("evidence card, supervised tile in focus", "card:evidence_sup", 6.2),
                 ("evidence card, speed and cost tile in focus", "card:evidence_run", 6.6),
                 ("evidence card", "card:evidence", 8.6)],
    "fly": [("second species (qualitative, no score)", "cap:fly", 15.0)],
    "close": [("model stack card", "card:model_stack", 6.0), ("closing card with links", "card:closing", 9.0)],
}

SLATE_HINTS = {
    "live_run": ("App with a visible clock; waits cut and tagged 'time skipped'; planner card shows the phrases "
                 "Muse Spark proposed and the one SAM confirmed."),
    "code": ("Architecture diagram, then etho/gateway/gateway.py, etho/adjudication/tier1.py, etho/rules/sandbox.py, "
             "commit-date overlay, the pytest summary line."),
    "trace": "The trace panel for the error: the frame check answered 'attack', a rule in the cascade set it aside.",
    "correction": "Mark a few bouts; Etho rewrites the rule and shows what changed. No gain claim.",
    "fly": ("Fruit flies, one sentence, no retraining. Qualitative preview: no F1 on screen (ADR-004). "
            "No pipeline output for flies is on disk yet."),
}
SLATE_NAME = {"live_run": "live run", "code": "code beat", "trace": "trace panel", "correction": "correction loop",
              "fly": "second species (flies)"}
SLATE_WHEN = {"live_run": "recorded Nov 3", "code": "recorded Nov 3", "trace": "recorded Nov 3",
              "correction": "recorded Nov 3", "fly": "fly run clip not produced yet"}


def find_capture(name: str) -> Path | None:
    for ext in ("mp4", "png"):
        p = C.CAPTURE / f"{name}.{ext}"
        if p.exists():
            return p
    return None


def slate_html(key: str, beat: T.Beat, dur: float) -> str:
    from video.cards import page
    return page(f"""
<div class=brand><i></i>ETHO &middot; rough cut</div>
<div class=abs style='left:0;top:0;width:1920px;height:1080px;
 background:repeating-linear-gradient(135deg,rgba(217,181,58,.07) 0 28px,
transparent 28px 56px)'></div>
<div class=abs style='left:80px;top:230px;width:1760px;font-size:80px;font-weight:700;color:{C.GOLD};line-height:1.15'>
 PLACEHOLDER: {SLATE_NAME[key]}, {SLATE_WHEN[key]}</div>
<div class=abs style='left:80px;top:470px;width:1760px;font-size:46px;color:{C.TEXT2};
line-height:1.3'>{H.escape(SLATE_HINTS[key])}</div>
<div class=abs style='left:80px;top:720px;font-size:44px;color:{C.MUTED}'>Beat: {H.escape(beat.title)} &middot;
{T.fmt(beat.start)}&ndash;{T.fmt(beat.end)} &middot; {int(dur)} s on screen</div>
<div class=abs style='left:80px;top:790px;font-size:40px;color:{C.MUTED}'>Drop data/video/capture/{key}.mp4 (or .png)
 and re-run video.assemble.</div>
""")


def resolve(beat: T.Beat, label: str, ref: str, dur: float) -> Piece:
    kind, _, name = ref.partition(":")
    if kind == "cap":
        cap = find_capture(name)
        if cap:
            return Piece(beat.key, label, cap, dur, True, f"capture/{cap.name}")
        png = C.CARDS / f"placeholder_{name}.png"
        C.html_to_png(slate_html(name, beat, dur), png)
        return Piece(beat.key, label, png, dur, False, f"PLACEHOLDER slate ({SLATE_WHEN[name]})")
    if kind == "card":
        return Piece(beat.key, label, C.CARDS / f"{name}.png", dur, True, f"cards/{name}.png")
    if ref == "opening":
        return Piece(beat.key, label, C.OUT / "opening.mp4", dur, True, "opening.mp4")
    if ref == "results_beat":
        return Piece(beat.key, label, C.OUT / "results_beat.mp4", dur, True, "results_beat.mp4")
    if ref == "arch":
        png = C.CARDS / "architecture.png"
        from video.cards import architecture_html
        C.html_to_png(architecture_html(), png)
        return Piece(beat.key, label, png, dur, True, "docs/architecture.svg (rendered)")
    raise KeyError(ref)


def build_pieces() -> list[Piece]:
    out: list[Piece] = []
    for b in T.BEATS:
        spec = PLAN[b.key]
        if abs(sum(d for _, _, d in spec) - b.dur) > 1e-9:
            raise ValueError(f"{b.key}: pieces sum to {sum(d for _, _, d in spec)}, beat is {b.dur}")
        out.extend(resolve(b, label, ref, d) for label, ref, d in spec)
    return out


def _filter_for(i: int, p: Piece, dur_total: float) -> str:
    vf = (f"scale={C.W}:{C.H}:force_original_aspect_ratio=decrease,pad={C.W}:{C.H}:(ow-iw)/2:(oh-ih)/2:color=0x0d0d0d,"
          f"setsar=1,fps={C.FPS},format=yuv420p")
    if p.src.suffix == ".png":
        return f"[{i}:v]{vf},trim=duration={dur_total},setpts=PTS-STARTPTS[v{i}]"
    return (f"[{i}:v]{vf},tpad=stop_mode=clone:stop_duration={dur_total},"
            f"trim=duration={dur_total},setpts=PTS-STARTPTS[v{i}]")


def render(pieces: list[Piece], out: Path, srt: Path | None, audio: Path | None = None) -> None:
    n = len(pieces)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    filt = []
    for i, p in enumerate(pieces):
        ext = XFADE if i < n - 1 else 0.0
        total = p.dur + ext
        if p.src.suffix == ".png":
            cmd += ["-loop", "1", "-framerate", str(C.FPS), "-t", f"{total}", "-i", str(p.src)]
        else:
            cmd += ["-i", str(p.src)]
        filt.append(_filter_for(i, p, total))
    acc, s = "v0", pieces[0].dur
    for i in range(1, n):
        filt.append(f"[{acc}][v{i}]xfade=transition=fade:duration={XFADE}:offset={s:.3f}[x{i}]")
        acc, s = f"x{i}", s + pieces[i].dur
    last = acc
    if srt:
        filt.append(f"[{last}]ass=filename={srt}[vout]")
        last = "vout"
    if audio:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-t", f"{s}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex", ";".join(filt), "-map", f"[{last}]", "-map", f"{n}:a", "-t", f"{s}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(C.FPS),
            "-c:a", "aac", "-b:a", "192k" if audio else "96k", "-movflags", "+faststart", str(out)]
    C.run(cmd)


STILL_AT = {"hook": 2.5, "who": 30.0, "live_run": 50.0, "code": 68.0, "results": 97.0, "correction": 115.0,
            "evidence": 135.0, "fly": 152.0, "close": 168.0}   # seconds in the cut: inside each beat (checked by test)


def export_stills(video: Path, dest: Path | None = None, at: dict[str, float] | None = None) -> list[Path]:
    dest = dest or C.STILLS
    at = at or STILL_AT
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for i, b in enumerate(T.BEATS, 1):
        p = dest / f"{i:02d}_{b.key}.png"
        C.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at[b.key]:.2f}", "-i", str(video),
               "-frames:v", "1", str(p)])
        out.append(p)
    return out


def srt_count(path: Path) -> list[tuple[float, float]]:
    spans = []
    text = path.read_text(encoding="utf-8")
    for m in re.finditer(r"(\d+):(\d\d):(\d\d),(\d{3}) --> (\d+):(\d\d):(\d\d),(\d{3})", text):
        a = [int(x) for x in m.groups()]
        spans.append((a[0] * 3600 + a[1] * 60 + a[2] + a[3] / 1000, a[4] * 3600 + a[5] * 60 + a[6] + a[7] / 1000))
    return spans


def write_beats_md(pieces: list[Piece], srt: Path, out: Path, total_s: float) -> None:
    caps = srt_count(srt)
    rows = ["# Rough cut v1: beat timing", "",
            f"Total {T.fmt(total_s)} ({total_s:.1f} s), hard limit {T.HARD_LIMIT_S:.0f} s. Silent; "
            f"captions from `docs/demo_captions_v5.srt` (draft timings).", "",
            "| # | Beat | Plan time | Dur (s) | Captions | On screen | Status |", "|---|---|---|---|---|---|---|"]
    for i, b in enumerate(T.BEATS, 1):
        ps = [p for p in pieces if p.beat == b.key]
        ncap = sum(1 for s, e in caps if b.start <= s < b.end)
        on = "; ".join(f"{p.label} ({p.dur:g} s)" for p in ps)
        st = "; ".join(("real: " if p.real else "**PLACEHOLDER**: ") + p.note for p in ps)
        rows.append(f"| {i} | {b.title} | {T.fmt(b.start)}-{T.fmt(b.end)} | {b.dur:g} | {ncap} | {on} | {st} |")
    rows += ["", "Captions that end after the cut ends: " +
             (", ".join(f"{e:.1f}" for s, e in caps if e > total_s) or "none"), ""]
    out.write_text("\n".join(rows), encoding="utf-8")


def retime_pieces(pieces: list[Piece], beat_durs: dict[str, float]) -> list[Piece]:
    """Fit each beat's pieces to its new length: a longer beat holds its last piece, a shorter one trims every
    piece in proportion (the render already holds the last frame of a short clip and trims a long one)."""
    out: list[Piece] = []
    for b in T.BEATS:
        ps = [p for p in pieces if p.beat == b.key]
        old, new = sum(p.dur for p in ps), beat_durs[b.key]
        if new >= old:
            out += [replace(p, dur=p.dur + (new - old if i == len(ps) - 1 else 0)) for i, p in enumerate(ps)]
        else:
            out += [replace(p, dur=p.dur * new / old) for p in ps]
    return out


def main_vo(argv: list[str]) -> None:
    """Final cut: `--vo [--vo-dir DIR] [--out-dir DIR] [--no-cards]`. --out-dir sends every output (video, beats table,
    captions, stills) there instead of data/video and docs/, which is how a dry run avoids touching the real ones."""
    from video import vo

    def opt(name: str) -> Path | None:
        return Path(argv[argv.index(name) + 1]) if name in argv else None

    vo_dir = opt("--vo-dir") or C.OUT / "vo"
    out_dir = opt("--out-dir")
    dest = out_dir or C.OUT
    srt_out = (out_dir / "demo_captions_final.srt") if out_dir else vo.FINAL_SRT
    work = dest / "vo_work"
    vo.find_takes(vo_dir)          # fail on missing takes before rendering anything
    from video import anim, cards
    from video.numbers import Numbers
    N = Numbers()
    if "--no-cards" not in argv:
        cards.build_all(N)
        anim.build_all(N)
    print("takes:")
    try:
        res = vo.analyse(vo_dir, work)
    except vo.OverLimit as e:
        sys.exit(f"STOP: {e}")
    pieces = retime_pieces(build_pieces(), {p.key: p.dur for p in res.plans})
    dest.mkdir(parents=True, exist_ok=True)
    vo.write_srt(res.cues, srt_out)
    ass = dest / "captions_final.ass"
    print(f"captions: {srt_to_ass(srt_out, ass)} cues -> {srt_out}")
    for p in res.plans:
        b = res.bounds[p.key]
        print(f"  {p.key:<10} start {p.start:7.2f}  dur {p.dur:6.2f} ({p.delta:+.2f})  captions: {b.method} {b.detail}")
    audio = vo.mix_track(res.takes, res.plans, res.total, work / "vo_mix.wav")
    out = dest / "final_v1.mp4"
    render(pieces, out, ass, audio)
    loud = vo.measure_lufs(out)
    mid = {p.key: p.start + p.dur / 2 for p in res.plans}
    stills = export_stills(out, dest / "stills_final", mid)
    by_beat = {b.key: [p for p in pieces if p.beat == b.key] for b in T.BEATS}
    vo.write_beats_final(res, by_beat, dest / "beats_final.md", loud)
    print(f"final: {res.total:.2f} s; {loud['integrated_lufs']:.1f} LUFS integrated, "
          f"true peak {loud['true_peak_dbtp']:.1f} dBTP")
    print(f"wrote {out}\n" + "\n".join(f"  {p}" for p in stills))
    print("placeholders:", ", ".join(f"{p.beat}/{p.label}" for p in pieces if not p.real) or "none")


def main(argv: list[str]) -> None:
    if "--vo" in argv:
        return main_vo(argv)
    if not C.have_render_tools():
        sys.exit("need Chromium and ffmpeg")
    from video import anim, cards
    from video.numbers import Numbers
    N = Numbers()
    if "--no-cards" not in argv:
        cards.build_all(N)
        anim.build_all(N)
    pieces = build_pieces()
    srt_src = C.ROOT / "docs" / "demo_captions_v5.srt"
    srt = C.OUT / "captions_v5.srt"
    shutil.copyfile(srt_src, srt)
    ass = C.OUT / "captions_v5.ass"
    print(f"captions: {srt_to_ass(srt, ass)} cues -> {ass}")
    out = C.OUT / "rough_cut_v1.mp4"
    render(pieces, out, ass)
    stills = export_stills(out)
    write_beats_md(pieces, srt, C.OUT / "beats.md", T.total())
    print(N.source_map())
    print(f"\nwrote {out}\n" + "\n".join(f"  {p}" for p in stills))
    print("placeholders:", ", ".join(f"{p.beat}/{p.label}" for p in pieces if not p.real) or "none")


if __name__ == "__main__":
    main(sys.argv[1:])
