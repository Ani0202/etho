"""Time one SAM 3.1 video request end to end (upload, first frame, stream rate). Spike/measurement only.
Usage: uv run python scripts/time_sam.py <video_10fps.mp4> [phrase] [--chunk-s N --workers K]"""
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from etho.gateway import Gateway


def nframes(p):
    cmd = ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
           "stream=nb_read_packets", "-of", "csv=p=0", str(p)]
    return int(subprocess.run(cmd, capture_output=True, text=True).stdout)


def one(gw, video, phrase, tag="eval-sam"):
    n = nframes(video)
    t0 = time.monotonic()
    stamps = []
    res = gw.sam(phrase, tag=tag, video=video, n_frames=n, public_data=True, metadata={"mask_encoding": "one_bit"},
                 on_line=lambda ln: stamps.append(time.monotonic()), caller="scripts/time_sam.py")
    t1 = time.monotonic()
    return {"video": str(video), "frames": n, "video_s": n / 10, "lines": len(stamps), "wall_s": round(t1 - t0, 1),
            "first_line_s": round(stamps[0] - t0, 1) if stamps else None, "cost": res.cost_usd,
            "cache_hit": res.cache_hit, "x_realtime": round((t1 - t0) / (n / 10), 3)}


def chunks(video, chunk_s, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    n = nframes(video)
    paths = []
    for i, start in enumerate(range(0, n, chunk_s * 10)):
        p = outdir / f"{Path(video).stem}_c{i:03d}.mp4"
        if not p.exists():
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vf",
                            f"select=between(n\\,{start}\\,{start + chunk_s * 10 - 1}),setpts=N/10/TB", "-r", "10",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", "-an", str(p)], check=True)
        paths.append(p)
    return paths


if __name__ == "__main__":
    video = Path(sys.argv[1])
    phrase = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "mouse"
    gw = Gateway()
    if "--chunk-s" in sys.argv:
        cs = int(sys.argv[sys.argv.index("--chunk-s") + 1])
        k = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 8
        t0 = time.monotonic()
        ps = chunks(video, cs, Path("data/calms21/chunks"))
        t_cut = time.monotonic() - t0
        with ThreadPoolExecutor(k) as ex:
            rs = list(ex.map(lambda p: one(gw, p, phrase), ps))
        wall = time.monotonic() - t0
        n = sum(r["frames"] for r in rs)
        print(json.dumps({"chunks": len(ps), "chunk_s": cs, "workers": k, "cut_s": round(t_cut, 1),
                          "wall_s": round(wall, 1), "video_s": n / 10, "x_realtime": round(wall / (n / 10), 3),
                          "cost": round(sum(r["cost"] for r in rs), 4),
                          "per_chunk_wall": [r["wall_s"] for r in rs]}, indent=1))
    else:
        print(json.dumps(one(gw, video, phrase), indent=1))
