"""Cut the raw live-run recording (live_run_raw.mp4, 271.8 s) to the 29.0 s scene-3 clip live_run.mp4.

    python video/capture/cut_live_run.py [--raw RAW] [--out OUT]

Segments follow docs/VIDEO_APP_SCRIPT.md scene 3. Raw timestamps below were read off the recording itself (frame
contact sheets), not from log times. Every cut that skips time gets a "time skipped" tag for 1 s, showing how much of
the raw recording was skipped. The one backward cut (the progress page, last seconds of the run) is tagged as such,
because nothing is skipped there. The cut list is written next to the output as live_run.cut.json.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

MAIN = Path("/home/ubuntu/work/etho")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TAG_S = 1.0

# (label, raw_in, raw_out)
SEGMENTS = [
    ("paste: attack definition cleared, then pasted", 20.2, 21.7),
    ("upload picked, cost estimate, Score clicked, run clock starts", 45.0, 48.5),
    ("planner card (phrases proposed, CHOSEN one)", 241.8, 248.8),
    ("overlay playing, both mice tracked, close contact at about 0:05", 252.6, 261.6),
    ("progress page, last seconds of the run (clock 3:05 to 3:08)", 233.1, 236.1),
    ("per-stage timing and cost card", 265.5, 270.5),
]
TOTAL_S = 29.0


def fmt(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def tags(segments=SEGMENTS) -> list[str | None]:
    """Tag text per segment (None = contiguous, no tag). A forward gap from the furthest point reached so far is skipped
    time; a jump back into earlier raw footage skips nothing and is tagged as such."""
    out: list[str | None] = [None]
    reached = segments[0][2]
    for _, a, b in segments[1:]:
        gap = a - reached
        out.append(f"time skipped {fmt(gap)}" if gap > 0.5 else "earlier in the run" if gap < -0.5 else None)
        reached = max(reached, b)
    return out


def cut_list(segments=SEGMENTS) -> dict:
    t, rows = 0.0, []
    for (label, a, b), tag in zip(segments, tags(segments), strict=True):
        rows.append({"label": label, "raw_in": a, "raw_out": b, "out_in": round(t, 2), "out_out": round(t + b - a, 2),
                     "tag": tag})
        t += b - a
    return {"total_s": round(t, 2), "tag_s": TAG_S, "segments": rows,
            "note": "raw timestamps are seconds into live_run_raw.mp4"}


def filter_graph(segments=SEGMENTS) -> str:
    """One input per segment (inputs are seeked with -ss/-t, see inputs()), so ffmpeg never buffers the whole raw."""
    parts = [f"[{i}:v]fps=30,setpts=PTS-STARTPTS[s{i}]" for i in range(len(segments))]
    parts.append("".join(f"[s{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[cat]")
    cl, cur = cut_list(segments), "[cat]"
    n = 0
    for r in cl["segments"]:
        if not r["tag"]:
            continue
        nxt = f"[t{n}]"
        parts.append(f"{cur}drawtext=fontfile={FONT}:text='{r['tag'].replace(':', chr(92) + ':')}':fontsize=40:fontcolor=white:box=1:"
                     f"boxcolor=black@0.72:boxborderw=14:x=w-tw-50:y=h-th-50:"
                     f"enable='between(t,{r['out_in']},{r['out_in'] + TAG_S})'{nxt}")
        cur, n = nxt, n + 1
    parts.append(f"{cur}format=yuv420p[v]")
    return ";".join(parts)


def inputs(raw: str, segments=SEGMENTS) -> list[str]:
    args: list[str] = []
    for _, a, b in segments:
        args += ["-ss", f"{a}", "-t", f"{b - a:.3f}", "-i", raw]
    return args


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(MAIN / "data/video/capture/live_run_raw.mp4"))
    ap.add_argument("--out", default=str(MAIN / "data/video/capture/live_run.mp4"))
    a = ap.parse_args(argv)
    out = Path(a.out)
    tmp = out.with_name(out.stem + ".part.mp4")
    tmp.unlink(missing_ok=True)      # write aside, rename at the end: a kill cannot leave a broken file
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs(a.raw), "-filter_complex", filter_graph(), "-map", "[v]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "30", "-movflags", "+faststart", "-an",
                    str(tmp)], check=True)
    tmp.replace(out)
    cl = cut_list()
    out.with_suffix(".cut.json").write_text(json.dumps(cl, indent=1) + "\n")
    print(out, cl["total_s"], "s")


if __name__ == "__main__":
    main()
