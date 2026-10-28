"""Daily spend cap (with pre-estimate), per-client rate limit, cheap refusals, request-size gates, headers."""

import json
import threading
import time
from datetime import datetime, timezone

import pytest

from etho.app.config import AppConfig
from etho.app.guards import RateLimiter, Refused, SpendGuard, client_key
from etho.app.jobgw import AppLedger
from tests.app_env import make_app, no_jobs, post_job, wait_terminal


def test_estimate_uses_measured_rates_with_margin():
    cfg = AppConfig()
    # SAM $0.003/s + tier-1 $0.003/s (measured $0.002 and $0.0012) + $0.05 fixed + $0.06 per behavior
    assert cfg.estimate_job_usd(60, 3) == round(60 * 0.006 + 0.05 + 3 * 0.06, 4)
    assert cfg.estimate_job_usd(10, 0) == round(10 * 0.006 + 0.05, 4)
    assert cfg.daily_cap_usd == 5.0 and cfg.tag == "dev" and cfg.rate_limit_jobs == 3 and cfg.max_duration_s == 300
    assert cfg.total_cap_usd is None and cfg.retention_hours == 24.0 and not cfg.public and cfg.live_until is None


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
    # the window is a rolling 24 h, not a calendar day: 15 h later the $4 still counts, 25 h later it has aged out
    g2 = SpendGuard(AppConfig(daily_cap_usd=5.0), led, now=lambda: datetime(2026, 10, 29, 0, tzinfo=timezone.utc))
    assert g2.check(0.9)["spent_today_usd"] == 4.0
    g3 = SpendGuard(AppConfig(daily_cap_usd=5.0), led, now=lambda: datetime(2026, 10, 29, 10, tzinfo=timezone.utc))
    assert g3.check(4.9)["spent_today_usd"] == 0.0


def test_job_refused_by_api_when_cap_would_be_crossed_and_nothing_is_spent(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 5.0})
    c.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    c.cfg.ledger_path.write_text(json.dumps({"caller": "job:old|x", "cost_usd": 4.9,
                                             "time": datetime.now(timezone.utc).isoformat()}) + "\n")
    r = post_job(client, c.video)           # 6 s, 2 behaviors: estimate ~ $0.21 > $0.10 left
    assert r.status_code == 429 and r.json()["error"] == "daily_cap" and "cap" in r.json()["message"]
    assert not c.gw.calls and no_jobs(c)
    assert client.get("/api/config").json()["cap"]["remaining_usd"] == pytest.approx(0.1)


def test_estimate_between_minimum_and_actual_is_refused_after_the_probe(tmp_path, monkeypatch):
    # $0.17 left clears the middleware's minimum estimate (about $0.12) but not this video's ($0.21)
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 5.0})
    c.cfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    c.cfg.ledger_path.write_text(json.dumps({"caller": "job:old|x", "cost_usd": 4.83,
                                             "time": datetime.now(timezone.utc).isoformat()}) + "\n")
    r = post_job(client, c.video)
    assert r.status_code == 429 and r.json()["error"] == "daily_cap" and no_jobs(c)


def test_default_cap_admits_a_five_minute_upload(tmp_path):
    cfg = AppConfig()
    g = SpendGuard(cfg, AppLedger(tmp_path / "none.jsonl"))
    for nb in (3, cfg.max_behaviors):
        g.check(cfg.estimate_job_usd(cfg.max_duration_s, nb))      # the longest allowed upload fits under $5
    one = cfg.estimate_job_usd(cfg.max_duration_s, 3)
    g.check(one, outstanding_usd=one)                              # two fit
    with pytest.raises(Refused):                                   # but the day's budget is not unlimited
        g.check(one, outstanding_usd=2.5 * one)


def test_queued_jobs_count_against_the_cap(tmp_path, monkeypatch):
    # estimate ~ $0.21 and ceiling $0.71 (reserved at admission): the first reserves $0.71 of the $1.00 cap, the
    # second fits in the remaining $0.29, the third has nothing left to reserve
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.3}, cfg_kw={"daily_cap_usd": 1.0, "rate_limit_jobs": 9})
    assert post_job(client, c.video).status_code == 200
    assert post_job(client, c.video).status_code == 200
    r = post_job(client, c.video)
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


def test_concurrent_submits_at_the_cap_admit_only_what_fits(tmp_path, monkeypatch):
    # the cap fits one ~$0.21 estimate: simultaneous uploads must not all be admitted
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 0.3, "rate_limit_jobs": 9, "max_queue": 9},
                         gw_kw={"delay": 0.3})
    out = []
    ts = [threading.Thread(target=lambda: out.append(post_job(client, c.video))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    codes = sorted(r.status_code for r in out)
    assert codes.count(200) == 1 and all(x in (200, 429, 503) for x in codes), codes
    assert len(list(c.cfg.jobs_dir.iterdir())) == 1


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


def test_rate_limiter_prunes_idle_clients():
    t = [0.0]
    rl = RateLimiter(3, 10, clock=lambda: t[0])
    for i in range(300):
        rl.record(f"c{i}")
    t[0] = 11.0
    rl.record("fresh")
    assert list(rl._hits) == ["fresh"]       # expired clients are dropped, not kept forever
    rl.check("never-seen")
    assert "never-seen" not in rl._hits      # checking an unknown client allocates nothing


def test_client_key_groups_ipv6_by_64():
    assert client_key("203.0.113.9") == "203.0.113.9"
    assert client_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == client_key("2001:db8:1:2:1::1") == "2001:db8:1:2::/64"
    assert client_key("2001:db8:1:3::1") != client_key("2001:db8:1:2::1")
    assert client_key("testclient") == "testclient" and client_key(None) == "unknown"


def test_api_rate_limit_per_client(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 2, "max_queue": 9})
    assert post_job(client, c.video).status_code == 200
    assert post_job(client, c.video).status_code == 200
    r = post_job(client, c.video)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and int(r.headers["retry-after"]) > 0
    assert len(list(c.cfg.jobs_dir.iterdir())) == 2      # the third attempt left nothing behind


def test_forwarded_header_is_never_read_by_the_app(tmp_path, monkeypatch):
    # uvicorn rewrites request.client from X-Forwarded-For only for --forwarded-allow-ips; the app itself ignores it
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 1, "max_queue": 9})
    assert post_job(client, c.video).status_code == 200
    for ip in ("9.9.9.9", "1.2.3.4, 5.6.7.8"):
        r = post_job(client, c.video, headers={"X-Forwarded-For": ip})
        assert r.status_code == 429 and r.json()["error"] == "rate_limited"


def test_cheap_refusals_happen_before_the_upload_is_stored(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 1, "max_queue": 9})
    assert post_job(client, c.video).status_code == 200
    n_dirs = len(list(c.cfg.jobs_dir.iterdir()))
    from etho.app import server as srv
    seen = []
    orig = srv.check_video
    monkeypatch.setattr(srv, "check_video", lambda *a, **k: seen.append(1) or orig(*a, **k))
    r = post_job(client, c.video)               # 2nd job in the hour: refused by the middleware, body never parsed
    assert r.status_code == 429 and not seen and len(list(c.cfg.jobs_dir.iterdir())) == n_dirs
    client2, c2 = make_app(tmp_path / "q", monkeypatch, cfg_kw={"max_queue": 0, "rate_limit_jobs": 9})
    r = post_job(client2, c2.video)
    assert r.status_code == 503 and r.json()["error"] == "queue_full" and not c2.cfg.jobs_dir.exists()


def test_upload_concurrency_is_bounded(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"max_concurrent_uploads": 1, "rate_limit_jobs": 9, "max_queue": 9})
    from etho.app import server as srv
    orig = srv.check_video
    gate = threading.Event()
    monkeypatch.setattr(srv, "check_video", lambda *a, **k: (gate.wait(5), orig(*a, **k))[1])
    out = []
    t1 = threading.Thread(target=lambda: out.append(post_job(client, c.video)))
    t1.start()
    time.sleep(0.5)                              # first upload is parked inside the probe, holding the slot
    r2 = post_job(client, c.video)
    gate.set()
    t1.join()
    assert r2.status_code == 503 and r2.json()["error"] == "busy" and out[0].status_code == 200


def test_411_and_413_before_the_body_is_read(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"max_upload_mb": 1.0})
    hdr = {"Content-Type": "multipart/form-data; boundary=x"}
    r = client.post("/api/jobs", content=iter([b"abc", b"def"]), headers=hdr)      # chunked: no Content-Length
    assert r.status_code == 411 and r.json()["error"] == "length_required"
    r = client.post("/api/projects/j1/revise", content=iter([b"{}"]))
    assert r.status_code == 411
    r = client.post("/api/jobs", content=b"x" * 2_100_000, headers=hdr)           # declared length over the limit
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    assert not c.cfg.ledger_path.exists()


def test_non_upload_post_bodies_are_capped(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    big = {"corrections": [{"kind": "missed", "behavior": "attack", "start_s": 1, "end_s": 2, "note": "x" * 300_000}]}
    r = client.post("/api/projects/j1/revise", json=big)
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    r = client.post("/api/jobs/j1/cancel", content=b"y" * 300_000)
    assert r.status_code == 413
    assert client.post("/api/jobs/jnone/cancel").status_code == 404                 # empty POST is fine


def test_security_headers_on_pages_and_api(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    for path in ("/", "/p/anything", "/static/app.css", "/api/config"):
        r = client.get(path)
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "base-uri 'none'" in csp and "form-action 'self'" in csp
        assert "frame-ancestors 'none'" in csp
    r = client.post("/api/jobs", content=b"x", headers={"Content-Type": "text/plain", "Content-Length": "1"})
    assert r.headers["x-content-type-options"] == "nosniff"          # also on refusals and errors
