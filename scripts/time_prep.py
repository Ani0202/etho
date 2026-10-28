"""Time the 10 fps / 60 s chunking step (`prepare_chunks`) per video, uncached, one video at a time.

The test run reused chunks cut earlier by `scripts/fetch_test_videos.py`, so its manifests record ~0 s of
preprocessing. This measures that step on its own. It makes no model calls and reads no labels.

    uv run python scripts/time_prep.py --split test --out data/runs/T1/prep_timing.json
"""
import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from etho.tracking.pipeline import prepare_chunks

ap = argparse.ArgumentParser()
ap.add_argument("--split", required=True, choices=["dev", "dev_holdout", "test"])
ap.add_argument("--out", type=Path, required=True)
a = ap.parse_args()
seqs = json.loads(Path("eval/splits/calms21_v1.json").read_text())[a.split]
sub = "test" if a.split == "test" else "train"
res = {}
for s in seqs:
    src = Path(f"data/calms21/videos/{sub}/{s}.mp4")
    tmp = Path(tempfile.mkdtemp(prefix="prep_"))
    t0 = time.monotonic()
    chunks = prepare_chunks(src, tmp)
    dt = time.monotonic() - t0
    res[s] = {"prep_s": dt, "n_chunks": len(chunks), "n_samples": sum(c.n_samples for c in chunks)}
    shutil.rmtree(tmp, ignore_errors=True)
    print(s, res[s], flush=True)
tot = sum(v["prep_s"] for v in res.values())
a.out.write_text(json.dumps({"split": a.split, "note": "prepare_chunks uncached, sequential, fresh temp dir per "
                             "video (stride 3, 10 fps, 60 s chunks)", "total_prep_s": tot, "seqs": res}, indent=1))
print("total prep s", tot)
