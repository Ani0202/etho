import gzip
import json

import httpx
import pytest

from etho.gateway import CapExceeded, PolicyError, ProviderError, prices
from etho.gateway.ledger import Ledger


def chat_ok(prompt=1000, completion=200, reasoning=150, text="hi"):
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "completion_tokens_details": {"reasoning_tokens": reasoning}}})


MSG = [{"role": "user", "content": "hello"}]


def test_cost_math():
    # 1000 in, 200 out on muse-spark-1.3: 1000*1.25/1e6 + 200*4.25/1e6
    assert prices.token_cost("meta", "muse-spark-1.3", 1000, 200) == pytest.approx(0.00125 + 0.00085)
    # contributor is much cheaper
    assert prices.token_cost("meta", "muse-spark-1.3-contributor", 1_000_000, 1_000_000) == pytest.approx(0.30)
    # cached input on Fireworks
    c = prices.token_cost("fireworks", "accounts/fireworks/models/muse-spark-open", 1000, 0, cached_tokens=600)
    assert c == pytest.approx((400 * 1.25 + 600 * 0.15) / 1e6)
    # SAM: 50-frame minimum per video request
    assert prices.sam_video_cost(10) == pytest.approx(0.01)
    assert prices.sam_video_cost(200) == pytest.approx(0.04)
    assert prices.sam_image_cost(1) == pytest.approx(0.0025)


def test_chat_logs_cost_and_reasoning(make_gw, cfg):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return chat_ok()
    gw = make_gw(handler)
    res = gw.chat("meta", "muse-spark-1.3", MSG, effort="high", tag="dev")
    assert res.text == "hi" and not res.cache_hit
    assert res.reasoning_tokens == 150
    assert res.cost_usd == pytest.approx(0.00125 + 0.00085)
    assert seen["body"]["reasoning_effort"] == "high"
    assert seen["auth"] == "Bearer test-meta"
    rows = Ledger(cfg.ops_dir).rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["provider"] == "meta" and r["tag"] == "dev" and r["reasoning_tokens"] == 150
    assert r["cost_usd"] == pytest.approx(0.0021) and r["cache_hit"] is False
    assert "test-meta" not in json.dumps(rows)  # keys never logged
    assert not list((cfg.ops_dir / "inflight").glob("*.json"))  # reservation released


def test_cache_hit_costs_zero(make_gw, cfg):
    calls = []

    def handler(req):
        calls.append(1)
        return chat_ok()
    gw = make_gw(handler)
    a = gw.chat("fireworks", "accounts/fireworks/models/muse-spark-open", MSG, effort="low", tag="dev")
    b = gw.chat("fireworks", "accounts/fireworks/models/muse-spark-open", MSG, effort="low", tag="dev")
    assert len(calls) == 1
    assert b.cache_hit and b.cost_usd == 0.0 and b.text == a.text
    rows = Ledger(cfg.ops_dir).rows()
    assert [r["cache_hit"] for r in rows] == [False, True]
    assert rows[1]["cost_usd"] == 0.0
    # different effort -> different key -> new call
    gw.chat("fireworks", "accounts/fireworks/models/muse-spark-open", MSG, effort="medium", tag="dev")
    assert len(calls) == 2


def test_cap_refusal_no_http(make_gw, cfg):
    led = Ledger(cfg.ops_dir)
    led.settle(None, {"provider": "baseten", "model": "x", "tag": "dev", "cost_usd": 89.999})
    calls = []
    gw = make_gw(lambda req: calls.append(1) or chat_ok())
    with pytest.raises(CapExceeded):
        gw.chat("baseten", "meta-models/muse-spark-open-1026", MSG, effort="low", tag="dev")
    assert calls == []


def test_meta_tag_budget_refusal(make_gw, cfg):
    Ledger(cfg.ops_dir).settle(None, {"provider": "meta", "model": "x", "tag": "dev", "cost_usd": 14.999})
    gw = make_gw(lambda req: chat_ok())
    with pytest.raises(CapExceeded, match="tag 'dev'"):
        gw.chat("meta", "muse-spark-1.3", MSG, effort="low", tag="dev")
    # other tags still have room
    gw.chat("meta", "muse-spark-1.3", MSG, effort="low", tag="eval-llm")


def test_inflight_reservations_count_toward_cap(cfg):
    led = Ledger(cfg.ops_dir, caps={"baseten": 1.0})
    led.reserve("baseten", "dev", 0.6)
    with pytest.raises(CapExceeded):
        led.reserve("baseten", "dev", 0.6)


def test_alert_written_when_crossing_50pct(cfg):
    led = Ledger(cfg.ops_dir, caps={"fireworks": 10.0})
    led.settle(None, {"provider": "fireworks", "model": "x", "tag": "dev", "cost_usd": 4.0})
    led.settle(None, {"provider": "fireworks", "model": "x", "tag": "dev", "cost_usd": 2.0})
    alerts = [json.loads(x) for x in (cfg.ops_dir / "alerts.jsonl").read_text().splitlines()]
    assert alerts[0]["what"] == "provider:fireworks" and alerts[0]["crossed"] == 0.5


@pytest.mark.parametrize("provider,model,effort", [
    ("meta", "muse-spark-1.3", None),
    ("meta", "muse-spark-1.3", "none"),
    ("meta", "muse-spark-1.3", "max"),
    ("baseten", "meta-models/muse-spark-open-1026", "minimal"),
])
def test_effort_required_and_validated(make_gw, provider, model, effort):
    gw = make_gw(lambda req: chat_ok())
    with pytest.raises((PolicyError, TypeError)):
        if effort is None:
            gw.chat(provider, model, MSG, tag="dev")  # type: ignore[call-arg]
        else:
            gw.chat(provider, model, MSG, effort=effort, tag="dev")


def test_policy_refusals(make_gw):
    gw = make_gw(lambda req: chat_ok())
    with pytest.raises(PolicyError, match="public datasets"):
        gw.chat("meta", "muse-spark-1.3-contributor", MSG, effort="low", tag="dev")
    gw.chat("meta", "muse-spark-1.3-contributor", MSG, effort="low", tag="dev", public_data=True)
    with pytest.raises(PolicyError, match="tag"):
        gw.chat("meta", "muse-spark-1.3", MSG, effort="low", tag="misc")
    with pytest.raises(PolicyError, match="demo-reserve"):
        gw.chat("meta", "muse-spark-1.3", MSG, effort="low", tag="demo-reserve")
    with pytest.raises(PolicyError, match="n>1"):
        gw.chat("baseten", "meta-models/muse-spark-open-1026", MSG, effort="low", tag="dev", n=2)
    with pytest.raises(PolicyError, match="tool_choice"):
        gw.chat("baseten", "meta-models/muse-spark-open-1026", MSG, effort="low", tag="dev",
                tool_choice="auto")
    with pytest.raises(ValueError, match="no price"):
        gw.chat("baseten", "some-other-model", MSG, effort="low", tag="dev")


def test_demo_reserve_opens_nov3(make_gw):
    from datetime import datetime, timezone
    gw = make_gw(lambda req: chat_ok(), now=lambda: datetime(2026, 11, 3, 6, tzinfo=timezone.utc))
    gw.chat("meta", "muse-spark-1.3", MSG, effort="low", tag="demo-reserve")


def test_glimmer_effort_goes_in_system_prompt(make_gw):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return chat_ok()
    gw = make_gw(handler)
    gw.chat("fireworks", "accounts/fireworks/models/muse-glimmer-30b", MSG, effort="low", tag="dev")
    assert "reasoning_effort" not in seen["body"]
    assert seen["body"]["messages"][0] == {"role": "system", "content": "Reasoning strength: low"}


def test_retry_respects_retry_after(make_gw, cfg):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow down"})
        if n["i"] == 2:
            return httpx.Response(503, text="busy")
        return chat_ok()
    gw = make_gw(handler)
    res = gw.chat("baseten", "meta-models/muse-spark-open-1026", MSG, effort="low", tag="dev")
    assert res.text == "hi" and n["i"] == 3
    assert gw.sleeps[0] == 7.0 and len(gw.sleeps) == 2


def test_non_retryable_error_logged_and_raised(make_gw, cfg):
    gw = make_gw(lambda req: httpx.Response(404, json={"error": {"code": "model_not_found"}}))
    with pytest.raises(ProviderError) as ei:
        gw.chat("fireworks", "accounts/fireworks/models/muse-glimmer-30b", MSG, effort="low", tag="dev")
    assert ei.value.status == 404
    rows = Ledger(cfg.ops_dir).rows()
    assert rows[-1]["cost_usd"] == 0.0 and "404" in rows[-1]["error"]
    assert not list((cfg.ops_dir / "inflight").glob("*.json"))


def test_responses_api_usage(make_gw):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "output_tokens_details": {"reasoning_tokens": 40}}})
    gw = make_gw(handler)
    res = gw.responses("muse-spark-1.3", "hello", effort="medium", tag="dev")
    assert res.text == "ok" and res.reasoning_tokens == 40
    assert seen["body"]["reasoning"] == {"effort": "medium"}
    assert res.cost_usd == pytest.approx((100 * 1.25 + 50 * 4.25) / 1e6)


SAM_LINES = [
    "<0f>0<|box;x1=1;y1=2;x2=10;y2=20;w=320;h=240|><|mask;x=0;y=0;data=19,10,~abc|>",
    "<1f>0<|box;x1=1;y1=2;x2=10;y2=20;w=320;h=240|><|mask;x=0;y=0;data=19,10,~abd|>",
]


def sse(events):
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def test_sam_video_stream_cost_and_cache(make_gw, cfg, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00fake-mp4")
    calls = {"files": 0, "sam": 0}

    def handler(req):
        if req.url.path.endswith("/files"):
            calls["files"] += 1
            return httpx.Response(200, json={"id": "file-123", "status": "uploaded"})
        calls["sam"] += 1
        body = json.loads(req.content)
        assert body["model"] == "sam-3.1" and body["stream"] is True
        assert body["input"][0]["content"][1] == {"type": "input_video", "file_id": "file-123"}
        return sse([{"type": "response.output_text.delta", "delta": SAM_LINES[0] + "\n<1f>0<|bo"},
                    {"type": "response.output_text.delta", "delta": SAM_LINES[1][len("<1f>0<|bo"):] + "\n"},
                    {"type": "response.completed", "response": {"usage": {"video_frames_processed": 200}}}])
    gw = make_gw(handler)
    seen = []
    res = gw.sam("black mouse", tag="eval-sam", video=clip, n_frames=200, public_data=True, on_line=seen.append)
    assert seen == SAM_LINES
    assert list(res.lines()) == SAM_LINES
    assert res.frames_billed == 200 and res.cost_usd == pytest.approx(0.04)
    res2 = gw.sam("black mouse", tag="eval-sam", video=clip, n_frames=200, public_data=True)
    assert res2.cache_hit and res2.cost_usd == 0.0 and list(res2.lines()) == SAM_LINES
    assert calls == {"files": 1, "sam": 1}
    with gzip.open(res.text_path, "rt") as f:
        assert f.read().splitlines() == SAM_LINES


def test_sam_requires_frames_and_short_phrase(make_gw, tmp_path):
    gw = make_gw(lambda req: httpx.Response(500))
    with pytest.raises(PolicyError, match="n_frames"):
        gw.sam("mouse", tag="dev", video="https://example.com/a.mp4")
    with pytest.raises(PolicyError, match="noun phrase"):
        gw.sam("black mouse, white mouse", tag="dev", image="https://example.com/a.png")


def test_sam_min_50_frames_when_usage_missing(make_gw):
    gw = make_gw(lambda req: sse([{"type": "response.completed", "response": {}}]))
    res = gw.sam("mouse", tag="dev", video="https://example.com/a.mp4", n_frames=12)
    assert res.frames_billed == 50 and res.cost_usd == pytest.approx(0.01)
