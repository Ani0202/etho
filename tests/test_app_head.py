"""HEAD on every public GET route: same status and headers as GET, no body, nothing large read. No network."""

import asyncio
import json

from etho.app.headmw import HeadMiddleware
from tests.app_env import make_app, post_job, wait_terminal
from tests.app_helpers import make_video
from tests.test_app_polish import load_script
from tests.test_app_showcase import build_showcase

SKIP = {"date", "server"}


def same(client, url, status=200, **kw):
    g, h = client.get(url, **kw), client.head(url, **kw)
    assert g.status_code == h.status_code == status, (url, g.status_code, h.status_code)
    assert h.content == b""
    gh = {k: v for k, v in g.headers.items() if k not in SKIP}
    hh = {k: v for k, v in h.headers.items() if k not in SKIP}
    assert gh == hh, (url, gh, hh)
    return g, h


def test_head_matches_get_on_pages_api_static_and_projects(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    pid = "sc-calms21-x"
    for url in ("/", f"/p/{pid}", "/api/health", "/api/config", "/api/showcases", "/api/sample",
                f"/api/projects/{pid}", f"/api/projects/{pid}/overlay", f"/api/projects/{pid}/report",
                f"/api/projects/{pid}/audit", f"/api/projects/{pid}/trace",
                f"/api/projects/{pid}/strip/video_20_30.jpg", "/static/app.css", "/static/common.js"):
        g, _ = same(client, url)
        assert g.content, url                       # GET has a body, HEAD has none
    for url in ("/api/projects/sc-nope", "/api/projects/sc-nope/overlay", "/api/sample/clip", "/static/nope.js",
                f"/api/projects/{pid}/strip/video_1_2.jpg", "/api/jobs/nojob/events"):
        same(client, url, 404)
    assert client.head("/api/projects/sc-nope").headers["content-type"].startswith("application/json")


def test_head_sample_clip_and_write_methods_stay_closed(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    mod = load_script("make_sample_clip")
    vids = tmp_path / "v"
    vids.mkdir()
    make_video(vids / "seqA.mp4", seconds=8.0, w=64, h=48, fps=10)
    (tmp_path / "s.json").write_text(json.dumps({"dev": ["seqA"]}))
    mod.make_clip("seqA", videos=vids, splits=tmp_path / "s.json", out_dir=c.cfg.sample_dir, start_s=0, duration_s=5)
    g, h = same(client, "/api/sample/clip")
    assert h.headers["content-length"] == str(len(g.content)) and h.headers["content-type"] == "video/mp4"
    assert client.request("DELETE", "/api/sample/clip").status_code == 405


def test_head_follows_the_same_guards_as_get(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"read_only": True})
    same(client, "/api/config")
    same(client, "/api/health")
    assert client.head("/api/projects/x/revise").status_code == client.get("/api/projects/x/revise").status_code == 405


def test_head_on_the_event_stream_ends_and_leaks_no_stream_slot(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = post_job(client, c.video).json()["id"]
    wait_terminal(c.manager, jid)
    for _ in range(c.cfg.max_sse_per_client + 3):
        r = client.head(f"/api/jobs/{jid}/events")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream") and r.content == b""
    assert client.get(f"/api/jobs/{jid}/events").status_code == 200       # the slots were all released


def test_head_on_an_endless_stream_comes_back_at_once():
    seen = {}

    async def endless(scope, receive, send):
        seen["method"] = scope["method"]
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        while True:
            await send({"type": "http.response.body", "body": b"x", "more_body": True})
            if (await receive())["type"] == "http.disconnect":
                return
            await asyncio.sleep(0)

    sent = []

    async def run():
        async def send(m):
            sent.append(m)

        async def receive():
            await asyncio.sleep(3600)

        await asyncio.wait_for(HeadMiddleware(endless)({"type": "http", "method": "HEAD", "path": "/"},
                                                       receive, send), 5)

    asyncio.run(run())
    assert seen["method"] == "GET"
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[1]["body"] == b"" and sent[1]["more_body"] is False


def test_head_does_not_produce_a_large_body():
    produced = []

    async def big(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"17000000")]})
        for i in range(260):
            produced.append(i)
            await send({"type": "http.response.body", "body": b"\0" * 65536, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(HeadMiddleware(big)({"type": "http", "method": "HEAD", "path": "/"}, receive, send))
    assert produced == [0] and sent[0]["status"] == 200 and sum(len(m.get("body", b"")) for m in sent) == 0

    sent.clear()
    asyncio.run(HeadMiddleware(big)({"type": "http", "method": "GET", "path": "/"}, receive, send))
    assert len(produced) == 261 and len(sent) == 262          # GET is untouched
