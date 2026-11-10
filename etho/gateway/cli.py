"""Thin CLIs over the gateway: bin/spend, bin/muse, bin/open, bin/fw, bin/sam."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import Gateway, image_part, video_part
from .prices import TAGS
from .report import summary

DEFAULT_MODELS = {
    "muse": ("meta", "muse-spark-1.3"),
    "open": ("baseten", "meta-models/muse-spark-open-1026"),
    "fw": ("fireworks", "accounts/fireworks/models/muse-spark-open"),
}


def _llm_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument("prompt")
    p.add_argument("--model", default=DEFAULT_MODELS[prog][1])
    p.add_argument("--effort", required=True)
    p.add_argument("--tag", required=True, choices=TAGS)
    p.add_argument("--image", action="append", default=[], type=Path)
    p.add_argument("--video", action="append", default=[], type=Path)
    p.add_argument("--system")
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--public", action="store_true", help="input is from a public dataset")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    if prog == "muse":
        p.add_argument("--responses", action="store_true", help="use the Responses API")
    return p


def _print_llm(res, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"text": res.text, "usage": res.usage, "cost_usd": res.cost_usd,
                          "cache_hit": res.cache_hit, "latency_s": res.latency_s}, indent=2))
        return
    print(res.text)
    print(f"--- in={res.usage['input_tokens']} out={res.usage['output_tokens']} "
          f"reasoning={res.reasoning_tokens} cost=${res.cost_usd:.5f} latency={res.latency_s:.1f}s "
          f"cache_hit={res.cache_hit}", file=sys.stderr)


def llm_main(prog: str, argv: list[str]) -> None:
    a = _llm_parser(prog).parse_args(argv)
    provider = DEFAULT_MODELS[prog][0]
    if prog == "fw" and a.video:
        sys.exit("Fireworks Muse Spark Open is text + image only; send video with bin/open")
    gw = Gateway()
    if prog == "muse" and a.responses:
        content = [{"type": "input_text", "text": a.prompt}]
        content += [image_part(i, "responses") for i in a.image]
        content += [video_part(v, "responses") for v in a.video]
        inp = ([{"role": "system", "content": a.system}] if a.system else []) + \
              [{"role": "user", "content": content}]
        res = gw.responses(a.model, inp, effort=a.effort, tag=a.tag, max_output_tokens=a.max_tokens,
                           public_data=a.public, caller=f"bin/{prog}", use_cache=not a.no_cache)
    else:
        content = [{"type": "text", "text": a.prompt}]
        content += [image_part(i) for i in a.image] + [video_part(v) for v in a.video]
        msgs = ([{"role": "system", "content": a.system}] if a.system else []) + \
               [{"role": "user", "content": content if (a.image or a.video) else a.prompt}]
        res = gw.chat(provider, a.model, msgs, effort=a.effort, tag=a.tag, max_tokens=a.max_tokens,
                      public_data=a.public, caller=f"bin/{prog}", use_cache=not a.no_cache)
    _print_llm(res, a.json)


def _count_frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out)


def _probe_fps(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=avg_frame_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)


def subsample(path: Path, fps: int, cache_dir: Path) -> Path:
    """Re-encode to `fps` (H.264, faststart) unless already at or below it. Cached by content hash."""
    if _probe_fps(path) <= fps + 0.01:
        return path
    from .cache import file_sha256
    out = cache_dir / "clips" / f"{file_sha256(path)[:16]}_{fps}fps.mp4"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-vf", f"fps={fps}",
                        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        "-an", str(tmp)], check=True)
        tmp.replace(out)
    return out


def sam_main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="sam")
    p.add_argument("--phrase", required=True)
    p.add_argument("--tag", required=True, choices=TAGS)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--image", type=Path)
    g.add_argument("--video", type=Path)
    p.add_argument("--public", action="store_true")
    p.add_argument("--confidence", action="store_true")
    p.add_argument("--out", type=Path, help="copy raw SAM output text here")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--fps", type=int, default=10, help="subsample video to this rate first (10-15 recommended)")
    p.add_argument("--keep-fps", action="store_true", help="send the video at its native rate")
    a = p.parse_args(argv)
    gw = Gateway()
    if a.video and not a.keep_fps:
        a.video = subsample(a.video, a.fps, gw.cfg.cache_dir)
    n_frames = _count_frames(a.video) if a.video else None
    md = {"include_confidence": "true"} if a.confidence else None
    res = gw.sam(a.phrase, tag=a.tag, image=a.image, video=a.video, n_frames=n_frames,
                 public_data=a.public, metadata=md, caller="bin/sam", use_cache=not a.no_cache)
    n_lines = 0
    out = a.out.open("w") if a.out else None
    for line in res.lines():  # streamed: never holds the whole video's masks in memory
        n_lines += 1
        if out:
            out.write(line + "\n")
    if out:
        out.close()
    sent = str(a.video) if a.video else None
    print(json.dumps({"frames_with_output": n_lines, "video_sent": sent, "frames_billed": res.frames_billed,
                      "usage": res.usage, "cost_usd": res.cost_usd, "cache_hit": res.cache_hit,
                      "latency_s": round(res.latency_s, 2), "raw": str(res.text_path)}, indent=2))


def main() -> None:
    prog, argv = sys.argv[1], sys.argv[2:]
    if prog == "spend":
        print(summary(Gateway().ledger))
    elif prog == "sam":
        sam_main(argv)
    elif prog in DEFAULT_MODELS:
        llm_main(prog, argv)
    else:
        sys.exit(f"unknown command {prog}")


if __name__ == "__main__":
    main()
