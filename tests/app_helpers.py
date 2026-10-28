"""Synthetic fixtures for the app tests: a tiny video, tracker output with RLE masks, and a fake gateway.
No network and no model calls anywhere."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from pycocotools import mask as mask_utils

from etho.app import backend as BK
from etho.rules.synthesize import Detector
from etho.tracking.pipeline import Chunk

FPS = 10
W, H = 160, 120

# investigation: bodies in contact; attack: very close and fast (never true in the synthetic table)
CODE = {"investigation": "def detect(t, fps):\n    return np.nan_to_num(t['gap'], nan=1e9) < 20\n",
        "attack": "def detect(t, fps):\n    return np.nan_to_num(t['dist'], nan=1e9) < 5\n"}


def make_video(path: Path, seconds: float = 6.0, w: int = W, h: int = H, fps: int = FPS) -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={w}x{h}:rate={fps}", "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def rle_rect(h: int, w: int) -> dict:
    r = mask_utils.encode(np.asfortranarray(np.ones((h, w), np.uint8)))
    return {"size": r["size"], "counts": r["counts"].decode()}


def write_tracking(seq_dir: Path, src: Path, n: int, stride=None) -> dict[str, np.ndarray]:
    """features.npz + tracking.json + raw_c000.jsonl.gz as track_video writes them (two animals; the first
    third of the video they are far apart, then in contact)."""
    seq_dir.mkdir(parents=True, exist_ok=True)
    gap = np.where(np.arange(n) < n // 3, 80.0, 5.0)
    t = {"r_present": np.ones(n), "i_present": np.ones(n), "gap": gap, "dist": gap + 10.0,
         "r_cx": np.full(n, 40.0), "r_cy": np.full(n, 60.0), "i_cx": np.full(n, 100.0), "i_cy": np.full(n, 60.0)}
    np.savez_compressed(seq_dir / "features.npz", **t)
    (seq_dir / "tracking.json").write_text(json.dumps({
        "src": str(src), "n_samples": n, "sample_fps": FPS, "src_stride": stride, "phrase": "mouse",
        "timings": {"wall_s": 1.0, "x_realtime": 0.1, "video_s": n / FPS}, "cost_usd": 0.0,
        "chunks": [{"chunk": 0, "identity_by": "intensity", "midpoint": 120.0,
                    "tracks": [{"track": "c0:1", "role": "resident"}, {"track": "c0:2", "role": "intruder"},
                               {"track": "c0:3", "role": "unknown"}], "relabeled": {}}], "cache_hits": 0}))
    with gzip.open(seq_dir / "raw_c000.jsonl.gz", "wt") as f:
        for k in range(n):
            for track, (x, y) in (("c0:1", (30, 50)), ("c0:2", (90, 50)), ("c0:3", (0, 0))):
                f.write(json.dumps({"frame": k, "track": track, "box": [x, y, x + 24, y + 16],
                                    "rle": rle_rect(16, 24)}) + "\n")
    return t


class FakeGateway:
    """Quacks like Gateway for JobGateway: records calls and appends ledger rows like the real one."""

    def __init__(self, ledger_path: Path, cost: float = 0.01, delay: float = 0.0, cache_root: Path | None = None):
        self.ledger_path, self.cost, self.delay = ledger_path, cost, delay
        self.cache_root = cache_root          # when set, every row also leaves a cache entry like the real gateway
        self.calls: list[dict] = []
        self.lock = threading.Lock()
        self._n = 0

    def _row(self, caller: str, tag: str, model: str, cost: float, endpoint: str = "/chat/completions") -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self._n += 1
            key = hashlib.sha256(f"{caller}|{self._n}|{endpoint}".encode()).hexdigest()
        row = {"provider": "meta", "model": model, "tag": tag, "caller": caller, "cost_usd": cost, "endpoint": endpoint,
               "request_key": key, "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "cache_hit": False}
        with self.lock, self.ledger_path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        if self.cache_root is not None:
            d = self.cache_root / key[:2]
            d.mkdir(parents=True, exist_ok=True)
            with gzip.open(d / f"{key}.json.gz", "wt") as f:
                json.dump({"id": f"file-{key[:8]}"} if endpoint == "/files" else {"text": "cached"}, f)
            if model == "sam-3.1":
                with gzip.open(d / f"{key}.txt.gz", "wt") as f:
                    f.write("sam output\n")
            (self.cache_root / "locks").mkdir(exist_ok=True)
            (self.cache_root / "locks" / f"{key}.lock").write_text("")

    def chat(self, provider, model, messages, *, effort, tag, caller=None, **kw):
        time.sleep(self.delay)
        self.calls.append({"provider": provider, "model": model, "effort": effort, "tag": tag, "caller": caller,
                           **kw})
        self._row(caller, tag, model, self.cost)
        return SimpleNamespace(text="Methods: synthetic run.", usage={"input_tokens": 10, "output_tokens": 5},
                               cost_usd=self.cost, cache_hit=False, latency_s=0.01, reasoning_tokens=0)

    def sam(self, phrase, *, tag, caller=None, **kw):
        self.calls.append({"sam": phrase, "tag": tag, "caller": caller})
        if self.cache_root is not None:
            self._row(caller, tag, "files", 0.0, endpoint="/files")  # the real gateway uploads the clip first
        self._row(caller, tag, "sam-3.1", self.cost, endpoint="/responses")
        return SimpleNamespace(cost_usd=self.cost, cache_hit=False, latency_s=0.01, frames_billed=50)

    def upload_file(self, *a, **k):
        return "file_x"


def patch_pipeline(monkeypatch, video: Path, n: int, *, revise_ok: bool = True, tier1_fail: bool = False,
                   plan_cost: float = 0.0):
    """Replace the heavy modules with synthetic stand-ins; everything else (sandbox, labels, report, overlay,
    files, state machine) runs for real."""
    from etho.perception.planner import Plan

    def fake_plan(path, *, gw, public_data, tag, **kw):
        gw.chat("meta", "muse-spark-1.3", [{"role": "user", "content": [{"type": "text", "text": "plan prompt"},
                                                                       {"type": "image_url",
                                                                        "image_url": {"url": "data:x"}}]}],
                effort="medium", tag="eval-llm", caller="etho.perception.planner", public_data=public_data)
        return Plan("mouse", 2, "coat color", "intensity", [{"step": "propose"}], 0.01)

    def fake_track(src, out_dir, *, gw, phrase, public_data, stride, tag, **kw):
        assert stride is None and public_data in (True, False)
        gw.sam(phrase, tag="eval-sam", video=src, n_frames=n, public_data=public_data,
               caller="etho.tracking.pipeline")
        table = write_tracking(out_dir, src, n)
        return SimpleNamespace(table=table, n_samples=n, sample_fps=10.0,
                               timings={"wall_s": 1.0, "x_realtime": 0.1}, cost_usd=0.01)

    def fake_synth(name, definition, *, gw, model, public_data, tag, effort, **kw):
        assert effort == "high" and tag == "dev"
        gw.chat("meta", model, [{"role": "user", "content": definition}], effort=effort, tag=tag,
                caller=f"etho.rules.synthesize:{name}", public_data=public_data)
        code = CODE.get(name, "def detect(t, fps):\n    return np.zeros(len(t['gap']), bool)\n")
        return Detector(name, code, model, [{"attempt": 0, "cost_usd": 0.01, "usage": {}, "cache_hit": False,
                                             "latency_s": 0.1}], prompt_hash="abc123")

    def fake_adjudicate(run_dir, seq, windows, definitions, *, gw, effort, tag, out_dir, **kw):
        assert effort == "low" and seq == "video"
        if tier1_fail:
            raise RuntimeError("fireworks 503")
        out_dir.mkdir(parents=True, exist_ok=True)
        res = []
        for w in windows:
            (out_dir / f"{seq}_{w.s0}_{w.s1}.jpg").write_bytes(b"\xff\xd8fake")
            res.append({"seq": seq, "s0": w.s0, "s1": w.s1, "rule_label": w.rule_label, "source": w.source,
                        "label": "none" if w.s0 == 20 else w.rule_label, "confidence": 0.9, "reason": "synthetic",
                        "cost_usd": 0.001, "cache_hit": False, "latency_s": 0.0})
        return res

    def fake_revise(det, definition, corrections, tables, *, gw, model, public_data, tag, effort, **kw):
        mine = [c for c in corrections if c["behavior"] == det.behavior]
        if not mine:
            return det
        gw.chat("meta", model, [{"role": "user", "content": "revise"}], effort=effort, tag=tag,
                caller=f"etho.rules.revise:{det.behavior}", public_data=public_data)
        if not revise_ok:
            from dataclasses import replace
            return replace(det, calls=det.calls + [{"revision_rejected": True, "cost_usd": 0.01,
                                                    "last_check": {"fixed_old": 0, "fixed_new": 0, "n": len(mine),
                                                                   "pos_old": 1, "pos_new": 1, "change": 0.0}}])
        # revised: gap threshold tightened so the contact window ends earlier
        code = "def detect(t, fps):\n    return np.nan_to_num(t['gap'], nan=1e9) < 2\n"
        return Detector(det.behavior, code, model,
                        [{"attempt": 0, "cost_usd": 0.01, "check": {"fixed_old": 0, "fixed_new": len(mine),
                                                                    "n": len(mine), "pos_old": 10, "pos_new": 9,
                                                                    "change": -0.1}}], prompt_hash=det.prompt_hash)

    def fake_chunks(src, chunk_dir, **kw):
        d = Path(chunk_dir) / hashlib.sha256(str(Path(src).resolve()).encode()).hexdigest()[:16]
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "c000.mp4").exists():
            shutil.copy(video, d / "c000.mp4")
        return [Chunk(0, d / "c000.mp4", 0, n)]

    monkeypatch.setattr(BK, "run_planner", fake_plan)
    monkeypatch.setattr(BK, "track_video", fake_track)
    monkeypatch.setattr(BK, "synthesize", fake_synth)
    monkeypatch.setattr(BK, "adjudicate", fake_adjudicate)
    monkeypatch.setattr(BK, "run_revise", fake_revise)
    monkeypatch.setattr(BK, "prepare_chunks", fake_chunks)
