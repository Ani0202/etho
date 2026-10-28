"""Review fixes: revision numbering after a failure, per-job spend ceiling, hardened ffprobe/ffmpeg, retention,
SSE limits."""

import json
import subprocess
import threading
import time

import pytest

from etho.app import backend as BK
from etho.app.config import AppConfig
from etho.app.jobgw import JobBudgetExceeded, JobCancelled, JobGateway
from etho.app.validate import ValidationError, check_video, probe, remux_clean
from tests.app_env import make_app, post_job, wait_for, wait_terminal
from tests.app_helpers import FakeGateway, make_video

REVISE = {"corrections": [{"kind": "wrong", "behavior": "investigation", "start_s": 5.0, "end_s": 6.0,
                           "correct_label": "none"}]}


def run_job(client, c):
    jid = post_job(client, c.video).json()["id"]
    assert wait_terminal(c.manager, jid).status == "done"
    return jid


def test_failed_revision_does_not_break_the_next_one(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 10})
    jid = run_job(client, c)
    good = BK.run_revise
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider 503")
        return good(*a, **k)
    monkeypatch.setattr(BK, "run_revise", flaky)
    r1 = client.post(f"/api/projects/{jid}/revise", json=REVISE)
    j1 = wait_terminal(c.manager, r1.json()["job_id"])
    assert j1.status == "failed" and "provider 503" in j1.error
    p = client.get(f"/api/projects/{jid}").json()
    assert p["latest_version"] == 0 and len(p["versions"]) == 1          # the failed one is not a version
    r2 = client.post(f"/api/projects/{jid}/revise", json=REVISE)         # rev 2, built on version 0
    assert r2.status_code == 200 and r2.json()["rev"] == 2
    assert c.manager.jobs[r2.json()["job_id"]].params["base_version"] == 0
    assert wait_terminal(c.manager, r2.json()["job_id"]).status == "done"
    p = client.get(f"/api/projects/{jid}").json()
    assert p["latest_version"] == 2 and [v["v"] for v in p["versions"]] == [0, 2]
    assert p["versions"][1]["diff"]["per_behavior"]["investigation"]["bouts_before"] == 1
    # a third revision builds on version 2 and carries the corrections along
    r3 = client.post(f"/api/projects/{jid}/revise", json={"corrections": [
        {"kind": "missed", "behavior": "attack", "start_s": 2.0, "end_s": 3.0}]})
    assert c.manager.jobs[r3.json()["job_id"]].params["base_version"] == 2 and r3.json()["n_corrections"] == 2
    assert wait_terminal(c.manager, r3.json()["job_id"]).status == "done"


def test_restart_during_a_revision_leaves_later_revisions_working(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = run_job(client, c)
    d = c.cfg.jobs_dir / jid / "revisions" / "1"          # a revision interrupted by a crash: job.json only
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({"id": f"{jid}-r1", "project_id": jid, "kind": "revise", "rev": 1,
                                            "status": "running", "stages": [{"name": "revise", "status": "running"}]}))
    from etho.app.server import create_app
    app2 = create_app(c.cfg, gateway_factory=lambda: c.gw)
    assert app2.state.manager.jobs[f"{jid}-r1"].status == "failed"
    from fastapi.testclient import TestClient
    c2 = TestClient(app2)
    r = c2.post(f"/api/projects/{jid}/revise", json=REVISE)
    assert r.status_code == 200 and r.json()["rev"] == 2
    assert wait_terminal(app2.state.manager, r.json()["job_id"]).status == "done"


def test_job_spend_ceiling_stops_further_calls(tmp_path, monkeypatch):
    # estimate for the 6 s test video is about $0.21, so the ceiling is $0.71; each fake call costs $0.4
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"cost": 0.4})
    jid = post_job(client, c.video).json()["id"]
    j = wait_terminal(c.manager, jid)
    assert j.status == "failed" and "spend ceiling" in j.error
    assert len(c.gw.calls) == 2                      # plan call, SAM call; the third was refused before it was sent
    assert j.gw.total_cost == pytest.approx(0.8)


def test_job_gateway_ceiling_and_cancel_unit(tmp_path):
    fake = FakeGateway(tmp_path / "s.jsonl", cost=0.3)
    flag = {"c": False}
    jg = JobGateway(fake, job_id="j1", tag="dev", max_cost_usd=0.5, cancelled=lambda: flag["c"])
    msg = [{"role": "user", "content": "hi"}]
    jg.chat("meta", "muse-spark-1.3", msg, effort="low")
    jg.chat("meta", "muse-spark-1.3", msg, effort="low")         # total 0.6 >= ceiling
    with pytest.raises(JobBudgetExceeded):
        jg.chat("meta", "muse-spark-1.3", msg, effort="low")
    with pytest.raises(JobBudgetExceeded):
        jg.sam("mouse", video="v.mp4", n_frames=5)
    jg2 = JobGateway(fake, job_id="j2", tag="dev", cancelled=lambda: flag["c"])
    jg2.chat("meta", "muse-spark-1.3", msg, effort="low")
    flag["c"] = True
    with pytest.raises(JobCancelled):
        jg2.chat("meta", "muse-spark-1.3", msg, effort="low")
    assert len(fake.calls) == 3


def test_ceiling_never_exceeds_the_days_headroom(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 0.5}, gw_kw={"cost": 0.1})
    jid = post_job(client, c.video).json()["id"]       # estimate $0.21 fits; ceiling = min($0.71, $0.5 headroom)
    j = wait_terminal(c.manager, jid)
    assert j.gw.max_cost_usd == pytest.approx(0.5)


# ---- hardened ffprobe / ffmpeg ------------------------------------------------------------------
def test_probe_counts_real_frames_and_rejects_odd_inputs(tmp_path):
    v = make_video(tmp_path / "v.mp4", 6)
    info = probe(v)
    assert info.frames == 60 and info.duration_s == pytest.approx(6, abs=0.3)
    fast = make_video(tmp_path / "fast.mp4", 2, fps=200)
    with pytest.raises(ValidationError) as e:
        probe(fast)
    assert e.value.code == "bad_fps"
    # a playlist / concat script named .mp4 is parsed as MP4 (forced demuxer) and fails; nothing is fetched
    (tmp_path / "list.mp4").write_text("#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/x.ts\n")
    with pytest.raises(ValidationError) as e:
        probe(tmp_path / "list.mp4")
    assert e.value.code == "unreadable"
    (tmp_path / "concat.avi").write_text("ffconcat version 1.0\nfile '/etc/passwd'\n")
    with pytest.raises(ValidationError):
        probe(tmp_path / "concat.avi")
    # container must match the extension
    (tmp_path / "mislabeled.avi").write_bytes(v.read_bytes())
    with pytest.raises(ValidationError):
        probe(tmp_path / "mislabeled.avi")
    (tmp_path / "ok.mov").write_bytes(v.read_bytes())               # mp4 and mov share a demuxer
    assert probe(tmp_path / "ok.mov").frames == 60
    cfg = AppConfig(root=tmp_path, max_duration_s=300)
    assert check_video(v, cfg).frames == 60


def test_frame_count_limit_catches_a_container_that_understates_length(tmp_path):
    v = make_video(tmp_path / "v.mp4", 6)
    cfg = AppConfig(root=tmp_path, max_duration_s=0.4)     # 60 frames > 0.4 s * 125 fps
    with pytest.raises(ValidationError) as e:
        check_video(v, cfg)
    assert e.value.status == 413


def test_remux_clean_is_h264_10fps_video_only_and_cut_at_the_limit(tmp_path):
    src = tmp_path / "src.avi"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-c:v", "mpeg4", "-c:a", "mp3", str(src)],
                   check=True)
    dst = tmp_path / "clean.mp4"
    remux_clean(src, dst, AppConfig(root=tmp_path, max_duration_s=5))
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-count_packets", "-print_format", "json",
                                   str(dst)], capture_output=True, text=True, check=True).stdout)
    assert [s["codec_type"] for s in p["streams"]] == ["video"]
    s = p["streams"][0]
    assert s["codec_name"] == "h264" and s["avg_frame_rate"] == "10/1" and 55 <= int(s["nb_read_packets"]) <= 62
    (tmp_path / "junk.avi").write_text("junk")
    with pytest.raises(ValidationError) as e:
        remux_clean(tmp_path / "junk.avi", tmp_path / "x.mp4", AppConfig(root=tmp_path))
    assert e.value.code == "remux_failed" and not (tmp_path / "x.mp4").exists()


def test_the_pipeline_only_sees_the_clean_mp4(tmp_path, monkeypatch):
    seen = []
    client, c = make_app(tmp_path, monkeypatch)
    orig = BK.track_video
    monkeypatch.setattr(BK, "track_video", lambda src, *a, **k: seen.append(src) or orig(src, *a, **k))
    jid = run_job(client, c)
    d = c.cfg.jobs_dir / jid
    assert seen and seen[0].name == "clean.mp4"
    assert (d / "clean.mp4").exists() and not list(d.glob("upload.*"))   # the original container is dropped


# ---- retention and SSE limits -------------------------------------------------------------------
def test_retention_removes_old_finished_projects_only(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"retention_hours": 1.0, "rate_limit_jobs": 9})
    old, fresh = run_job(client, c), run_job(client, c)
    jf = c.cfg.jobs_dir / old / "job.json"                  # the age is counted from the upload (job.json created_at)
    d = json.loads(jf.read_text())
    d["created_at"] = time.time() - 7200
    jf.write_text(json.dumps(d))
    assert c.manager.cleanup() == [old]
    assert not (c.cfg.jobs_dir / old).exists() and (c.cfg.jobs_dir / fresh).exists()
    assert client.get(f"/api/projects/{old}").status_code == 404 and old not in c.manager.jobs
    assert client.get(f"/api/projects/{fresh}").status_code == 200


def test_retention_never_removes_a_project_with_an_active_job(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"retention_hours": 0.0001}, gw_kw={"delay": 0.4})
    jid = post_job(client, c.video).json()["id"]
    wait_for(lambda: c.manager.jobs[jid].status == "running", what="job start")
    assert c.manager.cleanup(now=time.time() + 3600) == []
    assert wait_terminal(c.manager, jid).status == "done"
    assert c.manager.cleanup(now=time.time() + 3600) == [jid]


def test_sse_streams_per_client_are_capped(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"max_sse_per_client": 1}, gw_kw={"delay": 0.5})
    jid = post_job(client, c.video).json()["id"]
    started, done = threading.Event(), threading.Event()

    def hold():
        with client.stream("GET", f"/api/jobs/{jid}/events") as r:
            started.set()
            for _ in r.iter_lines():
                pass
        done.set()
    t = threading.Thread(target=hold)
    t.start()
    assert started.wait(10)
    time.sleep(0.3)
    r = client.get(f"/api/jobs/{jid}/events")
    assert r.status_code == 429 and r.json()["error"] == "too_many_streams"
    t.join(60)
    assert done.is_set()
    with client.stream("GET", f"/api/jobs/{jid}/events") as r2:       # the slot was released when the stream ended
        assert r2.status_code == 200
        list(r2.iter_lines())


def test_end_event_is_always_the_final_state(tmp_path, monkeypatch):
    """The status and the numbers sent with it come from one snapshot, so an 'end' event never carries a stale
    running stage list or an elapsed time lower than an earlier event."""
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.05})
    for _ in range(3):
        jid = post_job(client, c.video).json()["id"]
        with client.stream("GET", f"/api/jobs/{jid}/events") as r:
            frames = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
        assert frames[-1]["status"] == "done" and all(s["status"] == "done" for s in frames[-1]["stages"])
        assert [f["elapsed_s"] for f in frames] == sorted(f["elapsed_s"] for f in frames)
        assert all(f["status"] != "done" for f in frames[:-1])
        wait_terminal(c.manager, jid)
        time.sleep(0.05)
