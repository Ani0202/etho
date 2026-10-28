"""scripts/speed_sweep.py pure helpers."""

import importlib.util
import io
from pathlib import Path

import numpy as np

_path = Path(__file__).resolve().parents[1] / "scripts" / "speed_sweep.py"
spec = importlib.util.spec_from_file_location("speed_sweep", _path)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def test_call_stats_tail_and_throughput():
    calls = [{"start": 0.0, "end": 1.0 + i, "latency": 1.0 + i % 3} for i in range(10)]
    calls[-1]["end"] = 30.0
    st = ss.call_stats(calls)
    assert st["n_calls"] == 10 and st["span_s"] == 30.0
    assert abs(st["calls_per_s"] - 10 / 30.0) < 1e-9
    assert st["t90_s"] == 9.0 and st["tail_s"] == 21.0  # 9th completion at t=9, last at 30
    assert st["latency_median_s"] == float(np.median([c["latency"] for c in calls]))


def test_call_stats_empty():
    assert ss.call_stats([]) == {}


def test_tee_counts_retries():
    t = ss.Tee(io.StringIO())
    t.write("gateway: fireworks HTTP 429, retry 1 in 2.0s\ngateway: x HTTP 503, retry 2 in 1s\n")
    assert t.lines == [429, 503]
