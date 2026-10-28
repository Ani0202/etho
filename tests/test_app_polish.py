"""ADR-005 bundle: planner card data, progress phases and friendly failures, bout trace, sample clip, packaging a real
app job as a showcase, presentation mode. No network, no model call."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from etho.app import progress as PG
from etho.app.server import FRIENDLY, SAMPLE_ATTRIBUTION
from tests.app_env import make_app, post_job, wait_terminal
from tests.app_helpers import make_video
from tests.test_app_showcase import build_showcase

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "etho" / "app" / "static"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PLAN = {"phrase": "mouse", "expected_count": 2, "identity_cue": "coat color: one black, one white",
        "identity_method": "intensity", "cost_usd": 0.0421,
        "steps": [{"step": "propose", "model": "muse-spark-1.3", "cost_usd": 0.01,
                   "answer": {"animals": "two mice", "expected_count": 2,
                              "phrases": ["black mouse", "mouse", "rodent"]}},
                  {"step": "image_check", "phrase": "black mouse", "counts": [1, 1]},
                  {"step": "image_check", "phrase": "mouse", "counts": [2, 2]},
                  {"step": "image_check", "phrase": "rodent", "counts": [2, 1]},
                  {"step": "video_probe", "phrase": "mouse", "frames": 50, "frac_with_expected": 0.96}]}


# ------------------------------------------------------------------------------------------------ planner card
def test_planner_view_keeps_muse_sparks_order_and_sams_counts():
    v = PG.planner_view(PLAN)
    assert v["ran"] and v["chosen"] == "mouse" and v["expected_count"] == 2 and v["cost_usd"] == 0.0421
    assert [r["phrase"] for r in v["phrases"]] == ["black mouse", "mouse", "rodent"]       # Muse Spark's order
    by = {r["phrase"]: r for r in v["phrases"]}
    assert by["black mouse"]["image_counts"] == [1, 1] and by["black mouse"]["image_frames_ok"] == 0
    assert by["mouse"]["image_frames_ok"] == 2 and by["mouse"]["probe_frac"] == 0.96 and by["mouse"]["probe_frames"] == 50
    assert by["mouse"]["chosen"] and not by["rodent"]["chosen"] and by["rodent"]["probe_frac"] is None
    assert v["identity_cue"].startswith("coat color") and v["identity_method"] == "intensity"
    assert v["fallback"] is False and v["animals"] == "two mice"


def test_planner_view_marks_fallback_and_handles_skipped_planner():
    fb = {**PLAN, "steps": PLAN["steps"][:-1] + [{"step": "video_probe", "phrase": "mouse", "frames": 50,
                                                  "frac_with_expected": 0.4},
                                                 {"step": "fallback", "phrase": "mouse", "note": "x"}]}
    assert PG.planner_view(fb)["fallback"] is True
    for plan in (None, {"phrase": "mouse", "steps": []}):
        v = PG.planner_view(plan)
        assert v == {"ran": False, "note": "planner not run for this precomputed showcase"}


def test_project_api_serves_the_planner_card_data(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    d = build_showcase(c, monkeypatch)
    p = client.get("/api/projects/sc-calms21-x").json()
    assert p["planner"]["ran"] is False and "not run" in p["planner"]["note"]      # skipped planner
    res = json.loads((d / "result.json").read_text())
    res["plan"] = PLAN
    (d / "result.json").write_text(json.dumps(res))
    p = client.get("/api/projects/sc-calms21-x").json()
    assert p["planner"]["ran"] and [r["phrase"] for r in p["planner"]["phrases"]][0] == "black mouse"


def test_planner_card_wording_is_literal():
    js = (STATIC / "project.js").read_text()
    assert "Muse Spark proposed these phrases; SAM 3.1 checked each one" in js
    assert "planner not run for this precomputed showcase" in (ROOT / "etho" / "app" / "progress.py").read_text()
    low = js.lower()
    assert "inspected overlay" not in low and "re-prompt" not in low and "reprompt" not in low


# ------------------------------------------------------------------------------------------------ progress
def snap(stage_status, status="running", elapsed=12.0, **kw):
    names = ["validate", "plan", "track", "synthesize", "predict", "tier1", "report", "overlay"]
    stages = [{"name": n, "status": stage_status.get(n, "pending"), "elapsed_s": 1.0, "message": stage_status.get(n + "_m", ""),
               "cost_usd": 0.01, "start_s": None, "end_s": None} for n in names]
    return {"status": status, "elapsed_s": elapsed, "stages": stages, "error": None, "message": "", **kw}


def test_phases_group_stages_into_five_with_status_and_messages():
    sn = snap({"validate": "done", "plan": "done", "track": "running", "track_m": "tracking 'mouse' with SAM 3.1"})
    ph = PG.phases(sn)
    assert [p["label"] for p in ph] == ["Plan", "Tracking", "Rules", "Frame-strip check", "Report"]
    assert [p["status"] for p in ph] == ["done", "running", "pending", "pending", "pending"]
    assert ph[1]["message"] == "tracking 'mouse' with SAM 3.1"
    assert ph[2]["message"] == ph[2]["blurb"]                    # pending phases say what they will do
    mid = PG.phases(snap({"validate": "done", "plan": "done", "track": "done", "synthesize": "done", "predict": "pending"}))
    assert mid[2]["status"] == "running"                           # between two stages of one phase
    assert PG.phases(snap({"synthesize": "failed"}, status="failed"))[2]["status"] == "failed"


def test_phase_elapsed_is_a_wall_clock_window_not_a_sum():
    sn = snap({"report": "done", "overlay": "done"})
    for s in sn["stages"]:
        if s["name"] in ("report", "overlay"):
            s["start_s"], s["end_s"] = 50.0, 58.0
    rep = [p for p in PG.phases(sn) if p["key"] == "report"][0]
    assert rep["elapsed_s"] == 8.0                                 # not 16: the two ran at the same time


def test_failures_become_plain_sentences_never_raw_errors():
    raw = "synthesize: RuntimeError: Traceback: boom at /home/x/y.py line 3"
    f = PG.friendly_failure(snap({}, status="failed", error=raw, error_code="failed"), FRIENDLY)
    assert "Traceback" not in f and "boom" not in f and "showcase" in f
    # the sentence follows the machine code, not words in the error text
    assert PG.friendly_failure(snap({}, status="failed", error="daily cap reached", error_code="failed"),
                               FRIENDLY) == f
    for code in ("daily_cap", "total_cap", "killed"):
        assert PG.friendly_failure(snap({}, status="failed", error_code=code), FRIENDLY) == FRIENDLY[code]
    assert "spend limit" in PG.friendly_failure(snap({}, status="failed", error_code="budget"), FRIENDLY)
    assert PG.friendly_failure(snap({}, status="cancelled", error_code="cancelled"), FRIENDLY).startswith("This job was cancelled")
    assert PG.friendly_failure(snap({}, status="running"), FRIENDLY) is None


def test_job_endpoint_returns_phases_and_a_real_elapsed_time(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    r = post_job(client, c.video)
    jid = r.json()["id"]
    j = wait_terminal(c.manager, jid)
    assert j.status == "done"
    sn = client.get(f"/api/jobs/{jid}").json()
    assert [p["label"] for p in sn["phases"]] == ["Plan", "Tracking", "Rules", "Frame-strip check", "Report"]
    assert all(p["status"] == "done" for p in sn["phases"]) and sn["friendly"] is None
    assert sn["elapsed_s"] == pytest.approx(j.run_s, abs=0.06) and sn["elapsed_s"] > 0   # the job's own clock
    ev = client.get(f"/api/jobs/{jid}/events").text                 # the SSE stream carries the same view
    assert "event: end" in ev and '"phases"' in ev


def test_failed_job_shows_the_friendly_message_in_the_api(tmp_path, monkeypatch):
    from etho.app import backend as BK
    client, c = make_app(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("provider exploded: secret-ish details")
    monkeypatch.setattr(BK, "synthesize", boom)
    jid = post_job(client, c.video).json()["id"]
    assert wait_terminal(c.manager, jid).status == "failed"
    sn = client.get(f"/api/jobs/{jid}").json()
    assert sn["status"] == "failed" and "exploded" not in sn["friendly"] and "showcase" in sn["friendly"]
    assert [p["status"] for p in sn["phases"]][2] == "failed" and sn["error_code"] == "failed"
    assert "exploded" not in json.dumps(sn["phases"]) and "exploded" not in json.dumps(sn["stages"])
    # on a public deployment nothing raw is served anywhere: job, SSE stream, project
    monkeypatch.setattr(c.cfg, "public", True, raising=False)
    for text in (client.get(f"/api/jobs/{jid}").text, client.get(f"/api/jobs/{jid}/events").text,
                 client.get(f"/api/projects/{jid}").text):
        assert "exploded" not in text and "secret-ish" not in text and "RuntimeError" not in text


# ------------------------------------------------------------------------------------------------ bout trace
def test_trace_marks_the_verdict_set_aside_by_the_restriction(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    d = build_showcase(c, monkeypatch)
    rows = [json.loads(x) for x in (d / "verdicts.jsonl").read_text().splitlines()]
    rows += [{"seq": "video", "s0": 40, "s1": 50, "rule_label": "investigation", "source": "rule", "label": "mount",
              "confidence": 0.9, "reason": "r2"},
             {"seq": "video", "s0": 52, "s1": 58, "rule_label": "investigation", "source": "rule", "label": "none",
              "confidence": 0.3, "reason": "r3"}]
    (d / "verdicts.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    idx = client.get("/api/projects/sc-calms21-x/trace").json()
    assert idx["counts"] == {"applied": 1, "restriction": 1, "set_aside": 1}
    assert [w[5] for w in idx["windows"]] == ["applied", "restriction", "set_aside"]
    t = client.get("/api/projects/sc-calms21-x/trace", params={"behavior": "investigation", "s0": 30, "s1": 60}).json()
    assert t["decided_by"] == "rules" and "restriction" in t["summary"]
    assert t["rule_result"]["flagged"] and t["rule_result"]["exact"] is False      # rules said 20-60, tier 1 cut 20-30
    by = {v["s0"]: v for v in t["tier1"]}
    assert by[40]["decision"] == "restriction" and "restriction" in by[40]["decision_text"]
    assert by[52]["decision"] == "set_aside" and "0.30" in by[52]["decision_text"]
    assert by[40]["strip"] == "video_40_50.jpg"
    t2 = client.get("/api/projects/sc-calms21-x/trace", params={"behavior": "investigation", "s0": 20, "s1": 30}).json()
    assert t2["decided_by"] == "tier1" and t2["tier1"][0]["decision"] == "applied"
    assert client.get("/api/projects/sc-calms21-x/trace", params={"behavior": "nope", "s0": 1, "s1": 2}).status_code == 400
    assert client.get("/api/projects/sc-nope/trace").status_code == 404
    assert not c.cfg.ledger_path.exists()


def test_tier1_decision_matches_apply():
    """decide() must agree with etho.adjudication.tier1.apply for every kind of verdict."""
    from etho.adjudication.tier1 import apply
    codes = {"investigation": 0, "attack": 1, "other": 2}
    allow = PG.allow_for(["investigation", "attack"])
    for lab, conf in (("investigation", 0.9), ("none", 0.9), ("attack", 0.9), ("investigation", 0.3), ("unsure", 0.9)):
        v = {"s0": 0, "s1": 5, "label": lab, "confidence": conf}
        changed = bool((apply(np.full(10, 1, np.int8), [v], codes, allow=allow) != 1).any())
        assert (PG.decide(v, allow)[0] == "applied") == changed, (lab, conf)


# ------------------------------------------------------------------------------------------------ sample clip
def test_make_sample_clip_refuses_non_dev_and_writes_meta(tmp_path):
    mod = load_script("make_sample_clip")
    vids = tmp_path / "videos"
    vids.mkdir()
    make_video(vids / "seqA.mp4", seconds=9.0, w=64, h=48, fps=10)
    make_video(vids / "seqT.mp4", seconds=9.0, w=64, h=48, fps=10)
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"dev": ["seqA"], "test": ["seqT"]}))
    with pytest.raises(SystemExit):
        mod.make_clip("seqT", videos=vids, splits=splits, out_dir=tmp_path / "o", start_s=1)
    meta = mod.make_clip("seqA", videos=vids, splits=splits, out_dir=tmp_path / "o", start_s=1, duration_s=5)
    assert meta["split"] == "dev" and meta["license"] == "CC BY 4.0" and 4.5 < meta["duration_s"] < 5.5
    assert (tmp_path / "o" / "calms21_dev_60s.mp4").stat().st_size == meta["size_bytes"]
    short = mod.make_clip("seqA", videos=vids, splits=splits, out_dir=tmp_path / "o2", start_s=60, duration_s=60)
    assert short["source_start_s"] == 0.0 and short["duration_s"] < 10         # a short recording is not padded


def test_app_serves_the_sample_clip_read_only_with_attribution(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    off = client.get("/api/sample").json()
    assert off["available"] is False and off["download"] is None and "make_sample_clip" in off["message"]
    assert client.get("/api/sample/clip").status_code == 404
    load_script("make_sample_clip")                                  # imports cleanly
    mod = load_script("make_sample_clip")
    vids = tmp_path / "v"
    vids.mkdir()
    make_video(vids / "seqA.mp4", seconds=8.0, w=64, h=48, fps=10)
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps({"dev": ["seqA"]}))
    mod.make_clip("seqA", videos=vids, splits=sp, out_dir=c.cfg.sample_dir, start_s=0, duration_s=5)
    on = client.get("/api/sample").json()
    assert on["available"] and on["download"] == "/api/sample/clip" and on["clip"]["split"] == "dev"
    assert on["attribution"] == SAMPLE_ATTRIBUTION == "Clip from CalMS21 (Sun et al. 2021, Caltech), licensed CC BY 4.0"
    assert on["links"] == {"dataset": "https://data.caltech.edu/records/s0vdx-0k302",
                           "license": "https://creativecommons.org/licenses/by/4.0/"}
    assert "trimmed" in on["trimmed_note"]
    from etho.app.server import default_definitions
    assert on["definitions"] == default_definitions(c.cfg)["behaviors"] and set(on["definitions"]) == {"attack", "investigation", "mount"}
    r = client.get("/api/sample/clip")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and "attachment" in r.headers["content-disposition"]
    assert len(r.content) == on["clip"]["size_bytes"]
    assert client.post("/api/sample/clip").status_code == 405                        # read-only: no write method works
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert client.get("/api/sample/clip?path=../../etc/passwd").status_code == 200   # the path is never taken from the request
    assert not c.cfg.ledger_path.exists()


def test_home_page_carries_the_attribution_without_javascript(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    html = client.get("/").text
    assert SAMPLE_ATTRIBUTION in html and "https://creativecommons.org/licenses/by/4.0/" in html
    assert "https://data.caltech.edu/records/s0vdx-0k302" in html and "trimmed" in html
    assert "Behavior definitions to paste" in html and "/static/home.js" in html


# ------------------------------------------------------------------------------------------------ packaging a real job
def test_package_job_showcase_keeps_real_timings_prompts_and_planner_steps(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = post_job(client, c.video).json()["id"]
    assert wait_terminal(c.manager, jid).status == "done"
    job = c.cfg.jobs_dir / jid
    res0 = json.loads((job / "result.json").read_text())
    n = res0["n_samples"]
    lab = np.full(n, 2, np.int8)                                      # names: investigation, attack -> other = 2
    lab[10:25] = 1
    np.savez_compressed(tmp_path / "expert.npz", labels=lab)
    mod = load_script("package_job_showcase")
    mod.main(["--job", str(job), "--name", "live-run", "--title", "Live run", "--description", "D", "--calms21",
              "--out", str(c.cfg.showcase_dir), "--expert-labels", str(tmp_path / "expert.npz"),
              "--expert-seq", "mouse008_task1_annotator1"])
    d = c.cfg.showcase_dir / "live-run"
    for f in ("result.json", "calls.jsonl", "plan.json", "clean.mp4", "overlay.mp4", "report.html", "labels.npz",
              "verdicts.jsonl", "run/video/features.npz"):
        assert (d / f).exists(), f
    assert not (d / "input.json").exists() and not (d / "job.json").exists()
    assert json.loads((d / "run/video/tracking.json").read_text())["src"].endswith("live-run/clean.mp4")

    lst = client.get("/api/showcases").json()["showcases"]
    assert [s["id"] for s in lst] == ["sc-live-run"] and lst[0]["cost_estimated"] is False
    assert lst[0]["wall_s"] == res0["timing"]["wall_s"] and lst[0]["dataset"]["license"] == "CC BY 4.0"
    p = client.get("/api/projects/sc-live-run").json()
    assert p["kind"] == "showcase" and p["title"] == "Live run" and p["id"] == "sc-live-run"
    assert p["timing"]["x_realtime"] is not None and p["timing"]["complete"] is True      # real, not "n/a"
    assert p["cost"]["source"] == "measured in the live run" and not p["cost"].get("estimated") and p["cost"]["total_usd"] == res0["cost"]["total_usd"] > 0
    assert any(s["elapsed_s"] > 0 for s in p["stages"]) and p["planner"]["ran"] is True
    assert p["expert"]["split"] == "dev" and p["expert"]["label"] == "Expert (CalMS21)" and p["expert"]["bouts"]["attack"] == [[10, 25]]
    a = client.get("/api/projects/sc-live-run/audit").json()
    prompts = [x for x in a["calls"] if x.get("prompt")]
    assert prompts and any(x["stage"] == "plan" for x in a["calls"]) and "Packaged from a live run" in a["note"]
    assert a["plan"]["phrase"] == "mouse"
    assert client.get("/api/projects/sc-live-run/overlay").status_code == 200
    assert client.get("/api/projects/sc-live-run/trace").status_code == 200
    # a job that did not finish cannot be packaged
    (job / "overlay.mp4").unlink()
    with pytest.raises(SystemExit):
        mod.main(["--job", str(job), "--name", "bad", "--out", str(tmp_path / "x")])


def test_make_showcase_expert_row_is_optional_and_display_only(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    lab = np.full(c.n, 3, np.int8)
    lab[5:15] = 0
    np.savez_compressed(tmp_path / "e.npz", labels=lab)
    from etho.app import packaging
    sp = tmp_path / "splits.json"
    sp.write_text(json.dumps({"dev": ["seqA"], "test": []}))
    monkeypatch.setattr(packaging, "SPLITS", sp)
    d = build_showcase(c, monkeypatch, extra=("--expert-labels", str(tmp_path / "e.npz")))
    res = json.loads((d / "result.json").read_text())
    assert res["expert"]["bouts"]["attack"] == [[5, 15]] and "never saw" in res["expert"]["note"]
    d2 = build_showcase(c, monkeypatch, name="calms21-y")
    assert "expert" not in json.loads((d2 / "result.json").read_text())


# ------------------------------------------------------------------------------------------------ presentation mode
def test_presentation_mode_and_ui_pieces_are_wired():
    js = (STATIC / "common.js").read_text()
    assert 'get("present")' in js and "classList.add(\"present\")" in js and 'role: "timer"' in js
    css = (STATIC / "app.css").read_text()
    assert "html.present" in css and ".clock" in css
    eg = (STATIC / "ethogram.js").read_text()
    assert "Expert (CalMS21)" in (STATIC / "ethogram.js").read_text() or "expert.label" in eg
    for page in ("index.html", "project.html"):
        assert 'id="present-toggle"' in (STATIC / page).read_text()


# ------------------------------------------------------------------------------------------------ review fixes
def test_trace_without_stored_verdicts_and_with_untouching_verdicts_says_so_literally():
    rec = {"rule_bouts": {"investigation": [[20, 60]]}}
    t = PG.bout_trace(rec, [], "investigation", 20, 60, 10.0)
    assert t["decided_by"] == "rules" and t["summary"] == (
        "No frame-strip verdicts are stored for this version (tier 1 was skipped or failed)")
    far = [{"s0": 100, "s1": 110, "rule_label": "investigation", "label": "investigation", "confidence": 0.9,
            "decision": "applied"}]
    t = PG.bout_trace(rec, far, "investigation", 20, 60, 10.0)
    assert t["summary"] == "The rules decided: no stored tier-1 window touches this bout"
    assert "alone" not in (STATIC / "project.js").read_text()


def test_trace_credits_tier1_when_it_promoted_a_window_the_rules_did_not_flag():
    promo = [{"s0": 20, "s1": 40, "rule_label": "other", "label": "investigation", "confidence": 0.9,
              "decision": "applied"}]
    t = PG.bout_trace({"rule_bouts": {"investigation": []}}, promo, "investigation", 20, 40, 10.0)
    assert t["decided_by"] == "tier1" and t["rule_result"]["flagged"] is False and "did not flag" in t["summary"]
    same = [{**promo[0], "rule_label": "investigation"}]
    t = PG.bout_trace({"rule_bouts": {"investigation": [[20, 40]]}}, same, "investigation", 20, 40, 10.0)
    assert t["decided_by"] == "rules" and "confirmed" in t["summary"]


def test_expert_labels_are_validated_and_test_split_needs_the_flag(tmp_path, monkeypatch, capsys):
    from etho.app import packaging
    sp = tmp_path / "sp.json"
    sp.write_text(json.dumps({"dev": ["d1"], "test": ["t1"]}))
    names = ["attack", "investigation", "mount"]
    ok = tmp_path / "ok.npz"
    np.savez_compressed(ok, labels=np.array([3, 3, 0, 0, 1, 3], np.int8))
    rec = packaging.expert_record(ok, names, 6, 10.0, seq="d1", splits=sp)
    assert rec["bouts"]["attack"] == [[2, 4]] and rec["split"] == "dev" and rec["sequence"] == "d1"
    assert "samples per code" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        packaging.expert_record(ok, names, 6, 10.0, seq="t1", splits=sp)               # test split needs the flag
    rec = packaging.expert_record(ok, names, 6, 10.0, seq="t1", splits=sp, allow_test=True)
    assert rec["split"] == "test" and "TEST-split" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        packaging.expert_record(ok, names, 6, 10.0, seq="other", splits=sp)
    bad = tmp_path / "bad.npz"
    np.savez_compressed(bad, labels=np.array([0, 1, 7, 3, 3, 3], np.int8))
    with pytest.raises(SystemExit):
        packaging.expert_record(bad, names, 6, 10.0, seq="d1", splits=sp)
    with pytest.raises(SystemExit):
        packaging.expert_record(ok, names, 9, 10.0, seq="d1", splits=sp)               # length mismatch


def test_a_bad_expert_file_leaves_no_half_built_showcase(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = post_job(client, c.video).json()["id"]
    wait_terminal(c.manager, jid)
    np.savez_compressed(tmp_path / "bad.npz", labels=np.full(5, 99, np.int8))
    mod = load_script("package_job_showcase")
    with pytest.raises(SystemExit):
        mod.main(["--job", str(c.cfg.jobs_dir / jid), "--name", "nope", "--out", str(c.cfg.showcase_dir),
                  "--expert-labels", str(tmp_path / "bad.npz"), "--expert-seq", "mouse008_task1_annotator1"])
    assert not (c.cfg.showcase_dir / "nope").exists()


def test_explicit_daily_cap_is_not_raised_in_the_judging_window(monkeypatch):
    from datetime import datetime, timezone

    from etho.app.config import AppConfig
    inside = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    monkeypatch.delenv("ETHO_APP_DAILY_CAP_USD", raising=False)
    monkeypatch.delenv("ETHO_APP_JUDGING_DAILY_CAP_USD", raising=False)
    default = AppConfig(public=True)
    assert default.daily_cap_at(inside) == 8.0 and default.daily_cap_usd == 3.0
    monkeypatch.setenv("ETHO_APP_DAILY_CAP_USD", "2")
    explicit = AppConfig(public=True)
    assert explicit.judging_daily_cap_usd is None and explicit.daily_cap_at(inside) == 2.0
    monkeypatch.setenv("ETHO_APP_JUDGING_DAILY_CAP_USD", "6")
    both = AppConfig(public=True)
    assert both.daily_cap_at(inside) == 6.0


def test_cost_label_shows_ledger_match_only_for_non_showcases():
    js = (STATIC / "project.js").read_text()
    assert 'p.kind !== "showcase"' in js and "Math.abs(c.total_usd - c.ledger_usd) < 0.01" in js


def test_clock_text_rounds_before_splitting():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    code = ("const vm=require('vm'),fs=require('fs');const c={document:{documentElement:{classList:{add(){}}},"
            "getElementById(){return null}},location:{search:''},sessionStorage:{getItem(){return null}},"
            "URLSearchParams};vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);"
            "console.log(JSON.stringify([59.96,0,59.94,61.25,3599.99,-1].map(x=>vm.runInContext('clockText('+x+')',c))))")
    out = subprocess.run(["node", "-e", code, str(STATIC / "common.js")], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == ["1:00.0", "0:00.0", "0:59.9", "1:01.3", "60:00.0", "0:00.0"]


def test_a_confident_verdict_matching_the_rule_is_agreed_not_set_aside():
    allow = frozenset({"investigation", "none"})
    d, text = PG.decide({"label": "mount", "rule_label": "mount", "confidence": 0.92}, allow)
    assert d == "agreed" and "agreeing with the rule" in text
    assert PG.decide({"label": "attack", "rule_label": "mount", "confidence": 0.88}, allow)[0] == "restriction"
    assert PG.decide({"label": "mount", "rule_label": "mount", "confidence": 0.3}, allow)[0] == "set_aside"
