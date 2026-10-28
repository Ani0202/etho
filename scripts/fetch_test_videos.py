"""Fetch the 19 CalMS21 test videos (range reads from the archive) and cut 10 fps / 60 s chunks.
No labels, no model calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from etho.datasets.remote_zip import extract, open_remote_zip
from etho.tracking.pipeline import prepare_chunks

seqs = json.loads(Path("eval/splits/calms21_v1.json").read_text())["test"]
zf = open_remote_zip("https://data.caltech.edu/records/s0vdx-0k302/files/task1_videos_mp4.zip?download=1")
for s in seqs:
    dest = Path(f"data/calms21/videos/test/{s}.mp4")
    if not dest.exists():
        extract(zf, f"task1_videos_mp4/test/{s}.mp4", dest)
    print("fetched", s, dest.stat().st_size // 1_000_000, "MB", flush=True)
with ThreadPoolExecutor(4) as ex:
    chunks = list(ex.map(lambda s: prepare_chunks(Path(f"data/calms21/videos/test/{s}.mp4"), Path("cache/chunks")),
                         seqs))
print("chunks", sum(len(c) for c in chunks), "samples", sum(x.n_samples for c in chunks for x in c))
