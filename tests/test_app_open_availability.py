"""Open mode, availability: one upload and one job per client, a deadline for the whole upload body, a limit on rejected
uploads. No network; the gateway is faked."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from etho.app.config import AppConfig
from etho.app.jobs import ClientBusy, Job
from tests.app_env import make_app, no_jobs, post_job, wait_for, wait_terminal
from tests.markers import needs_bwrap
from tests.test_app_hosting import REVISE, START
from tests.test_app_open_uploads import YES, clean_env, friendly_ok, points_onward, up


def make_open(tmp_path, monkeypatch, *, t=None, worker=True, **cfg):
    kw = {"public": True, "open": True, "tag": "dev", "rate_limit_jobs": 50, "max_queue": 9,
          "gateway_cache_root": tmp_path / "gwc", "work_root": tmp_path, "maintenance_interval_s": 3600.0, **cfg}
    return make_app(tmp_path, monkeypatch, base_url="https://testserver", now=lambda: START,
                    clock=(lambda: t[0]) if t else None, cfg_kw=kw, gw_kw={"cache_root": tmp_path / "gwc"},
                    app_kw={"isolation": lambda: {"mode": "bwrap", "detail": "t"}, "start_worker": worker})


def visitor(c, ip):
    return TestClient(c.app, base_url="https://testserver", client=(ip, 5000))


def test_availability_defaults_in_open_mode_only(monkeypatch):
    clean_env(monkeypatch)
    o = AppConfig(public=True, open=True)
    assert (o.max_uploads_per_client, o.max_jobs_per_client, o.max_rejects_per_hour, o.upload_deadline_s) == (
        1, 1, 10, 120.0)
    n = AppConfig(public=True)
    assert (n.max_uploads_per_client, n.max_jobs_per_client, n.max_rejects_per_hour, n.upload_deadline_s) == (
        0, 0, 0, 0.0)
    monkeypatch.setenv("ETHO_APP_MAX_JOBS_PER_CLIENT", "2")
    monkeypatch.setenv("ETHO_APP_UPLOAD_DEADLINE_S", "30")
    e = AppConfig(public=True, open=True)
    assert e.max_jobs_per_client == 2 and e.upload_deadline_s == 30.0


@needs_bwrap
def test_one_job_per_client_in_the_middleware_and_in_submit(tmp_path, monkeypatch):
    client, c = make_open(tmp_path, monkeypatch, worker=False)
    a = up(client, c)
    assert a.status_code == 200
    r = up(client, c)                                              # the middleware refuses before the body is read
    f = friendly_ok(r, 429, "client_busy")
    assert "already have a run in progress" in f and "finish" in f and "showcase" in f.lower()
    assert len(list(c.cfg.jobs_dir.iterdir())) == 1
    assert post_job(visitor(c, "203.0.113.9"), c.video, **YES).status_code == 200      # another visitor is not held back
    # submit() refuses on its own (it does not rely on the middleware): same client key, bypassing HTTP
    d = c.cfg.jobs_dir / "jx"
    d.mkdir()
    with pytest.raises(ClientBusy):
        c.manager.submit(Job("jx", "jx", "analyze", d, est_cost_usd=0.1, client="testclient"))
    assert "jx" not in c.manager.jobs
    assert client.post(f"/api/jobs/{a.json()['id']}/cancel").json() == {"cancelled": True}     # cancelling frees it
    assert up(client, c).status_code == 200


@needs_bwrap
def test_a_revision_is_refused_while_the_client_has_a_job(tmp_path, monkeypatch):
    client, c = make_open(tmp_path, monkeypatch)
    a = up(client, c).json()["id"]
    wait_terminal(c.manager, a)
    c.manager.stop()                                               # the worker stops: what is submitted stays queued
    assert up(client, c).status_code == 200                        # the client's one queued job
    r = client.post(f"/api/projects/{a}/revise", json=REVISE)      # refused by submit(), and cleaned up
    friendly_ok(r, 429, "client_busy")
    assert not list((c.cfg.jobs_dir / a / "revisions").glob("*"))


def asgi_post(app, *, client, chunks, length, wait=None):
    """Drive the ASGI app by hand: send `chunks`, then stall on `wait` (an asyncio.Event). Returns (status, body)."""
    async def run():
        sent, left = [], list(chunks)

        async def receive():
            if left:
                return {"type": "http.request", "body": left.pop(0), "more_body": bool(left) or wait is not None}
            if wait is not None:
                await wait.wait()
            return {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "path": "/api/jobs", "raw_path": b"/api/jobs", "query_string": b"", "scheme": "https",
                 "server": ("testserver", 443), "client": client, "root_path": "",
                 "headers": [(b"host", b"testserver"), (b"content-length", str(length).encode()),
                             (b"content-type", b"multipart/form-data; boundary=xx")]}
        await app(scope, receive, send)
        st = next(m["status"] for m in sent if m["type"] == "http.response.start")
        return st, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return run()


def test_one_upload_at_a_time_per_client_and_the_body_deadline_frees_the_slot(tmp_path, monkeypatch):
    client, c = make_open(tmp_path, monkeypatch, worker=False, upload_deadline_s=0.5)

    async def scenario():
        never = asyncio.Event()
        slow = asyncio.create_task(asgi_post(c.app, client=("198.51.100.7", 4000), chunks=[b"--xx\r\n"], length=5000,
                                             wait=never))
        await asyncio.sleep(0.15)                                  # the slow sender holds this client's upload slot
        second = await asgi_post(c.app, client=("198.51.100.7", 4001), chunks=[b"x"], length=1)
        other = await asgi_post(c.app, client=("198.51.100.8", 4002), chunks=[b"x"], length=1)
        first = await slow                                         # the deadline stops it and answers 408
        after = await asgi_post(c.app, client=("198.51.100.7", 4003), chunks=[b"x"], length=1)
        return first, second, other, after
    first, second, other, after = asyncio.run(scenario())
    assert second[0] == 429 and json.loads(second[1])["error"] == "client_busy"
    assert "upload in progress" in json.loads(second[1])["message"]
    assert other[0] != 429                                         # another client is not held back
    assert first[0] == 408 and json.loads(first[1])["error"] == "upload_timeout"
    assert after[0] != 429                                         # the slot came back after the deadline
    assert no_jobs(c)


def test_there_is_no_body_deadline_without_open_mode(tmp_path, monkeypatch):
    client, c = make_open(tmp_path, monkeypatch, public=False, open=False, upload_deadline_s=None)
    assert c.cfg.upload_deadline_s == 0.0

    async def scenario():
        ev = asyncio.Event()
        t = asyncio.create_task(asgi_post(c.app, client=("198.51.100.9", 1), chunks=[b"--xx\r\n"], length=5000, wait=ev))
        await asyncio.sleep(0.3)
        done = t.done()
        ev.set()
        await t
        return done
    assert asyncio.run(scenario()) is False                        # it simply waited for the body


@needs_bwrap
def test_rejected_uploads_are_limited_per_client_per_hour(tmp_path, monkeypatch):
    t = [0.0]
    client, c = make_open(tmp_path, monkeypatch, t=t, max_rejects_per_hour=3)
    for _ in range(3):
        friendly_ok(post_job(client, c.video), 400, "consent_required")          # three rejections
    r = post_job(client, c.video, **YES)                                         # now even a good upload is paused
    f = friendly_ok(r, 429, "too_many_rejected")
    assert "paused" in f and "an hour" in f and int(r.headers["retry-after"]) > 0
    points_onward(f)
    assert post_job(visitor(c, "203.0.113.9"), c.video, **YES).status_code == 200    # another visitor is fine
    t[0] = 3601.0
    assert post_job(client, c.video, **YES).status_code == 200                   # the hour passed
    wait_for(lambda: not c.manager.busy())


@needs_bwrap
def test_refusals_that_are_not_rejections_do_not_count(tmp_path, monkeypatch):
    t = [0.0]
    client, c = make_open(tmp_path, monkeypatch, t=t, max_rejects_per_hour=2, rate_limit_jobs=1, worker=False)
    assert up(client, c).status_code == 200
    for _ in range(4):                                                           # rate limited, not rejected
        assert up(client, c).status_code == 429
    assert up(visitor(c, "203.0.113.9"), c).status_code == 200                   # nothing recorded against anyone
    assert c.app.state.limiter.usage("testclient") == 1


def test_rejection_limit_is_off_without_open_mode(tmp_path, monkeypatch):
    client, c = make_open(tmp_path, monkeypatch, open=False, max_rejects_per_hour=None, access_code_file=None)
    assert c.cfg.max_rejects_per_hour == 0
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not a video")
    for _ in range(12):                                                          # public, no code: read-only, so 403
        assert post_job(client, bad).status_code in (400, 403)


def test_the_readme_describes_open_mode_not_a_code_or_off_uploads():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "uploads are off on" not in text
    first = " ".join(text[:text.index("## ", text.index("See it live"))].split())
    for phrase in ("no login and no access code", "10 runs", "2 minutes", "I have the right to upload this video, and it contains no people",
                   "sample clip"):
        assert phrase in first, phrase
    assert "ETHO_APP_OPEN=1" in text and "without `ETHO_APP_OPEN` a public app" in text
