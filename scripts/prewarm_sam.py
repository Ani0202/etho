"""Cut 10 fps / 60 s chunks and run SAM on them (results land in the gateway cache; no decoding).
Usage: uv run python scripts/prewarm_sam.py <split> [<split> ...]   (dev, dev_holdout)"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from etho.gateway import Gateway
from etho.tracking.pipeline import prepare_chunks

splits = json.load(open("eval/splits/calms21_v1.json"))
seqs = [s for sp in sys.argv[1:] for s in splits[sp]]
t0 = time.monotonic()
with ThreadPoolExecutor(4) as ex:
    chunks = dict(zip(seqs, ex.map(lambda s: prepare_chunks(Path(f"data/calms21/videos/train/{s}.mp4"),
                                                              Path("cache/chunks")), seqs), strict=True))
print(f"prepared {sum(len(c) for c in chunks.values())} chunks in {time.monotonic() - t0:.0f}s", flush=True)
gw = Gateway()
jobs = [c for cs in chunks.values() for c in cs]
est = sum(max(c.n_samples, 50) for c in jobs) * 0.0002
print(f"{len(jobs)} SAM requests, {sum(c.n_samples for c in jobs)} frames, est ${est:.2f}", flush=True)
t1 = time.monotonic()
with ThreadPoolExecutor(8) as ex:
    res = list(ex.map(lambda c: gw.sam("mouse", tag="eval-sam", video=c.path, n_frames=c.n_samples,
                                       public_data=True, metadata={"mask_encoding": "one_bit"},
                                       caller="scripts/prewarm_sam.py"), jobs))
print(f"SAM done in {time.monotonic() - t1:.0f}s, cost ${sum(r.cost_usd for r in res):.2f}, "
      f"cache hits {sum(r.cache_hit for r in res)}")
