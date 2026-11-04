"""Review fixes on open uploads: no showcase copy before the capacity checks, a hard upload deadline, no provider key in
the throwaway drills. No network."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from etho.gateway.config import ENV_KEYS
from tests.app_env import no_jobs, post_job
from tests.test_app_open_availability import asgi_post, make_open, visitor
from tests.test_app_open_uploads import YES, friendly_ok, up

DOC = Path(__file__).resolve().parents[1] / "docs" / "DEPLOY_OPEN_UPLOADS.md"


def test_rejected_showcase_revisions_never_fork_the_showcase(tmp_path, monkeypatch):
    from etho.app import server as S
    from tests.test_app_showcase import BODY, build_showcase
    forks = []
    real = S.fork_showcase
    monkeypatch.setattr(S, "fork_showcase", lambda *a, **k: forks.append(1) or real(*a, **k))
    # 1. the client already has a job queued
    client, c = make_open(tmp_path / "a", monkeypatch, worker=False)
    build_showcase(c, monkeypatch)
    assert up(client, c).status_code == 200
    for _ in range(3):
        friendly_ok(client.post("/api/projects/sc-calms21-x/revise", json=BODY), 429, "client_busy")
    # 2. the whole site is at its in-flight limit (another client's jobs)
    client, c = make_open(tmp_path / "b", monkeypatch, worker=False, max_jobs_per_client=0, max_in_flight=1)
    build_showcase(c, monkeypatch)
    assert post_job(visitor(c, "203.0.113.9"), c.video, **YES).status_code == 200
    for _ in range(3):
        friendly_ok(client.post("/api/projects/sc-calms21-x/revise", json=BODY), 503, "queue_full")
    assert forks == []                                                       # nothing was copied, ever
    assert len(list(c.cfg.jobs_dir.iterdir())) == 1                          # only the other visitor's job is there
    # 3. control: with room, the same request forks (once)
    client, c = make_open(tmp_path / "c", monkeypatch, worker=False)
    build_showcase(c, monkeypatch)
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert r.status_code == 200 and r.json()["forked"] and forks == [1]


def test_the_deadline_holds_even_when_the_body_keeps_arriving(tmp_path, monkeypatch):
    client, c = make_open(tmp_path, monkeypatch, worker=False, upload_deadline_s=0.3)
    head = (b'--xx\r\nContent-Disposition: form-data; name="video"; filename="a.mp4"\r\n'
            b'Content-Type: video/mp4\r\n\r\n')

    async def drip():                                   # a sender that never pauses: data is always ready
        sent, n = [], [0]

        async def receive():
            n[0] += 1
            await asyncio.sleep(0.01)
            return {"type": "http.request", "body": head if n[0] == 1 else b"x" * 100, "more_body": True}

        async def send(m):
            sent.append(m)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "path": "/api/jobs", "raw_path": b"/api/jobs", "query_string": b"", "scheme": "https",
                 "server": ("testserver", 443), "client": ("198.51.100.7", 1), "root_path": "",
                 "headers": [(b"host", b"testserver"), (b"content-length", b"1000000"),
                             (b"content-type", b"multipart/form-data; boundary=xx")]}
        t0 = asyncio.get_running_loop().time()
        await asyncio.wait_for(c.app(scope, receive, send), 5)        # must end by itself, not run on forever
        st = next(m["status"] for m in sent if m["type"] == "http.response.start")
        return st, asyncio.get_running_loop().time() - t0, n[0]

    async def scenario():
        st, took, reads = await drip()
        after = await asgi_post(c.app, client=("198.51.100.7", 4003), chunks=[b"x"], length=1)
        return st, took, reads, after
    st, took, reads, after = asyncio.run(scenario())
    assert st == 408 and 0.25 < took < 1.5                              # stopped at the deadline, not when data ran out
    assert reads < 80                                                   # about 0.3 s of 10 ms reads, not thousands
    assert after[0] != 429                                              # the slot was released
    assert no_jobs(c)


def test_the_throwaway_drills_run_without_any_provider_key():
    """The doc's local drills start the instance with every provider key variable unset (an exported key would beat
    ETHO_SECRETS). Sentinel credentials in the environment must not reach the gateway when started that way."""
    text = DOC.read_text()
    for var in ENV_KEYS.values():                       # the doc names every variable the gateway reads
        assert f"-u {var}" in text, var
    unset = [x for v in ENV_KEYS.values() for x in ("-u", v)]
    env = {**os.environ, **{v: "sentinel-" + v for v in ENV_KEYS.values()}, "ETHO_SECRETS": "/nonexistent/keys"}
    code = ("import os, pathlib; from etho.gateway.config import load_secrets; "
            "print(sorted(load_secrets(pathlib.Path(os.environ['ETHO_SECRETS']))))")
    inherited = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout
    assert "META_MODEL_API_KEY" in inherited                            # the trap is real: the sentinel gets through
    stripped = subprocess.run(["env", *unset, sys.executable, "-c", code], env=env, capture_output=True,
                              text=True).stdout
    assert stripped.strip() == "[]"
