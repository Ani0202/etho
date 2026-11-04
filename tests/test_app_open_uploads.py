"""Open live uploads (Aniket, 2026-11-03): ETHO_APP_OPEN=1 makes uploads and revisions on a public deployment need no
access code and no login. Per-visitor limit, caps, in-flight limit, the server-enforced consent field, friendly
messages on every limit, the sample clip, and the budget move. No network: the gateway is faked. Without
ETHO_APP_OPEN nothing changes (tests/test_app_hosting.py and the other app tests are the proof)."""

import json
import os
import re
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from etho.app.config import DEFAULT_LIVE_UNTIL, AppConfig
from etho.app.server import CONSENT_TEXT
from etho.gateway.ledger import CapExceeded, Ledger
from etho.gateway.prices import DEMO_RESERVE_OPENS_UTC, HARD_CAPS, META_TAG_BUDGETS
from tests.app_env import no_jobs, post_job, wait_for, wait_terminal
from tests.markers import needs_bwrap
from tests.test_app_hosting import REVISE, START, hosted, spent_nothing, write_rows

STATIC = Path(__file__).resolve().parents[1] / "etho" / "app" / "static"
DOC = Path(__file__).resolve().parents[1] / "docs" / "DEPLOY_OPEN_UPLOADS.md"
YES = {"consent": "yes"}
README = "https://github.com/Ani0202/etho#readme"


def opened(tmp_path, monkeypatch, *, code=None, cfg_kw=None, **kw):
    """A public, open-mode app: no access code file at all unless `code` is given."""
    return hosted(tmp_path, monkeypatch, code=code,
                  cfg_kw={"open": True, "access_code_file": None if code is None else tmp_path / "access_code",
                          **(cfg_kw or {})}, **kw)


def up(client, c, **form):
    return post_job(client, c.video, **{**YES, **form})


def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("ETHO_APP_"):
            monkeypatch.delenv(k)


def friendly_ok(r, status, code):
    assert r.status_code == status and r.json()["error"] == code, r.text
    f = r.json()["friendly"]
    assert not re.search(r"HTTP \d{3}|Traceback|\{\"", f) and f[0] not in "{[<"
    return f


def points_onward(f):
    """Every limit says where to go next: the showcase on the site, `make demo` in the README, and the README link,
    worded so that it is true whether or not the replay demo has landed."""
    low = f.lower()
    assert "showcase" in low and "make demo" in f and README in f, f
    assert "offline replay demo (`make demo`" in f and "is described in the project README" in f, f


# ---- config ---------------------------------------------------------------------------------------------------
def test_open_mode_defaults_and_env(monkeypatch):
    clean_env(monkeypatch)
    o = AppConfig(public=True, open=True)
    assert (o.rate_limit_jobs, o.rate_window_s, o.max_in_flight) == (10, 86400.0, 2)
    assert o.max_upload_mb == 50.0 and o.max_duration_s == 120.0 and o.retention_hours == 24.0
    assert o.consent_required
    p = AppConfig(public=True)                                          # not open: exactly as before
    assert (p.rate_limit_jobs, p.rate_window_s, p.max_in_flight, p.open, p.consent_required) == (3, 3600.0, 0, False,
                                                                                               False)
    for k, v in {"ETHO_APP_OPEN": "1", "ETHO_APP_PUBLIC": "1", "ETHO_APP_RATE_WINDOW_S": "7200",
                 "ETHO_APP_RATE_JOBS": "4", "ETHO_APP_MAX_IN_FLIGHT": "3", "ETHO_APP_DAILY_CAP_USD": "8",
                 "ETHO_APP_JUDGING_DAILY_CAP_USD": "20", "ETHO_APP_TOTAL_CAP_USD": "60"}.items():
        monkeypatch.setenv(k, v)
    e = AppConfig()
    assert (e.open, e.rate_window_s, e.rate_limit_jobs, e.max_in_flight) == (True, 7200.0, 4, 3)
    assert (e.daily_cap_usd, e.judging_daily_cap_usd, e.total_cap_usd) == (8.0, 20.0, 60.0)
    from datetime import datetime

    from etho.app.config import ET
    assert e.daily_cap_at(datetime(2026, 11, 3, 23, 0, tzinfo=ET)) == 8.0          # $8 before the judging window
    assert e.daily_cap_at(datetime(2026, 11, 4, 0, 0, tzinfo=ET)) == 20.0          # $20 inside it, Nov 4-9 ET
    assert e.daily_cap_at(datetime(2026, 11, 9, 23, 59, tzinfo=ET)) == 20.0
    assert e.daily_cap_at(datetime(2026, 11, 10, 0, 0, tzinfo=ET)) == 8.0


# ---- no code, no login ----------------------------------------------------------------------------------------
def test_a_public_app_without_a_code_is_read_only_unless_it_is_open(tmp_path, monkeypatch):
    client, c = hosted(tmp_path / "closed", monkeypatch, code=None)             # today's default stays
    lv = client.get("/api/config").json()["live"]
    assert lv["mode"] == "read-only" and lv["can_submit"] is False
    assert post_job(client, c.video).status_code == 403 and spent_nothing(c)
    client, c = opened(tmp_path / "open", monkeypatch)
    lv = client.get("/api/config").json()["live"]
    assert lv["mode"] == "live" and lv["access_required"] is False and lv["can_submit"] is True
    assert client.get("/api/health").json()["open"] is True


@needs_bwrap
def test_open_mode_takes_uploads_and_revisions_without_a_code_or_cookie(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch)
    r = up(client, c)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert wait_terminal(c.manager, jid).status == "done"
    r = client.post(f"/api/projects/{jid}/revise", json=REVISE)
    assert r.status_code == 200, r.text
    wait_terminal(c.manager, r.json()["job_id"])
    assert client.post("/api/access", json={"code": "x" * 20}).json()["error"] == "no_code_needed"


def test_open_mode_does_not_make_every_visitor_an_operator(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch)
    h = client.get("/api/health").json()
    for k in ("reasons", "sandbox", "client_seen_as", "retention", "queue", "access_problem"):
        assert k not in h, k
    assert set(h) <= {"ok", "open", "mode", "live_until", "caps"}
    client, c = opened(tmp_path / "withcode", monkeypatch, code="operator-code-0123456789")
    assert "client_seen_as" not in client.get("/api/health").json()
    h = client.get("/api/health", headers={"X-Etho-Admin": "operator-code-0123456789"}).json()   # the code still works
    assert h["open"] is True and "client_seen_as" in h and h["access_required"] is False


@needs_bwrap
def test_kill_switch_read_only_file_expiry_and_sandbox_still_apply_when_open(tmp_path, monkeypatch):
    kill, ro = tmp_path / "ops" / "app-kill", tmp_path / "ops" / "app-readonly"
    client, c = opened(tmp_path, monkeypatch, cfg_kw={"kill_file": kill, "readonly_file": ro})
    kill.parent.mkdir(parents=True)
    ro.write_text("")
    r = up(client, c)
    f = friendly_ok(r, 403, "read_only")
    assert "showcase" in f.lower()
    ro.unlink()
    kill.write_text("")
    friendly_ok(up(client, c), 503, "killed")
    kill.unlink()
    assert up(client, c).status_code == 200
    c.clock.t = DEFAULT_LIVE_UNTIL + timedelta(seconds=1)
    friendly_ok(up(client, c), 503, "expired")
    assert client.get("/api/showcases").status_code == 200 and client.get("/").status_code == 200
    client, c = opened(tmp_path / "nobwrap", monkeypatch, bwrap=False)
    assert client.get("/api/config").json()["live"]["mode"] == "read-only"
    assert up(client, c).status_code == 403


def test_showcases_stay_open_and_a_showcase_revision_is_open_too(tmp_path, monkeypatch):
    from tests.test_app_showcase import BODY, build_showcase
    client, c = opened(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    assert client.get("/api/projects/sc-calms21-x").status_code == 200
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert r.status_code == 200 and r.json()["forked"]
    wait_terminal(c.manager, r.json()["job_id"])


# ---- consent --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("form", [{}, {"consent": ""}, {"consent": "no"}, {"consent": "false"}, {"consent": "0"}])
@needs_bwrap
def test_an_upload_without_the_consent_field_is_a_400_and_costs_nothing(tmp_path, monkeypatch, form):
    client, c = opened(tmp_path, monkeypatch)
    r = post_job(client, c.video, **form)
    f = friendly_ok(r, 400, "consent_required")
    assert CONSENT_TEXT in f and "Nothing was kept or spent" in f
    assert spent_nothing(c) and no_jobs(c)
    assert client.get("/api/health").json()["caps"]["remaining_usd"] == client.get("/api/config").json()["cap"][
        "remaining_usd"]
    assert up(client, c).status_code == 200                      # the same request with the field is accepted


@pytest.mark.parametrize("v", ["yes", "true", "1", "on", "YES"])
@needs_bwrap
def test_consent_values_that_count(tmp_path, monkeypatch, v):
    client, c = opened(tmp_path, monkeypatch)
    assert post_job(client, c.video, consent=v).status_code == 200


def test_consent_is_checked_before_the_file_is_looked_at(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch)
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not a video")
    r = post_job(client, bad)                                    # no consent AND not a video: consent comes first
    assert r.status_code == 400 and r.json()["error"] == "consent_required"
    assert no_jobs(c) and not c.gw.calls


@needs_bwrap
def test_consent_is_logged_as_given_and_nothing_personal(tmp_path, monkeypatch, caplog):
    client, c = opened(tmp_path, monkeypatch)
    with caplog.at_level("INFO", logger="etho.app"):
        jid = up(client, c).json()["id"]
    msgs = [r.getMessage() for r in caplog.records if "consent" in r.getMessage()]
    assert msgs == [f"upload consent given for job {jid}"]
    assert "testclient" not in msgs[0] and "127.0.0.1" not in msgs[0]
    wait_terminal(c.manager, jid)
    inp = json.loads((c.cfg.jobs_dir / jid / "input.json").read_text())
    assert inp["consent"] is True and "testclient" not in json.dumps(inp)


@needs_bwrap
def test_without_open_mode_the_upload_needs_no_consent_field(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    client.post("/api/access", json={"code": "CODE-3f9a-unit-test-code"})
    assert post_job(client, c.video).status_code == 200
    assert "consent" not in client.get("/api/config").json()["limits"] or \
        client.get("/api/config").json()["limits"]["consent_required"] is False


def test_the_page_shows_the_consent_box_next_to_the_upload_button_and_sends_it():
    html = (STATIC / "index.html").read_text()
    form = html[html.index('<form id="form"'):html.index("</form>", html.index('<form id="form"'))]
    assert 'type="checkbox"' in form and 'name="consent"' in form and CONSENT_TEXT in form
    assert form.index('id="consent"') < form.index('id="go"') and form.index('id="go"') - form.index('id="consent"') < 600
    js = (STATIC / "home.js").read_text()
    assert 'fd.append("consent", "yes")' in js and 'consent_required' in js and "$(\"consent\").checked" in js


# ---- per-visitor limit ----------------------------------------------------------------------------------------
@needs_bwrap
def test_the_limit_is_per_visitor_per_rolling_24h_and_revisions_count(tmp_path, monkeypatch):
    t = [0.0]
    from tests.app_env import make_app
    client, c = make_app(tmp_path, monkeypatch, clock=lambda: t[0], now=lambda: START, base_url="https://testserver",
                         cfg_kw={"public": True, "open": True, "tag": "dev", "rate_limit_jobs": 2, "max_in_flight": 0,
                                 "max_queue": 9, "gateway_cache_root": tmp_path / "gwc", "work_root": tmp_path,
                                 "maintenance_interval_s": 3600.0}, gw_kw={"cache_root": tmp_path / "gwc"},
                         app_kw={"isolation": lambda: {"mode": "bwrap", "detail": "t"}})
    assert c.cfg.rate_window_s == 86400.0
    a = up(client, c).json()["id"]                                       # run 1
    wait_terminal(c.manager, a)
    r = client.post(f"/api/projects/{a}/revise", json=REVISE)            # run 2: a revision counts as one
    assert r.status_code == 200
    wait_terminal(c.manager, r.json()["job_id"])
    cfg = client.get("/api/config").json()
    assert cfg["visitor"] == {"runs_used": 2, "runs_limit": 2, "window_s": 86400.0}
    t[0] = 23 * 3600.0
    r = up(client, c)
    f = friendly_ok(r, 429, "rate_limited")                              # the third in 23 h
    assert "tomorrow" in f and "24 hours" in f and "2 live runs" in f
    points_onward(f)
    assert int(r.headers["retry-after"]) <= 3600 + 1
    r = client.post(f"/api/projects/{a}/revise", json=REVISE)
    friendly_ok(r, 429, "rate_limited")
    other = TestClient(c.app, base_url="https://testserver", client=("203.0.113.9", 5000))   # another visitor
    assert post_job(other, c.video, **YES).status_code == 200
    t[0] = 24 * 3600.0 + 1                                               # the window rolls on: allowed again
    assert up(client, c).status_code == 200
    wait_for(lambda: not c.manager.busy())


@needs_bwrap
def test_a_rejected_upload_does_not_use_up_a_run(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 1})
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"x" * 500)
    assert post_job(client, bad, **YES).status_code == 400
    assert post_job(client, c.video).status_code == 400                  # no consent
    assert up(client, c).status_code == 200                              # the one run is still there


# ---- caps -----------------------------------------------------------------------------------------------------
def test_cap_messages_point_to_the_showcase_and_make_demo_and_say_when_it_resets(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 3.0, "total_cap_usd": 20.0})
    write_rows(c.cfg.ledger_path, [{"caller": f"{c.cfg.caller_prefix}old|x", "cost_usd": 2.99, "tag": "dev"}])
    f = friendly_ok(up(client, c), 429, "daily_cap")
    points_onward(f)
    assert "tomorrow" in f and "24-hour" in f
    lv = client.get("/api/config").json()["live"]
    assert lv["capped"] == "daily_cap" and lv["can_submit"] is False
    points_onward(lv["message"])
    assert not c.gw.calls and no_jobs(c)
    client2, c2 = opened(tmp_path / "t", monkeypatch, cfg_kw={"daily_cap_usd": 9.0, "total_cap_usd": 1.0})
    write_rows(c2.cfg.ledger_path, [{"caller": f"{c2.cfg.caller_prefix}old|x", "cost_usd": 0.99, "tag": "dev"}])
    f = friendly_ok(up(client2, c2), 429, "total_cap")
    points_onward(f)
    assert "over for this demo" in f and "tomorrow" not in f
    points_onward(client2.get("/api/config").json()["live"]["message"])


def test_a_job_stopped_by_a_cap_after_admission_says_the_same_and_closed_mode_keeps_its_words(tmp_path):
    from etho.app import progress as PG
    from etho.app.server import FRIENDLY, friendly_map
    from tests.test_app_polish import snap
    fr = friendly_map(AppConfig(public=True, open=True, root=tmp_path))
    for code in ("daily_cap", "total_cap", "rate_limited", "queue_full"):
        points_onward(fr[code])
    for code in ("daily_cap", "total_cap"):
        points_onward(PG.friendly_failure(snap({}, status="failed", error_code=code), fr))
    assert friendly_map(AppConfig(public=True, root=tmp_path)) is FRIENDLY          # not open: the very same table
    assert all("make demo" not in v for v in FRIENDLY.values())


def test_the_judging_window_cap_is_the_one_the_guard_uses(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 8.0, "judging_daily_cap_usd": 20.0,
                                                      "total_cap_usd": 60.0})
    cap = client.get("/api/config").json()["cap"]
    assert cap["cap_usd"] == 8.0 and cap["total_cap_usd"] == 60.0       # START is Oct 28: before the window
    c.clock.t = c.clock.t.replace(month=11, day=5)
    assert client.get("/api/config").json()["cap"]["cap_usd"] == 20.0


# ---- in flight, queue position, worker -------------------------------------------------------------------------
@needs_bwrap
def test_one_worker_runs_the_jobs_and_two_are_allowed_in_flight_with_a_visible_position(tmp_path, monkeypatch):
    from tests.app_env import make_app
    client, c = make_app(tmp_path, monkeypatch, base_url="https://testserver", now=lambda: START,
                         cfg_kw={"public": True, "open": True, "tag": "dev", "rate_limit_jobs": 50, "max_queue": 9,
                                 "max_jobs_per_client": 0,
                                 "gateway_cache_root": tmp_path / "gwc", "work_root": tmp_path,
                                 "maintenance_interval_s": 3600.0}, gw_kw={"cache_root": tmp_path / "gwc"},
                         app_kw={"isolation": lambda: {"mode": "bwrap", "detail": "t"}, "start_worker": False})
    a = up(client, c)
    b = up(client, c)
    assert a.status_code == 200 and b.status_code == 200
    assert (a.json()["queue_position"], b.json()["queue_position"]) == (1, 2)
    r = up(client, c)                                                    # a third upload while two are in flight
    f = friendly_ok(r, 503, "queue_full")
    points_onward(f)
    assert "a few minutes" in f and not no_jobs(c)
    assert len(list(c.cfg.jobs_dir.iterdir())) == 2                      # nothing left behind by the refused one
    sn = client.get(f"/api/jobs/{b.json()['id']}").json()
    assert sn["status"] == "queued" and sn["queue_position"] == 2
    cfg = client.get("/api/config").json()
    assert cfg["queue"] == {"in_flight": 2, "waiting": 2} and cfg["limits"]["max_in_flight"] == 2
    # cancelling a queued job frees its slot and costs nothing
    assert client.post(f"/api/jobs/{b.json()['id']}/cancel").json() == {"cancelled": True}
    assert up(client, c).status_code == 200
    assert not c.gw.calls


def test_the_manager_runs_exactly_one_worker_thread(tmp_path, monkeypatch):
    before = len([t for t in threading.enumerate() if t.name == "etho-jobs"])     # other tests' apps may still be up
    client, c = opened(tmp_path, monkeypatch)
    assert len([t for t in threading.enumerate() if t.name == "etho-jobs"]) == before + 1


@needs_bwrap
def test_a_revision_is_refused_when_two_jobs_are_in_flight(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch, cfg_kw={"max_jobs_per_client": 0})
    a = up(client, c).json()["id"]
    wait_terminal(c.manager, a)
    c.manager.stop()                                                      # the worker stops: new jobs stay queued
    assert up(client, c).status_code == 200 and up(client, c).status_code == 200
    r = client.post(f"/api/projects/{a}/revise", json=REVISE)
    points_onward(friendly_ok(r, 503, "queue_full"))
    assert len(list(c.cfg.jobs_dir.iterdir())) == 3                      # the refused revision left nothing behind
    assert not list((c.cfg.jobs_dir / a / "revisions").glob("*"))


# ---- uploads: validation, limits, sample clip ----------------------------------------------------------------
@needs_bwrap
def test_a_file_that_is_not_a_video_is_rejected_with_a_plain_message_and_no_spend(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch)
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    r = post_job(client, txt, **YES)
    assert r.status_code == 415 and r.json()["error"] == "bad_type" and "use .mp4, .mov, .avi" in r.json()["message"]
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"this is not a video at all" * 200)
    r = post_job(client, fake, **YES)
    assert r.status_code == 400 and r.json()["error"] == "unreadable"
    assert r.json()["message"] == "file is not a readable video"
    f = r.json()["friendly"]
    assert "could not be read as a video" in f and "Nothing was kept or spent" in f and "sample clip" in f
    assert spent_nothing(c) and no_jobs(c)


def test_public_upload_limits_stay_50mb_and_120s(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch)
    lim = client.get("/api/config").json()["limits"]
    assert lim["max_upload_mb"] == 50.0 and lim["max_duration_s"] == 120.0
    assert lim["consent_required"] is True and lim["consent_text"] == CONSENT_TEXT
    assert lim["rate_limit_jobs"] == 50 and lim["rate_window_s"] == 86400.0       # `hosted` raises the count to 50


@needs_bwrap
def test_the_sample_clip_link_is_on_the_home_page_and_downloadable_in_open_mode(tmp_path, monkeypatch):
    client, c = opened(tmp_path, monkeypatch)
    sd = c.cfg.sample_dir
    sd.mkdir(parents=True)
    (sd / "calms21_dev_60s.mp4").write_bytes(c.video.read_bytes())
    (sd / "sample.json").write_text(json.dumps({"sequence": "s", "duration_s": 6.0, "size_bytes": 10,
                                                "trimmed": "6 s"}))
    html = client.get("/").text
    card = html[html.index('id="sample-card"'):html.index("</section>", html.index('id="sample-card"'))]
    assert 'id="sample-dl"' in card and "Download the sample clip" in card
    assert html.index('id="sample-card"') < html.index('<form id="form"')       # outside the form: always visible
    sm = client.get("/api/sample").json()
    assert sm["available"] and sm["download"] == "/api/sample/clip" and sm["definitions"]
    r = client.get("/api/sample/clip")
    assert r.status_code == 200 and r.content == c.video.read_bytes()
    js = (STATIC / "home.js").read_text()
    assert "sample-upload-hint" in js and "dl.hidden = false" in js
    # and the clip it serves is accepted as an upload with the box ticked (the one-step path for a judge)
    assert post_job(client, sd / "calms21_dev_60s.mp4", **YES).status_code == 200
    wait_for(lambda: not c.manager.busy())


def test_server_sentences_become_links_without_markup():
    js = (STATIC / "common.js").read_text() + (STATIC / "home.js").read_text() + (STATIC / "project.js").read_text()
    assert "function linkify" in js and "noopener noreferrer" in js
    assert not re.search(r"\.innerHTML\s*=", js)
    assert "linkify(ex.message)" in js and "linkify(text)" in js and "linkify(e.message)" in js


# ---- the budget move ------------------------------------------------------------------------------------------
def test_meta_tag_budgets_after_the_move(tmp_path):
    assert META_TAG_BUDGETS == {"dev": 15.0, "eval-sam": 25.0, "eval-llm": 1.0, "ablation": 20.0,
                                "demo-reserve": 79.0}
    assert sum(META_TAG_BUDGETS.values()) == HARD_CAPS["meta"] == 140.0         # the Meta total is unchanged
    assert HARD_CAPS == {"meta": 140.0, "baseten": 90.0, "fireworks": 90.0, "together": 5.0}
    assert META_TAG_BUDGETS["eval-sam"] > 24.58 and META_TAG_BUDGETS["eval-llm"] > 0.0712   # what is spent still fits
    led = Ledger(tmp_path)
    led.settle(None, {"provider": "meta", "model": "x", "tag": "eval-llm", "cost_usd": 0.999})
    with pytest.raises(CapExceeded, match="eval-llm"):
        led.reserve("meta", "eval-llm", 0.05)
    led.reserve("meta", "demo-reserve", 60.0)                                     # room for $60 of app spend there
    assert DEMO_RESERVE_OPENS_UTC.startswith("2026-11-03")


# ---- the deployment notes -------------------------------------------------------------------------------------
def test_the_deploy_notes_have_the_env_lines_and_every_drill():
    text = DOC.read_text()
    for line in ("ETHO_APP_OPEN=1", "ETHO_APP_RATE_JOBS=10", "ETHO_APP_RATE_WINDOW_S=86400",
                 "ETHO_APP_DAILY_CAP_USD=8", "ETHO_APP_JUDGING_DAILY_CAP_USD=20", "ETHO_APP_TOTAL_CAP_USD=60",
                 "ETHO_APP_MAX_UPLOAD_MB=50", "ETHO_APP_MAX_DURATION_S=120", "--resolve", "199.38.181.54",
                 "ETHO_APP_MAX_IN_FLIGHT=2", "ETHO_APP_MAX_UPLOADS_PER_CLIENT=1", "ETHO_APP_MAX_JOBS_PER_CLIENT=1",
                 "ETHO_APP_MAX_REJECTS_PER_HOUR=10", "ETHO_APP_UPLOAD_DEADLINE_S=120",
                 "ETHO_APP_KILL_FILE=/home/ubuntu/work/etho/ops/app-kill",
                 "ETHO_APP_READONLY_FILE=/home/ubuntu/work/etho/ops/app-readonly",
                 "X-Forwarded-For: 9.9.9.9", "tail -c1", "$0.42", "keep the site read-only"):
        assert line in text, line
    for drill in ("sample clip", "queue", "cap", "not a video", "no animal", "consent", "per-visitor", "worst case"):
        assert drill.lower() in text.lower(), drill
    # the public health curl goes through the public IP, and drill 7 costs nothing (no key file, a limit of 2)
    assert "curl -s --resolve $HOST:443:$IP https://$HOST/api/health" in text
    assert "no-such-keys-file" in text and "ETHO_APP_RATE_JOBS=2" in text and "| 7 per-visitor limit" in text
    assert "no further SAM evaluation run is possible" in text
