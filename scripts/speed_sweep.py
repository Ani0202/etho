"""Speed sweep: full uncached pipeline on ONE fresh clip, timing every stage, at a chosen tier-1 concurrency.

  ETHO_ROOT=/home/ubuntu/work/etho uv run python scripts/speed_sweep.py --seq mouse024_task1_annotator1 --conc 6

prepare_chunks -> track_video (SAM) -> rules (R1 zero-shot) -> tier-1 (effort low, DEFAULT_ALLOW) with the given
Fireworks concurrency. Clips come from the UNUSED CalMS21 train pool only (eval/splits/calms21_v1.json
"unused_train"); nothing is scored and no labels are read. Spends on the gateway (tag dev, public data).
Run with ETHO_ROOT = the repo root that holds data/ and ops/. Pure helpers (`call_stats`, `Tee`) are unit-tested."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}
PRIORITY = ("mount", "attack", "investigation")
ARCHIVE = "https://data.caltech.edu/records/s0vdx-0k302/files/task1_videos_mp4.zip?download=1"


def call_stats(calls: list[dict]) -> dict:
    """calls: [{start, end, latency}] with start/end in seconds on one clock. Throughput and the tail:
    time from the 90th-percentile completion to the last completion."""
    if not calls:
        return {}
    ends = np.sort([c["end"] for c in calls])
    lat = np.array([c["latency"] for c in calls])
    t0 = min(c["start"] for c in calls)
    span = float(ends[-1] - t0)
    k90 = max(0, int(np.ceil(0.9 * len(ends))) - 1)
    return {"n_calls": len(calls), "span_s": span, "calls_per_s": len(calls) / span if span > 0 else float("nan"),
            "latency_median_s": float(np.median(lat)), "latency_p90_s": float(np.percentile(lat, 90)),
            "latency_max_s": float(lat.max()), "t90_s": float(ends[k90] - t0), "tail_s": float(ends[-1] - ends[k90])}


class Tee:
    """Counts gateway retry lines ('HTTP 429, retry') written to stderr while passing them through."""

    def __init__(self, inner):
        self.inner, self.lines, self._lock = inner, [], threading.Lock()

    def write(self, s):
        for m in re.finditer(r"HTTP (\d+), retry", s):
            with self._lock:
                self.lines.append(int(m.group(1)))
        return self.inner.write(s)

    def flush(self):
        self.inner.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", required=True)
    ap.add_argument("--conc", type=int, required=True)
    ap.add_argument("--budget-usd", type=float, default=1.6, help="stop tier-1 calls if this clip's spend passes it")
    a = ap.parse_args()
    os.chdir(Path(os.environ.get("ETHO_ROOT", ".")))
    from etho.adjudication import tier1
    from etho.datasets.remote_zip import extract, open_remote_zip
    from etho.gateway import Gateway
    from etho.rules.sandbox import run_detector
    from etho.tracking.pipeline import track_video

    split = json.loads(Path("eval/splits/calms21_v1.json").read_text())
    if a.seq not in split["unused_train"]:
        raise SystemExit(f"{a.seq} is not in the unused train pool; refusing (no dev/holdout/test videos here)")
    src = Path(f"data/calms21/videos/train/{a.seq}.mp4")
    if not src.exists():
        extract(open_remote_zip(ARCHIVE), f"task1_videos_mp4/train/{a.seq}.mp4", src)
    dets = json.loads(Path("configs/detectors/R1_zero_shot.json").read_text())["detectors"]
    defs = json.loads(Path("configs/calms21_definitions.json").read_text())
    out = Path("data/runs/speed") / f"{a.seq}_c{a.conc}"
    gw = Gateway()
    tee = Tee(sys.stderr)
    sys.stderr = tee
    t_ledger = time.time()

    # Stages 1-2: chunking is timed inside track_video (prep_s); then SAM + decode + features.
    t0 = time.monotonic()
    run = track_video(src, out, gw=gw, public_data=True, tag="dev", workers=8)
    wall_track = time.monotonic() - t0
    video_s = run.n_samples / 10.0
    sam_cost = run.cost_usd

    # Stage 3: rules (+ candidate windows for tier-1).
    t0 = time.monotonic()
    masks = {d["behavior"]: run_detector(d["code"], run.table, 10.0) for d in dets}
    lab = np.full(run.n_samples, CODES["other"], np.int8)
    for b in reversed(PRIORITY):
        lab[masks[b]] = CODES[b]
    wins = tier1.make_windows(a.seq, lab, run.table, CODES, 10.0)
    t_rules = time.monotonic() - t0

    # Stage 4: tier-1 with N concurrent Fireworks calls, instrumented from outside the frozen module.
    tier1.FW_CONCURRENCY = a.conc
    tier1._FW_SLOTS = threading.BoundedSemaphore(a.conc)
    calls, render = [], []
    lock = threading.Lock()
    orig_render, orig_chat = tier1.render_strip, gw.chat
    spent = {"usd": sam_cost}

    def timed_render(*args, **kw):
        s = time.monotonic()
        try:
            return orig_render(*args, **kw)
        finally:
            with lock:
                render.append(time.monotonic() - s)

    def timed_chat(*args, **kw):
        if spent["usd"] > a.budget_usd:
            raise RuntimeError(f"sweep budget guard: clip spend {spent['usd']:.2f} > {a.budget_usd}")
        s = time.monotonic()
        res = orig_chat(*args, **kw)
        e = time.monotonic()
        with lock:
            calls.append({"start": s, "end": e, "latency": res.latency_s, "cache_hit": res.cache_hit})
            spent["usd"] += res.cost_usd
        return res

    tier1.render_strip = timed_render
    gw.chat = timed_chat
    t_start = time.monotonic()
    # adjudicate reads run_dir/seq/tracking.json, so pass the parent of our output dir and its name.
    verdicts = tier1.adjudicate(out.parent, out.name, wins, defs, gw=gw, effort="low", tag="dev",
                                concurrency=a.conc, table=run.table)
    wall_t1 = time.monotonic() - t_start
    final = tier1.apply(lab, verdicts, CODES, tier1.CONFIDENT, tier1.DEFAULT_ALLOW)
    sys.stderr = tee.inner

    st = call_stats(calls)
    first_call = min(c["start"] for c in calls) if calls else t_start
    t1_cost = sum(v["cost_usd"] for v in verdicts)
    led = [json.loads(x) for x in Path("ops/spend.jsonl").read_text().splitlines() if x.strip()]
    mine = [r for r in led if datetime.fromisoformat(r["time"]).timestamp() >= t_ledger]
    t = run.timings
    res = {
        "seq": a.seq, "concurrency": a.conc, "video_s": video_s, "windows": len(verdicts),
        "stage_s": {"prepare_chunks": t["prep_s"], "sam_decode_features": t["sam_and_decode_s"],
                    "assemble": t["assemble_s"], "track_total": wall_track, "rules": t_rules,
                    "tier1_total": wall_t1, "tier1_setup_before_first_call": first_call - t_start,
                    "tier1_calls_span": st.get("span_s"), "tier1_tail_after_p90": st.get("tail_s")},
        "x_realtime": {"prepare_chunks": t["prep_s"] / video_s, "sam_decode_features": t["sam_and_decode_s"] / video_s,
                       "track_total": wall_track / video_s, "rules": t_rules / video_s, "tier1": wall_t1 / video_s,
                       "total": (wall_track + t_rules + wall_t1) / video_s,
                       "total_no_tier1": (wall_track + t_rules) / video_s},
        "tier1_s_per_window": wall_t1 / max(1, len(verdicts)), "windows_per_video_min": len(verdicts) / (video_s / 60),
        "render_total_cpu_s": sum(render), "render_mean_s": float(np.mean(render)) if render else None,
        "call_stats": st, "http_retries": {str(c): tee.lines.count(c) for c in sorted(set(tee.lines))},
        "n_429": tee.lines.count(429), "windows_changed_by_apply": int((final != lab).sum()),
        "cost_usd": {"sam": sam_cost, "tier1": t1_cost, "total": sam_cost + t1_cost},
        "ledger_rows": len(mine), "ledger_cache_hits": sum(bool(r.get("cache_hit")) for r in mine),
        "ledger_errors": sum(bool(r.get("error")) for r in mine),
        "tier1_cache_hits": sum(bool(v["cache_hit"]) for v in verdicts),
    }
    (out / "speed.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
