"""Job state machine, queue, SSE progress format, job-attributed gateway and ledger."""

import json
import time

from etho.app.jobgw import AppLedger, JobGateway, caller_for, job_of_caller, stage_of_caller
from etho.app.jobs import ANALYZE_STAGES, REVISE_STAGES, Job, Stage, sse
from tests.app_env import make_app, post_job, wait_terminal
from tests.app_helpers import FakeGateway


def parse_sse(text: str) -> list[tuple[str, dict, str | None]]:
    out = []
    for block in text.strip().split("\n\n"):
        ev, data, eid = None, None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line.startswith("id: "):
                eid = line[4:]
        if ev:
            out.append((ev, data, eid))
    return out


def test_sse_frame_format():
    f = sse("progress", {"a": 1, "b": "x"}, 7)
    assert f == 'id: 7\nevent: progress\ndata: {"a":1,"b":"x"}\n\n'
    assert sse("end", {}) == "event: end\ndata: {}\n\n"


def test_sse_stream_reports_stage_message_elapsed_and_cost(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.15})
    jid = post_job(client, c.video).json()["id"]
    with client.stream("GET", f"/api/jobs/{jid}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = parse_sse(body)
    kinds = [e[0] for e in events]
    assert kinds[-1] == "end" and set(kinds[:-1]) == {"progress"} and len(events) >= 4
    snaps = [e[1] for e in events]
    for s in snaps:
        assert {"id", "status", "stage", "stage_index", "n_stages", "message", "elapsed_s", "cost_usd",
                "stages", "queue_position", "est_cost_usd"} <= set(s)
        assert [x["name"] for x in s["stages"]] == list(ANALYZE_STAGES)
        assert all({"name", "status", "elapsed_s", "message", "cost_usd"} <= set(x) for x in s["stages"])
    ids = [int(e[2]) for e in events]
    assert ids == sorted(ids)                                         # monotonic event ids
    running = [s for s in snaps if s["status"] == "running"]
    assert any(s["stage"] == "synthesize" and s["message"] for s in running)   # stage + message while running
    costs = [s["cost_usd"] for s in snaps]
    assert costs == sorted(costs) and costs[-1] > 0                     # running cost from the ledger, never down
    elapsed = [s["elapsed_s"] for s in snaps]
    assert elapsed[-1] > 0.3 and elapsed == sorted(elapsed)
    last = snaps[-1]
    assert last["status"] == "done" and last["stage"] is None and all(x["status"] == "done" for x in last["stages"])
    assert abs(last["cost_usd"] - c.manager.ledger.job_cost(jid)) < 1e-9


def test_sse_unknown_job_is_404(tmp_path, monkeypatch):
    client, _ = make_app(tmp_path, monkeypatch)
    assert client.get("/api/jobs/jdoesnotexist/events").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc/events").status_code == 404


def test_jobs_run_one_at_a_time_in_fifo_order(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.1}, cfg_kw={"rate_limit_jobs": 10})
    ids = [post_job(client, c.video).json()["id"] for _ in range(3)]
    time.sleep(0.2)
    snaps = [client.get(f"/api/jobs/{i}").json() for i in ids]
    assert snaps[0]["status"] == "running" and snaps[0]["queue_position"] == 0
    assert [s["status"] for s in snaps[1:]] == ["queued", "queued"] and [s["queue_position"] for s in snaps[1:]] == [1, 2]
    jobs = [wait_terminal(c.manager, i) for i in ids]
    assert [j.status for j in jobs] == ["done"] * 3
    for a, b in zip(jobs, jobs[1:], strict=False):          # b starts only after a finished
        assert b.started_mono >= a.finished_mono


def test_queue_limit_and_cancel(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.2}, cfg_kw={"rate_limit_jobs": 20, "max_queue": 1})
    a = post_job(client, c.video).json()["id"]
    time.sleep(0.1)
    b = post_job(client, c.video).json()["id"]            # waits in the queue
    r = post_job(client, c.video)                          # queue full
    assert r.status_code == 503 and r.json()["error"] == "queue_full"
    assert not (c.cfg.jobs_dir / "x").exists() and len(list(c.cfg.jobs_dir.iterdir())) == 2  # rejected upload removed
    assert client.post(f"/api/jobs/{b}/cancel").json() == {"cancelled": True}
    assert client.get(f"/api/jobs/{b}").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{b}/cancel").json() == {"cancelled": False}
    assert wait_terminal(c.manager, a).status == "done"
    assert client.get(f"/api/jobs/{b}").json()["stages"][0]["status"] == "pending"   # never ran


def test_cancel_running_job_stops_between_stages(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.3})
    a = post_job(client, c.video).json()["id"]
    time.sleep(0.15)
    assert client.post(f"/api/jobs/{a}/cancel").json()["cancelled"] is True
    j = wait_terminal(c.manager, a)
    assert j.status == "cancelled" and any(s.status == "pending" for s in j.stages)


def test_restart_marks_unfinished_jobs_failed_and_never_resumes(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    d = c.cfg.jobs_dir / "jabc123"
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps({
        "id": "jabc123", "project_id": "jabc123", "kind": "analyze", "status": "running", "est_cost_usd": 0.3,
        "stages": [{"name": "plan", "status": "done"}, {"name": "track", "status": "running"},
                   {"name": "synthesize", "status": "pending"}]}))
    from etho.app.server import create_app
    app2 = create_app(c.cfg, gateway_factory=lambda: c.gw)
    j = app2.state.manager.jobs["jabc123"]
    assert j.status == "failed" and "restart" in j.error
    assert [s.status for s in j.stages] == ["done", "failed", "pending"]
    assert json.loads((d / "job.json").read_text())["status"] == "failed"
    assert not c.gw.calls                                         # nothing was re-run


def test_stage_lists():
    assert ANALYZE_STAGES == ("validate", "plan", "track", "synthesize", "predict", "tier1", "report", "overlay")
    assert REVISE_STAGES == ("revise", "predict", "tier1", "report", "overlay")
    j = Job("j1", "j1", "analyze", None)
    assert j.status == "queued" and Stage("plan").status == "pending"


# ---- job-attributed gateway and ledger ---------------------------------------------------------
def test_job_gateway_forces_tag_prefixes_caller_and_records(tmp_path):
    led = tmp_path / "spend.jsonl"
    fake = FakeGateway(led, cost=0.25)
    seen = []
    jg = JobGateway(fake, job_id="jx1", tag="dev", calls_path=tmp_path / "calls.jsonl", on_cost=seen.append)
    jg.stage = "plan"
    jg.chat("meta", "muse-spark-1.3", [{"role": "user", "content": [
        {"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]}],
        effort="low", tag="eval-llm", caller="etho.perception.planner", max_tokens=5)
    jg.stage = "track"
    jg.sam("mouse", tag="eval-sam", video="v.mp4", n_frames=10)
    assert [c["tag"] for c in fake.calls] == ["dev", "dev"]                    # the pipeline's own tags are overridden
    assert fake.calls[0]["caller"] == "job:jx1|etho.perception.planner"
    assert fake.calls[1]["caller"].startswith("job:jx1|")
    assert jg.total_cost == 0.5 and dict(jg.stage_cost) == {"plan": 0.25, "track": 0.25} and seen == [0.25, 0.5]
    rec = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert rec[0]["prompt"] == "[user] hello\n[image]" and rec[0]["effort"] == "low" and rec[1]["kind"] == "sam"
    assert "AAAA" not in json.dumps(rec)


def test_caller_helpers():
    assert caller_for("j1", "etho.rules.synthesize:attack") == "job:j1|etho.rules.synthesize:attack"
    assert job_of_caller("job:j1|x") == "j1" and job_of_caller("etho.x:y") is None
    assert stage_of_caller("job:j1|etho.rules.synthesize:joint") == "synthesize"
    assert stage_of_caller("job:j1|etho.report.methods") == "report"
    assert stage_of_caller("job:j1|etho.adjudication.tier1") == "tier1"


def test_app_ledger_tails_incrementally_and_filters_by_job_and_day(tmp_path):
    p = tmp_path / "spend.jsonl"
    led = AppLedger(p)
    assert led.job_cost("j1") == 0.0
    rows = [{"caller": "job:j1|a", "cost_usd": 0.5, "time": "2026-10-28T10:00:00+00:00"},
            {"caller": "job:j2|a", "cost_usd": 1.0, "time": "2026-10-28T11:00:00+00:00"},
            {"caller": "etho.tracking.pipeline:x", "cost_usd": 9.0, "time": "2026-10-28T11:00:00+00:00"},
            {"caller": "job:j1|b", "cost_usd": 0.25, "time": "2026-10-29T01:00:00+00:00"}]
    with p.open("w") as f:
        f.write(json.dumps(rows[0]) + "\n" + json.dumps(rows[1]) + "\n" + json.dumps(rows[2])[:20])  # partial last line
    assert led.job_cost("j1") == 0.5 and led.job_cost("j2") == 1.0
    with p.open("a") as f:
        f.write(json.dumps(rows[2])[20:] + "\n" + json.dumps(rows[3]) + "\n")
    assert led.job_cost("j1") == 0.75 and len(led.job_rows("j1")) == 2
    assert led.spend_on("2026-10-28") == 1.5 and led.spend_on("2026-10-29") == 0.25   # non-job rows excluded
