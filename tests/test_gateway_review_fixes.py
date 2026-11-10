"""Regression tests for the Codex review of the gateway (docs/history/codex/review-20261028-042016.md)."""
import json
import shutil
import subprocess
import threading

import httpx
import pytest

from etho.gateway import PolicyError, ProviderError
from etho.gateway.gateway import estimate_llm_cost
from etho.gateway.ledger import Ledger

from .test_gateway import MSG, chat_ok, sse

MODEL = ("fireworks", "accounts/fireworks/models/muse-spark-open")


def test_estimate_counts_tools_and_rejects_bad_limits(make_gw):
    small = estimate_llm_cost(*MODEL, {"messages": MSG}, 100)
    big = estimate_llm_cost(*MODEL, {"messages": MSG, "tools": [{"description": "x" * 300_000}]}, 100)
    assert big > small + 0.1  # 100k tokens of tool text at $1.25/M
    gw = make_gw(lambda req: chat_ok())
    for bad in (-1, 0, 10**9, 2.5):
        with pytest.raises(PolicyError):
            gw.chat(*MODEL, MSG, effort="low", tag="dev", max_tokens=bad)


def test_read_timeout_is_not_retried_and_is_booked(make_gw, cfg):
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ReadTimeout("timed out", request=req)
    gw = make_gw(handler)
    with pytest.raises(ProviderError) as ei:
        gw.chat(*MODEL, MSG, effort="low", tag="dev", max_tokens=100)
    assert ei.value.ambiguous and len(calls) == 1
    row = Ledger(cfg.ops_dir).rows()[-1]
    assert row["maybe_billed"] and row["cost_usd"] > 0


def test_connect_error_is_retried(make_gw):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] < 3:
            raise httpx.ConnectError("refused", request=req)
        return chat_ok()
    assert make_gw(handler).chat(*MODEL, MSG, effort="low", tag="dev").text == "hi"
    assert n["i"] == 3


def test_malformed_200_is_booked_at_estimate(make_gw, cfg):
    gw = make_gw(lambda req: httpx.Response(200, content=b"<html>oops"))
    with pytest.raises(ValueError):  # json.JSONDecodeError
        gw.chat(*MODEL, MSG, effort="low", tag="dev", max_tokens=100)
    row = Ledger(cfg.ops_dir).rows()[-1]
    assert row["maybe_billed"] and row["cost_usd"] > 0


def test_abandoned_reservation_is_booked_not_dropped(cfg):
    led = Ledger(cfg.ops_dir)
    rid = led.reserve("baseten", "dev", 0.5)
    p = cfg.ops_dir / "inflight" / f"{rid}.json"
    d = json.loads(p.read_text())
    d["pid"] = 2**22 + 12345  # a pid that doesn't exist
    p.write_text(json.dumps(d))
    led.reserve("baseten", "dev", 0.01)  # next reserve reaps it
    rows = led.rows()
    assert rows[-1]["abandoned_reservation"] == rid and rows[-1]["cost_usd"] == 0.5
    assert led.totals()[0]["baseten"] == 0.5


def test_live_reservation_is_kept_however_old(cfg):
    led = Ledger(cfg.ops_dir)
    rid = led.reserve("baseten", "dev", 0.5)
    p = cfg.ops_dir / "inflight" / f"{rid}.json"
    d = json.loads(p.read_text())
    d["ts"] = 0  # ancient, but our pid is alive
    p.write_text(json.dumps(d))
    led.reserve("baseten", "dev", 0.01)
    assert p.exists() and led.rows() == []


def test_concurrent_identical_requests_pay_once(make_gw, cfg):
    calls = []
    gate = threading.Event()

    def handler(req):
        calls.append(1)
        gate.wait(2)
        return chat_ok()
    gw = make_gw(handler)
    results = []
    threads = [threading.Thread(target=lambda: results.append(gw.chat(*MODEL, MSG, effort="low", tag="dev")))
               for _ in range(3)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert sorted(r.cache_hit for r in results) == [False, True, True]


def test_truncated_sam_stream_raises_is_booked_and_not_cached(make_gw, cfg):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return sse([{"type": "response.output_text.delta", "delta": "<0f>0<|box;x1=1"}])
    gw = make_gw(handler)
    for _ in range(2):
        with pytest.raises(ProviderError, match="without response.completed"):
            gw.sam("mouse", tag="dev", video="https://example.com/a.mp4", n_frames=100)
    assert n["i"] == 2  # nothing was cached
    rows = Ledger(cfg.ops_dir).rows()
    assert all(r["maybe_billed"] and r["cost_usd"] == pytest.approx(0.02) for r in rows)
    assert not list(cfg.cache_dir.rglob("*.partial-*"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_cli_subsamples_high_fps_video(tmp_path):
    from etho.gateway.cli import _count_frames, subsample
    src = tmp_path / "v30.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=30",
                    "-t", "2", "-pix_fmt", "yuv420p", str(src)], check=True)
    out = subsample(src, 10, tmp_path / "cache")
    assert out != src and _count_frames(out) == 20
    assert subsample(out, 10, tmp_path / "cache") == out  # already at 10 fps: untouched
