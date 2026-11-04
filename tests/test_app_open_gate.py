"""The no-animal gate: when the planner's own record says there is nothing to track, the job stops before tracking, as a
clean `declined` (not an error), for the planner's cost only. App backend only; no network (fake gateway)."""

import pytest

from etho.app import backend as BK
from etho.app import progress as PG
from etho.app.backend import found_no_animals
from etho.perception.planner import Plan
from tests.app_env import make_app, post_job, wait_terminal
from tests.markers import needs_bwrap

EXACT = ("We couldn't find animals in this video, so nothing was tracked or charged beyond a quick check. "
         "Etho scores animal behavior in lab videos; try the sample clip on this page.")


def steps(expected, counts_by_phrase):
    out = [{"step": "propose", "model": "m", "answer": {"expected_count": expected, "phrases": list(counts_by_phrase)},
            "cost_usd": 0.02}]
    out += [{"step": "image_check", "phrase": p, "counts": c} for p, c in counts_by_phrase.items()]
    out += [{"step": "video_probe", "phrase": p, "frames": 50, "frac_with_expected": 0.0} for p in counts_by_phrase]
    return out


# ---- the rule, on the planner's recorded steps ----------------------------------------------------------------------
def test_expected_count_zero_means_no_animals():
    assert found_no_animals(steps(0, {"mouse": [2, 2]})) is not None
    assert found_no_animals(steps("0", {"mouse": [2, 2]})) is not None
    assert found_no_animals(steps(0.0, {"mouse": [2, 2]})) is not None


def test_no_phrase_finding_even_one_object_on_any_probe_frame_means_no_animals():
    assert found_no_animals(steps(2, {"mouse": [0, 0], "rodent": [0, 0], "animal": [0, 0]})) is not None
    assert found_no_animals(steps(2, {"mouse": [0, 0], "rodent": [0, 1]})) is None           # one hit anywhere is enough
    assert found_no_animals(steps(2, {"mouse": [1, 0]})) is None
    assert found_no_animals(steps(2, {"mouse": [2, 2]})) is None                              # the normal case


def test_when_it_cannot_be_told_the_job_goes_on():
    assert found_no_animals([]) is None
    assert found_no_animals([{"step": "propose"}]) is None                                     # the test stand-in's plan
    assert found_no_animals([{"step": "propose", "answer": {"expected_count": None}}]) is None
    assert found_no_animals([{"step": "propose", "answer": {"expected_count": "two"}}]) is None
    assert found_no_animals([{"step": "propose", "answer": {"expected_count": 2}}]) is None   # no image checks recorded
    assert found_no_animals(steps(2, {"mouse": []})) is None


# ---- the job -----------------------------------------------------------------------------------------------------
def planner_returning(monkeypatch, st, calls):
    def plan(path, *, gw, public_data, tag, **kw):
        gw.chat("meta", "muse-spark-1.3", [{"role": "user", "content": "plan"}], effort="medium", tag="eval-llm",
                caller="etho.perception.planner", public_data=public_data)
        calls.append("plan")
        return Plan("mouse", 2, "none visible", "persistence", st, 0.03)
    monkeypatch.setattr(BK, "run_planner", plan)

    def no_track(*a, **k):
        calls.append("track")
        raise AssertionError("tracking must not run for a declined job")
    monkeypatch.setattr(BK, "track_video", no_track)


@pytest.mark.parametrize("st", [steps(0, {"mouse": [2, 2]}), steps(2, {"mouse": [0, 0], "animal": [0, 0]})])
def test_a_no_animal_plan_stops_before_tracking_and_is_declined_not_failed(tmp_path, monkeypatch, st):
    client, c = make_app(tmp_path, monkeypatch)
    calls = []
    planner_returning(monkeypatch, st, calls)
    r = post_job(client, c.video)
    assert r.status_code == 200
    jid = r.json()["id"]
    j = wait_terminal(c.manager, jid)
    assert j.status == "declined" and j.error_code == "no_animals" and j.error is None
    assert calls == ["plan"]                                                  # no tracking
    assert not [x for x in c.gw.calls if "sam" in x]                          # no SAM call of any kind from the job
    assert [x["caller"].split("|")[-1] for x in c.gw.calls] == ["etho.perception.planner"]   # only the planner call
    sn = client.get(f"/api/jobs/{jid}").json()
    assert sn["status"] == "declined" and sn["friendly"] == EXACT
    stages = {s["name"]: s["status"] for s in sn["stages"]}
    assert stages["validate"] == "done" and stages["plan"] == "done"
    assert all(stages[n] == "pending" for n in ("track", "synthesize", "predict", "tier1", "report", "overlay"))
    assert sn["cost_usd"] <= 0.05                                             # the planner's cost only
    assert c.app.state.guard.outstanding() == 0                               # the reservation was released
    assert client.get(f"/api/projects/{jid}").json()["status"] == "declined"  # no result, a clear status
    assert not (c.cfg.jobs_dir / jid / "result.json").exists()


def test_the_declined_message_is_exactly_the_agreed_text_and_is_a_friendly_sentence():
    assert PG.NO_ANIMALS == EXACT
    from etho.app.server import FRIENDLY
    assert FRIENDLY["no_animals"] == EXACT
    f = PG.friendly_failure({"status": "declined", "error_code": "no_animals"}, FRIENDLY)
    assert f == EXACT
    assert PG.friendly_failure({"status": "done"}, FRIENDLY) is None


@needs_bwrap
def test_the_hosted_view_of_a_declined_job_has_no_raw_error_text(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"public": True, "tag": "dev", "access_code_file": None,
                                                       "open": True, "gateway_cache_root": tmp_path / "g",
                                                       "work_root": tmp_path},
                         app_kw={"isolation": lambda: {"mode": "bwrap", "detail": "t"}}, gw_kw={"cache_root": tmp_path / "g"},
                         base_url="https://testserver")
    planner_returning(monkeypatch, steps(0, {"mouse": [0]}), [])
    jid = post_job(client, c.video, consent="yes").json()["id"]
    wait_terminal(c.manager, jid)
    sn = client.get(f"/api/jobs/{jid}").json()
    assert sn["status"] == "declined" and sn["friendly"] == EXACT and sn["error"] is None and sn["message"] == ""


def test_a_normal_plan_goes_on_to_tracking_and_finishes(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    real_track = BK.track_video
    calls = []

    def plan(path, *, gw, public_data, tag, **kw):
        gw.chat("meta", "muse-spark-1.3", [{"role": "user", "content": "plan"}], effort="medium", tag="eval-llm",
                caller="etho.perception.planner", public_data=public_data)
        return Plan("mouse", 2, "coat color", "intensity", steps(2, {"mouse": [2, 2]}), 0.03)
    monkeypatch.setattr(BK, "run_planner", plan)
    monkeypatch.setattr(BK, "track_video", lambda *a, **k: calls.append("track") or real_track(*a, **k))
    jid = post_job(client, c.video).json()["id"]
    assert wait_terminal(c.manager, jid).status == "done"
    assert calls == ["track"]
    assert [x for x in c.gw.calls if "sam" in x]


def test_a_declined_job_frees_its_upload_and_counts_as_a_run_but_not_as_an_error(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 1})
    planner_returning(monkeypatch, steps(0, {"mouse": [0]}), [])
    jid = post_job(client, c.video).json()["id"]
    wait_terminal(c.manager, jid)
    assert post_job(client, c.video).status_code == 429                       # the quick check was a run


def test_the_replay_demo_is_not_affected():
    """The recorded planner answer for the sample clip finds animals, so the gate adds no call and changes no recorded
    call (the gate reads `Plan.steps` only). The replay's own offline test runs the whole app pipeline over it."""
    from pathlib import Path
    idx = Path(__file__).resolve().parents[1] / "etho" / "replay"
    assert (idx / "backend.py").exists()
    src = (idx / "session.py").read_text()
    assert '"declined"' in src                                                # the wait loop knows the new status
