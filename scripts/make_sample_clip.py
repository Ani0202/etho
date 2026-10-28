"""Cut the 60 s sample clip that judges can download from the app's home page and run through the live pipeline.

  uv run python scripts/make_sample_clip.py [--seq mouse008_task1_annotator1] [--start-s 60]

Reads data/calms21/videos/train/<seq>.mp4 (CalMS21, Sun et al. 2021, CC BY 4.0), cuts 60 s with ffmpeg (re-encoded,
no audio, so the cut is frame-accurate and the file is small), and writes data/showcase/_sample/calms21_dev_60s.mp4 plus
sample.json next to it. The app serves that directory read-only (GET /api/sample, /api/sample/clip). Both stay
gitignored: raw dataset video is never committed.

The sequence must be in the "dev" list of eval/splits/calms21_v1.json (read only, never edited). A sequence in any
other split, test above all, is refused. No model is called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

CLIP_NAME = "calms21_dev_60s.mp4"
ROOT = Path(__file__).resolve().parents[1]
MAX_MB = 40.0       # well under the public upload limit (50 MB)


def dev_sequences(splits: Path) -> list[str]:
    return list(json.loads(splits.read_text())["dev"])


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def make_clip(seq: str, *, videos: Path, splits: Path, out_dir: Path, start_s: float = 60.0,
              duration_s: float = 60.0) -> dict:
    dev = dev_sequences(splits)
    if seq not in dev:
        sys.exit(f"{seq} is not in the dev split of {splits.name}; only dev-split sequences may be offered as a sample")
    src = videos / f"{seq}.mp4"
    if not src.is_file():
        sys.exit(f"source video not found: {src}")
    total = probe_duration(src)
    start = max(0.0, min(start_s, total - duration_s))      # a short recording: use its last `duration_s`
    dur = min(duration_s, total)
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / CLIP_NAME
    tmp = out_dir / (CLIP_NAME + ".part.mp4")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
                    "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(dst)
    size = dst.stat().st_size
    if size > MAX_MB * 1e6:
        dst.unlink()
        sys.exit(f"the clip is {size / 1e6:.0f} MB, over {MAX_MB:.0f} MB; pick a different --start-s or sequence")
    meta = {"file": CLIP_NAME, "sequence": seq, "split": "dev", "source_start_s": round(start, 2),
            "duration_s": round(probe_duration(dst), 2), "size_bytes": size,
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
            "dataset": "CalMS21 task 1 (Sun et al. 2021, Caltech)", "license": "CC BY 4.0",
            "trimmed": f"cut from {seq}.mp4, {start:.0f} s to {start + dur:.0f} s, re-encoded without audio"}
    (out_dir / "sample.json").write_text(json.dumps(meta, indent=1))
    return meta


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seq", help="a dev-split sequence (default: the first dev sequence with a video here)")
    ap.add_argument("--start-s", type=float, default=60.0)
    ap.add_argument("--duration-s", type=float, default=60.0)
    ap.add_argument("--videos", type=Path, default=ROOT / "data" / "calms21" / "videos" / "train")
    ap.add_argument("--splits", type=Path, default=ROOT / "eval" / "splits" / "calms21_v1.json")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "showcase" / "_sample")
    a = ap.parse_args(argv)
    seq = a.seq or next((s for s in dev_sequences(a.splits) if (a.videos / f"{s}.mp4").is_file()), None)
    if seq is None:
        sys.exit(f"no dev-split video found in {a.videos}")
    meta = make_clip(seq, videos=a.videos, splits=a.splits, out_dir=a.out_dir, start_s=a.start_s,
                     duration_s=a.duration_s)
    print(f"wrote {a.out_dir / CLIP_NAME}: {meta['duration_s']} s, {meta['size_bytes'] / 1e6:.1f} MB "
          f"({meta['trimmed']})")


if __name__ == "__main__":
    main()
