"""Daily spend cap (with pre-estimate) and per-client rate limit."""

import json
from datetime import datetime, timezone

import pytest

from etho.app.config import AppConfig
from etho.app.guards import RateLimiter, Refused, SpendGuard
from etho.app.jobgw import AppLedger
from tests.app_env import make_app, post_job, wait_terminal


def test_estimate_formula_matches_the_spec():
    cfg = AppConfig()
    # SAM $0.02/s + tier-1 $0.02/s + $0.05 fixed (+ $0.06 per behavior for the synthesis calls)
    assert cfg.estimate_job_usd(60, 3) == round(60 * 0.04 + 0.05 + 3 * 0.06, 4)
    assert cfg.estimate_job_usd(10, 0) == round(10 * 0.04 + 0.05, 4)
    assert cfg.daily_cap_usd == 5.0 and cfg.tag == "dev" and cfg.rate_limit_jobs == 3


def test_tag_is_a_config_value(monkeypatch):
    monkeypatch.setenv("ETHO_APP_TAG", "demo-reserve")
    monkeypatch.setenv("ETHO_APP_DAILY_CAP_USD", "2.5")
    cfg = AppConfig()
    assert cfg.tag == "demo-reserve" and cfg.daily_cap_usd == 2.5


def _ledger(tmp_path, rows):
    p = tmp_path / "spend.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return AppLedger(p)


def test_spend_guard_refuses_when_today_plus_estimate_exceeds_cap(tmp_path):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    led = _ledger(tmp_path, [
        {"caller": "job:a|x", "cost_usd": 4.0, "time": "2026-10-28T09:00:00+00:00"},
        {"caller": "job:b|x", "cost_usd": 3.0, "time": "2026-10-27T09:00:00+00:00"},      # yesterday: ignored
        {"caller": "etho.rules.synthesize:x", "cost_usd": 50.0, "time": "2026-10-28T09:00:00+00:00"}])  # not the app
    g = SpendGuard(AppConfig(daily_cap_usd=5.0), led, now=lambda: now)
    assert g.check(0.9)["spent_today_usd"] == 4.0
    with pytest.raises(Refused) as e:
        g.check(1.1)
    assert e.value.status == 429 and e.value.code == "daily_cap" and "$5.00 cap" in e.value.message
    with pytest.raises(Refused):                       # money already promised to queued jobs counts too
        g.check(0.5, outstanding_usd=0.6)
    assert g.remaining(0.2) == pytest.approx(0.8)
    # next UTC day the counter starts from zero
    g2 = SpendGuard(AppConfig(daily_cap_usd=5.0), led, now=lambda: datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert g2.check(4.9)["spent_today_usd"] == 0.0


def test_job_refused_by_api_when_cap_would_be_crossed_and_nothing_is_spent(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 5.0})
    c.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    c.cfg.ledger_path.write_text(json.dumps({"caller": "job:old|x", "cost_usd": 4.9,
                                             "time": datetime.now(timezone.utc).isoformat()}) + "\n")
    r = post_job(client, c.video)           # 6 s, 2 behaviors: estimate ~ $0.41 > $0.10 left
    assert r.status_code == 429 and r.json()["error"] == "daily_cap" and "cap" in r.json()["message"]
    assert not c.gw.calls and not any(c.cfg.jobs_dir.iterdir())
    assert client.get("/api/config").json()["cap"]["remaining_usd"] == pytest.approx(0.1)


def test_default_cap_refuses_a_five_minute_video_at_spec_rates(tmp_path):
    cfg = AppConfig()
    g = SpendGuard(cfg, AppLedger(tmp_path / "none.jsonl"))
    g.check(cfg.estimate_job_usd(100, 3))               # 100 s fits
    with pytest.raises(Refused):
        g.check(cfg.estimate_job_usd(300, 3))           # 300 s at $0.04/s + extras = $12.2 > $5


def test_queued_jobs_count_against_the_cap(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.3}, cfg_kw={"daily_cap_usd": 0.9, "rate_limit_jobs": 9})
    assert post_job(client, c.video).status_code == 200           # estimate ~ $0.41
    r = post_job(client, c.video)                                   # 0.41 + 0.41 < 0.9: accepted
    assert r.status_code == 200
    r = post_job(client, c.video)                                   # third would reach 1.23 > 0.9
    assert r.status_code == 429 and r.json()["error"] == "daily_cap"


def test_cap_is_rechecked_when_a_queued_job_starts(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.2}, cfg_kw={"daily_cap_usd": 1.0, "rate_limit_jobs": 9})
    a = post_job(client, c.video).json()["id"]
    b = post_job(client, c.video).json()["id"]
    # something else spends the day's budget while b waits
    c.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with c.cfg.ledger_path.open("a") as f:
        f.write(json.dumps({"caller": "job:other|x", "cost_usd": 0.9,
                            "time": datetime.now(timezone.utc).isoformat()}) + "\n")
    wait_terminal(c.manager, a)
    jb = wait_terminal(c.manager, b)
    assert jb.status == "failed" and "daily app spend cap" in jb.error
    assert not any(x.get("caller", "").startswith(f"job:{b}|") for x in c.gw.calls)   # b never called a model


def test_rate_limiter_sliding_window():
    t = [0.0]
    rl = RateLimiter(3, 3600, clock=lambda: t[0])
    for _ in range(3):
        rl.check("1.1.1.1")
        rl.record("1.1.1.1")
    with pytest.raises(Refused) as e:
        rl.check("1.1.1.1")
    assert e.value.status == 429 and e.value.code == "rate_limited" and 3500 < e.value.retry_after <= 3600
    rl.check("2.2.2.2")                      # other clients are unaffected
    t[0] = 1800.0
    with pytest.raises(Refused):
        rl.check("1.1.1.1")
    t[0] = 3600.5                            # the oldest hit has left the window
    rl.check("1.1.1.1")
    rl.record("1.1.1.1")
    rl.release("1.1.1.1")                    # an undone record frees the slot again
    rl.check("1.1.1.1")


def test_api_rate_limit_per_client(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 2, "max_queue": 9})
    assert post_job(client, c.video).status_code == 200
    assert post_job(client, c.video).status_code == 200
    r = post_job(client, c.video)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and int(r.headers["retry-after"]) > 0
    # the third attempt left nothing behind
    assert len([d for d in c.cfg.jobs_dir.iterdir()]) == 2


def test_forwarded_header_ignored_unless_proxy_is_trusted(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 1, "max_queue": 9})
    assert post_job(client, c.video).status_code == 200
    # spoofing X-Forwarded-For does not buy a new allowance when the proxy is not trusted
    with open(c.video, "rb") as f:
        r = client.post("/api/jobs", data={"definitions": json.dumps({"a": "a long enough definition"})},
                        files={"video": ("x.mp4", f)}, headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 429
    client2, c2 = make_app(tmp_path / "t", monkeypatch, cfg_kw={"rate_limit_jobs": 1, "max_queue": 9, "trust_proxy": True})
    for ip, want in (("1.2.3.4", 200), ("1.2.3.4", 429), ("5.6.7.8", 200)):
        with open(c2.video, "rb") as f:
            r = client2.post("/api/jobs", data={"definitions": json.dumps({"a": "a long enough definition"})},
                             files={"video": ("x.mp4", f)}, headers={"X-Forwarded-For": ip})
        assert r.status_code == want
