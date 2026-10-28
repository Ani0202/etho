"""End to end through the API with synthetic stages: real sandbox, labels, report, overlay, files and
state machine; the planner, SAM tracking, synthesis, tier-1 and revise calls are stand-ins."""

import json
import subprocess

import numpy as np

from tests.app_env import make_app, post_job, wait_terminal

STAGES = ["validate", "plan", "track", "synthesize", "predict", "tier1", "report", "overlay"]


def ffprobe(path, entries="stream=codec_name,nb_frames,width,height"):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def run_job(client, c, **kw):
    r = post_job(client, c.video, **kw)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    j = wait_terminal(c.manager, jid)
    assert j.status == "done", (j.status, j.error)
    return jid


def test_analysis_job_end_to_end(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = run_job(client, c)
    snap = client.get(f"/api/jobs/{jid}").json()
    assert snap["status"] == "done" and [s["name"] for s in snap["stages"]] == STAGES
    assert all(s["status"] == "done" for s in snap["stages"])
    # every model call used the app tag, uploads used Standard models, effort always explicit
    chats = [x for x in c.gw.calls if "model" in x]
    assert chats and all(x["tag"] == "dev" and x["effort"] for x in chats)
    assert all(not x["model"].endswith("-contributor") and x["public_data"] is False for x in chats)
    assert all(x["caller"].startswith(f"job:{jid}|") for x in c.gw.calls)

    p = client.get(f"/api/projects/{jid}").json()
    assert p["status"] == "done" and p["behaviors"]["names"] == ["investigation", "attack"]
    v0 = p["versions"][0]
    # contact starts at sample 20 (gap < 20): one investigation bout 20..60, and tier 1 voids the window at s0=20
    assert v0["rule_bouts"]["investigation"] == [[20, 60]] and v0["rule_bouts"]["attack"] == []
    assert v0["bouts"]["investigation"] == [[50, 60]]            # windows (20,50) none, (50,60) kept
    assert v0["metrics"]["investigation"]["total_s"] == 1.0
    assert p["timing"]["video_s"] == 6.0 and p["timing"]["x_realtime"] is not None
    assert abs(p["cost"]["total_usd"] - p["cost"]["ledger_usd"]) < 1e-9 and p["cost"]["total_usd"] > 0
    assert set(p["cost"]["by_stage"]) >= {"plan", "track", "synthesize", "report"}

    # overlay: H.264, one frame per sample, served with range support
    ov = client.get(f"/api/projects/{jid}/overlay")
    assert ov.status_code == 200 and ov.headers["content-type"] == "video/mp4"
    info = ffprobe(c.cfg.jobs_dir / jid / "overlay.mp4")
    assert info["codec_name"] == "h264" and int(info["nb_frames"]) == c.n
    part = client.get(f"/api/projects/{jid}/overlay", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100

    # report is the self-contained HTML, served with a locked-down CSP
    rep = client.get(f"/api/projects/{jid}/report")
    assert rep.status_code == 200 and "Ethogram" in rep.text and "investigation" in rep.text
    assert "default-src 'none'" in rep.headers["content-security-policy"]

    # audit trail: models, efforts, prompts, detector code, every call's cost
    a = client.get(f"/api/projects/{jid}/audit").json()
    assert {d["behavior"] for d in a["detectors"]} == {"investigation", "attack"}
    assert "def detect" in a["detectors"][0]["code"]
    stages = {x["stage"] for x in a["calls"]}
    assert {"plan", "track", "synthesize", "report"} <= stages
    plan_call = next(x for x in a["calls"] if x["stage"] == "plan")
    assert plan_call["effort"] == "medium" and "[image]" in plan_call["prompt"] and "data:x" not in plan_call["prompt"]
    assert a["ledger"]["rows"] == len(a["calls"]) and abs(a["ledger"]["total_usd"] - p["cost"]["total_usd"]) < 1e-9
    assert len(a["verdicts"]) == 2 and a["verdicts"][0]["strip"] == "video_20_50.jpg"
    assert client.get(f"/api/projects/{jid}/strip/video_20_50.jpg").status_code == 200
    assert client.get(f"/api/projects/{jid}/strip/..%2Fresult.json").status_code == 404


def test_custom_behavior_names_render_in_report_and_results(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = run_job(client, c, defs={"sniffing": "The animal is sniffing the other one closely.",
                                   "rearing": "The animal stands on its hind legs, away from walls."})
    p = client.get(f"/api/projects/{jid}").json()
    assert p["behaviors"]["names"] == ["sniffing", "rearing"]
    html = client.get(f"/api/projects/{jid}/report").text
    assert "sniffing" in html and "rearing" in html and "mount" not in html.split("<h2>Methods")[0]
    # the shared report module is restored after a build
    from etho.report import report as R
    assert R.BEHAVIOR_ORDER == ("attack", "investigation", "mount")


def test_tier1_failure_degrades_to_rule_labels(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, backend_kw={"tier1_fail": True})
    jid = run_job(client, c)
    snap = client.get(f"/api/jobs/{jid}").json()
    t1 = next(s for s in snap["stages"] if s["name"] == "tier1")
    assert t1["status"] == "skipped" and "fireworks 503" in t1["message"]
    p = client.get(f"/api/projects/{jid}").json()
    assert p["versions"][0]["bouts"]["investigation"] == [[20, 60]]   # rule labels, untouched
    assert any("Tier-1" in w for w in p["warnings"])


def test_stage_failure_fails_the_job_and_stops(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    from etho.app import backend as BK

    def boom(*a, **k):
        raise RuntimeError("sam exploded")
    monkeypatch.setattr(BK, "track_video", boom)
    jid = post_job(client, c.video).json()["id"]
    j = wait_terminal(c.manager, jid)
    assert j.status == "failed" and "track: RuntimeError: sam exploded" in j.error
    snap = client.get(f"/api/jobs/{jid}").json()
    status = {s["name"]: s["status"] for s in snap["stages"]}
    assert status["plan"] == "done" and status["track"] == "failed" and status["synthesize"] == "pending"
    assert client.get(f"/api/projects/{jid}").json()["status"] == "failed"


def test_correction_loop_revises_and_reports_the_change(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = run_job(client, c)
    # the researcher says: the investigation bout 5.0-6.0 s is wrong (it is "none"), and attack was missed 2-3 s
    r = client.post(f"/api/projects/{jid}/revise", json={"corrections": [
        {"kind": "wrong", "behavior": "investigation", "start_s": 5.0, "end_s": 6.0, "correct_label": "none"},
        {"kind": "missed", "behavior": "attack", "start_s": 2.0, "end_s": 3.0}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rev"] == 1 and body["job_id"] == f"{jid}-r1" and body["n_corrections"] == 2
    j = wait_terminal(c.manager, body["job_id"])
    assert j.status == "done", j.error
    assert [s.name for s in j.stages] == ["revise", "predict", "tier1", "report", "overlay"]
    p = client.get(f"/api/projects/{jid}").json()
    assert p["latest_version"] == 1 and len(p["versions"]) == 2
    rev = p["versions"][1]
    statuses = {x["behavior"]: x["status"] for x in rev["revise"]}
    assert statuses == {"investigation": "revised", "attack": "revised"}
    # revised detectors use gap < 2 (never true in the synthetic table), so both behaviors vanish
    assert rev["bouts"]["investigation"] == [] and rev["diff"]["per_behavior"]["investigation"] == {
        "bouts_before": 1, "bouts_after": 0, "total_s_before": 1.0, "total_s_after": 0.0}
    cc = rev["diff"]["corrections"]
    assert cc["n"] == 2 and cc["final_before"] == 0 and cc["rule_after"] == cc["rule_n"] == 2
    assert cc["final_after"] == 1                      # the false positive is gone; the missed attack is not found
    # rev files exist; v0 untouched; revised overlay re-rendered with the new labels
    assert (c.cfg.jobs_dir / jid / "revisions" / "1" / "overlay.mp4").exists()
    assert client.get(f"/api/projects/{jid}/overlay?v=1").status_code == 200
    assert client.get(f"/api/projects/{jid}").json()["versions"][0]["bouts"]["investigation"] == [[50, 60]]
    a = client.get(f"/api/projects/{jid}/audit?v=1").json()
    assert any(x["stage"] == "revise" and x["version"] == 1 for x in a["calls"])
    # a second revision is cumulative: the earlier corrections are carried along
    r2 = client.post(f"/api/projects/{jid}/revise", json={"corrections": [
        {"kind": "missed", "behavior": "investigation", "start_s": 3.0, "end_s": 4.0}]})
    assert r2.status_code == 200 and r2.json()["n_corrections"] == 3 and r2.json()["rev"] == 2
    assert wait_terminal(c.manager, r2.json()["job_id"]).status == "done"


def test_rejected_revision_keeps_detector(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, backend_kw={"revise_ok": False})
    jid = run_job(client, c)
    r = client.post(f"/api/projects/{jid}/revise", json={"corrections": [
        {"kind": "wrong", "behavior": "investigation", "start_s": 5.0, "end_s": 6.0, "correct_label": "none"}]})
    assert wait_terminal(c.manager, r.json()["job_id"]).status == "done"
    rev = client.get(f"/api/projects/{jid}").json()["versions"][1]
    assert rev["revise"][0]["status"] == "rejected"
    assert rev["bouts"] == client.get(f"/api/projects/{jid}").json()["versions"][0]["bouts"]
    assert rev["diff"]["relabeled_s"] == 0.0


def test_revise_validation_errors(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = run_job(client, c)
    url = f"/api/projects/{jid}/revise"
    bad = [{"corrections": []},
           {"corrections": [{"kind": "wrong", "behavior": "nope", "start_s": 1, "end_s": 2, "correct_label": "none"}]},
           {"corrections": [{"kind": "missed", "behavior": "attack", "start_s": 50, "end_s": 51}]},
           {"corrections": [{"kind": "missed", "behavior": "attack", "start_s": 2, "end_s": 1}]},
           {"corrections": [{"kind": "wrong", "behavior": "attack", "start_s": 1, "end_s": 2}]},
           {"corrections": [{"kind": "wrong", "behavior": "attack", "start_s": 1, "end_s": 2, "correct_label": "attack"}]},
           {"corrections": [{"kind": "missed", "behavior": "attack", "start_s": 1, "end_s": 2, "extra": 1}]},
           {"nope": 1}]
    for payload in bad:
        r = client.post(url, json=payload)
        assert r.status_code == 400 and r.json()["error"] == "bad_corrections", payload
    assert client.post(url, content=b"nope").status_code == 400
    assert client.post("/api/projects/j000000000000/revise", json={}).status_code == 404
    assert len(np.atleast_1d(list((c.cfg.jobs_dir / jid).glob("revisions/*")))) == 0   # nothing was queued
