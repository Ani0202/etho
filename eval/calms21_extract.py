"""Stream the CalMS21 Task 1 JSON (1.2 GB) into compact per-sequence .npz files.

Output: data/calms21/seq/<split>/<seq>.npz with
  labels    int8 [T]           0 attack, 1 investigation, 2 mount, 3 other (vocab saved too)
  keypoints float16 [T,2,2,7]  mouse (0 resident/black, 1 intruder/white) x (x,y) x 7 body parts
  scores    float16 [T,2,7]
Labels stay under eval/ and data/; product code (etho/) must never read them.
Usage: uv run python -m eval.calms21_extract data/calms21/task1_classic_classification/calms21_task1_train.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import ijson
import numpy as np

OUT = Path("data/calms21/seq")


def main(path: str) -> None:
    index = {}
    with open(path, "rb") as f:
        for key, seq in ijson.kvitems(f, "annotator-id_0", use_float=True):
            _, split, name = key.split("/")
            labels = np.asarray(seq["annotations"], dtype=np.int8)
            kp = np.asarray(seq["keypoints"], dtype=np.float16)
            sc = np.asarray(seq["scores"], dtype=np.float16)
            vocab = seq["metadata"]["vocab"]
            d = OUT / split
            d.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(d / f"{name}.npz", labels=labels, keypoints=kp, scores=sc,
                                vocab=json.dumps(vocab))
            counts = {k: int((labels == v).sum()) for k, v in vocab.items()}
            index[name] = {"split": split, "frames": int(len(labels)), "counts": counts}
            print(name, len(labels), counts, kp.shape, flush=True)
    idx_path = OUT / f"index_{Path(path).stem}.json"
    idx_path.write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
