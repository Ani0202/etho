"""ADR-003: hosting the app publicly. Access code, cookie, throttle, kill switch, read-only, caps with reservations,
auto-off, 24 h retention, privacy notice, health. No network: the gateway is faked."""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from etho.app import jobgw
from etho.app.access import COOKIE, AccessControl
from etho.app.config import DEFAULT_LIVE_UNTIL, ET, AppConfig
from etho.app.guards import Refused, SpendGuard
from etho.app.jobgw import AppLedger, JobBudgetExceeded, JobGateway
from tests.app_env import make_app, no_jobs, post_job, wait_for, wait_terminal
from tests.app_helpers import FakeGateway
from tests.test_app_showcase import BODY, build_showcase

CODE = "CODE-3f9a-unit-test-code"
START = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
REVISE = {"corrections": [{"kind": "wrong", "behavior": "investigation", "start_s": 5.0, "end_s": 6.0,
                           "correct_label": "none"}]}


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def hosted(tmp_path, monkeypatch, *, code=CODE, cfg_kw=None, gw_kw=None, public=True, bwrap=True):
    """A public-mode app (https test client, fake gateway, injected clock and sandbox probe)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    codefile = tmp_path / "access_code"
    if code is not None:
        codefile.write_text(code + "\n")
    kw = {"public": public, "tag": "dev", "access_code_file": codefile, "rate_limit_jobs": 50, "max_queue": 9,
          "gateway_cache_root": tmp_path / "gwcache", "work_root": tmp_path, "maintenance_interval_s": 3600.0}
    clock = Clock()
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={**kw, **(cfg_kw or {})}, now=clock,
                         gw_kw={"cache_root": tmp_path / "gwcache", **(gw_kw or {})},
                         app_kw={"isolation": lambda: {"mode": "bwrap" if bwrap else "rlimit-only", "detail": "t"}},
                         base_url="https://testserver")
    c.clock, c.code, c.codefile = clock, code, codefile
    return client, c


def unlock(client, code=CODE):
    return client.post("/api/access", json={"code": code})


def spent_nothing(c):
    return not c.gw.calls and not c.cfg.ledger_path.exists() and no_jobs(c)


def wait_done(c, jid):
    assert wait_terminal(c.manager, jid).status == "done"
    return jid


# ---- defaults and config -----------------------------------------------------------------------------------
def test_public_deployment_defaults_follow_the_approved_plan(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("ETHO_APP_"):
            monkeypatch.delenv(k)
    cfg = AppConfig(public=True)
    assert cfg.tag == "demo-reserve" and cfg.daily_cap_usd == 3.0 and cfg.total_cap_usd == 20.0
    assert cfg.retention_hours == 24.0 and cfg.max_upload_mb == 50.0
    assert cfg.live_until == datetime(2026, 11, 10, 23, 59, 59, tzinfo=ET) == DEFAULT_LIVE_UNTIL
    assert cfg.live_until.utcoffset() == timedelta(hours=-5)            # EST: Nov 10 is after the DST change
    assert cfg.private_gateway_cache and cfg.gateway_cache_root == cfg.data_dir / "gateway_cache"
    assert cfg.work_dir == cfg.data_dir / "work" and cfg.chunk_cache_dir == cfg.work_dir / "cache" / "chunks"
    local = AppConfig()
    assert not local.public and local.live_until is None and local.total_cap_usd is None and not local.private_gateway_cache


def test_config_comes_from_env_and_paths_need_not_be_under_home(monkeypatch, tmp_path):
    for k, v in {"ETHO_APP_PUBLIC": "1", "ETHO_APP_TAG": "dev", "ETHO_APP_DAILY_CAP_USD": "1.5",
                 "ETHO_APP_TOTAL_CAP_USD": "7", "ETHO_APP_LIVE_UNTIL": "2026-12-01T00:00:00",
                 "ETHO_APP_ACCESS_CODE_FILE": str(tmp_path / "code"), "ETHO_APP_KILL_FILE": str(tmp_path / "K"),
                 "ETHO_APP_READONLY_FILE": str(tmp_path / "R"), "ETHO_APP_DATA_DIR": str(tmp_path / "data"),
                 "ETHO_APP_SHOWCASE_DIR": str(tmp_path / "sc"), "ETHO_ROOT": str(tmp_path / "repo"),
                 "ETHO_APP_RETENTION_H": "12", "ETHO_APP_READ_ONLY": "yes"}.items():
        monkeypatch.setenv(k, v)
    cfg = AppConfig()
    assert (cfg.public, cfg.tag, cfg.daily_cap_usd, cfg.total_cap_usd, cfg.read_only) == (True, "dev", 1.5, 7.0, True)
    assert cfg.live_until == datetime(2026, 12, 1, tzinfo=ET)          # a naive time is read as US Eastern
    assert cfg.kill_path == tmp_path / "K" and cfg.readonly_path == tmp_path / "R"
    assert cfg.data_dir == tmp_path / "data" and cfg.showcase_dir == tmp_path / "sc"
    assert cfg.root == tmp_path / "repo" and cfg.ledger_path == tmp_path / "repo" / "ops" / "spend.jsonl"
    assert cfg.jobs_dir == tmp_path / "data" / "jobs" and cfg.tmp_dir == tmp_path / "data" / "tmp"
    monkeypatch.setenv("ETHO_APP_LIVE_UNTIL", "none")
    assert AppConfig().live_until is None


# ---- access code, cookie, throttle --------------------------------------------------------------------------
def test_upload_and_revise_need_the_access_code(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    r = post_job(client, c.video)
    assert r.status_code == 401 and r.json()["error"] == "access_required" and spent_nothing(c)
    r = client.post("/api/projects/j1/revise", json=REVISE)
    assert r.status_code == 401 and r.json()["error"] == "access_required"
    r = unlock(client, "not-the-code")
    assert r.status_code == 401 and r.json()["error"] == "wrong_code" and COOKIE not in r.cookies
    assert post_job(client, c.video).status_code == 401
    r = unlock(client)
    assert r.status_code == 200 and r.json()["granted"] is True
    sc = r.headers["set-cookie"].lower()
    assert COOKIE in sc and "httponly" in sc and "samesite=strict" in sc and "secure" in sc and "max-age=7200" in sc
    assert CODE not in r.headers["set-cookie"] and CODE not in r.text
    cfg = client.get("/api/config").json()
    assert cfg["live"]["mode"] == "live" and cfg["live"]["access_granted"] and cfg["live"]["can_submit"]
    jid = post_job(client, c.video).json()["id"]
    wait_done(c, jid)
    r = client.post(f"/api/projects/{jid}/revise", json=REVISE)               # a revision is spending too
    assert r.status_code == 200
    wait_terminal(c.manager, r.json()["job_id"])
    client.cookies.clear()
    assert client.post(f"/api/projects/{jid}/revise", json=REVISE).status_code == 401


def test_a_showcase_revise_is_spending_and_needs_the_code(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert r.status_code == 401 and r.json()["error"] == "access_required" and spent_nothing(c)
    assert client.get("/api/projects/sc-calms21-x").status_code == 200      # browsing needs nothing
    assert unlock(client).status_code == 200
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert r.status_code == 200 and r.json()["forked"]
    wait_terminal(c.manager, r.json()["job_id"])


def test_wrong_codes_are_throttled_per_client_and_a_locked_client_cannot_even_succeed(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    for _ in range(5):
        assert unlock(client, "guess").status_code == 401
    r = unlock(client, "guess")
    assert r.status_code == 429 and r.json()["error"] == "access_throttled" and int(r.headers["retry-after"]) > 0
    assert unlock(client).status_code == 429                  # the right code is refused too: no oracle
    assert client.get("/api/health").status_code == 200       # browsing is unaffected
    c.clock.advance(seconds=901)
    assert unlock(client).status_code == 200                  # the window passed


def test_throttle_is_per_client_plus_a_global_limit(tmp_path):
    codefile = tmp_path / "code"
    codefile.write_text(CODE)
    cfg = AppConfig(public=True, access_code_file=codefile, access_max_failures=3, access_global_failures=5)
    clock = Clock()
    ac = AccessControl(cfg, now=clock)
    for _ in range(3):
        with pytest.raises(Refused) as e:
            ac.attempt("1.1.1.1", "bad")
        assert e.value.code == "wrong_code"
    with pytest.raises(Refused) as e:
        ac.attempt("1.1.1.1", CODE)
    assert e.value.code == "access_throttled"
    assert ac.valid(ac.attempt("2.2.2.2", CODE))              # another client is not locked by it
    for ip in ("3.3.3.3", "4.4.4.4"):
        with pytest.raises(Refused):
            ac.attempt(ip, "bad")
    with pytest.raises(Refused) as e:                          # 5 failures overall: everyone waits
        ac.attempt("5.5.5.5", CODE)
    assert e.value.code == "access_throttled"
    clock.advance(hours=1, seconds=1)
    assert ac.valid(ac.attempt("5.5.5.5", CODE))
    for bad in (None, 5, ["x"], "x" * 5000):                   # junk is a wrong code, never a crash
        with pytest.raises(Refused) as e:
            ac.attempt("9.9.9.9", bad)
        assert e.value.code in ("wrong_code", "access_throttled")


def test_code_comparison_is_constant_time_digest_compare(tmp_path, monkeypatch):
    import hmac as _hmac
    codefile = tmp_path / "code"
    codefile.write_text(CODE)
    ac = AccessControl(AppConfig(public=True, access_code_file=codefile))
    seen = []
    real = _hmac.compare_digest
    monkeypatch.setattr(_hmac, "compare_digest", lambda a, b: seen.append((len(a), len(b))) or real(a, b))
    for cand in ("x", CODE[:-1], CODE + "extra", CODE):
        try:
            ac.attempt(f"c{len(cand)}", cand)
        except Refused:
            pass
    assert seen[:4] == [(32, 32)] * 4        # the code and the guess are hashed first: equal length whatever the guess


def test_cookie_expires_is_tamper_proof_and_dies_when_the_code_rotates(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    assert post_job(client, c.video).status_code == 200
    token = client.cookies.get(COOKIE)
    exp, nonce, mac = token.split(".")
    for forged in (f"{int(exp) + 99999}.{nonce}.{mac}", f"{exp}.{nonce}.{'0' * 64}", f"{exp}.zz.{mac}", "garbage", ""):
        client.cookies.clear()
        client.cookies.set(COOKIE, forged)
        assert client.post("/api/projects/j1/revise", json=REVISE).status_code == 401
    client.cookies.clear()
    client.cookies.set(COOKIE, token)
    assert client.get("/api/config").json()["live"]["can_submit"]
    c.clock.advance(hours=2, seconds=1)                               # short-lived: 2 h
    assert client.get("/api/config").json()["live"]["access_granted"] is False
    assert post_job(client, c.video).status_code == 401
    c.clock.advance(hours=-2, seconds=-1)
    assert client.get("/api/config").json()["live"]["can_submit"]
    c.codefile.write_text("a-brand-new-access-code-9876\n")           # rotated without a restart
    assert client.get("/api/config").json()["live"]["access_granted"] is False
    assert unlock(client, CODE).status_code == 401
    assert unlock(client, "a-brand-new-access-code-9876").status_code == 200
    assert client.post("/api/access/logout").status_code == 200
    assert client.get("/api/config").json()["live"]["access_granted"] is False


def test_a_cookie_from_another_process_is_not_valid(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    token = client.cookies.get(COOKIE)
    other = AccessControl(c.cfg, now=c.clock)                          # a restarted server has a new key
    assert not other.valid(token) and c.app.state.access.valid(token)


def test_without_a_usable_code_a_public_app_is_read_only(tmp_path, monkeypatch):
    for label, code in (("missing", None), ("short", "tooshort"), ("empty", "")):
        client, c = hosted(tmp_path / label, monkeypatch, code=code)
        if label == "missing":
            c.codefile.unlink(missing_ok=True)
        h = client.get("/api/health").json()
        assert h["mode"] == "read-only" and h["access_problem"], (label, h)
        assert unlock(client, "anything-at-all-12345").status_code == 503
        assert post_job(client, c.video).status_code == 403 and spent_nothing(c)


def test_without_bubblewrap_a_public_app_is_read_only(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, bwrap=False)
    unlock(client)
    h = client.get("/api/health").json()
    assert h["mode"] == "read-only" and any("bubblewrap" in x for x in h["reasons"])
    assert post_job(client, c.video).status_code == 403 and spent_nothing(c)


def test_a_localhost_app_without_a_code_file_needs_no_code(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    assert client.get("/api/health").json()["mode"] == "live" and not client.get("/api/health").json()["access_required"]
    assert client.post("/api/access", json={"code": "x"}).status_code == 400
    assert post_job(client, c.video).status_code == 200


# ---- read-only: every spending endpoint -----------------------------------------------------------------------
SPENDING = {"/api/jobs", "/api/projects/{pid}/revise"}
NOT_SPENDING = {"/api/jobs/{jid}/cancel", "/api/access", "/api/access/logout"}


def test_every_post_route_is_classified(tmp_path, monkeypatch):
    """Fails when a POST route is added without deciding whether it can spend: classify it here and in
    server.SAFE_POST (anything not listed there is closed unless live use is on and the code was entered)."""
    from etho.app.server import SAFE_POST
    client, c = hosted(tmp_path, monkeypatch)
    posts = {r.path for r in c.app.routes if "POST" in (getattr(r, "methods", None) or ())}
    assert posts == SPENDING | NOT_SPENDING
    for path in NOT_SPENDING:
        assert SAFE_POST.match(path.replace("{jid}", "jabc"))
    for path in SPENDING:
        assert not SAFE_POST.match(path.replace("{pid}", "jabc"))
    assert not SAFE_POST.match("/api/jobs/jabc/other") and not SAFE_POST.match("/api/access/x")


@pytest.mark.parametrize("variant", ["env", "flag-file", "kill", "expired", "no-cookie"])
def test_read_only_blocks_every_spending_endpoint_and_spends_nothing(tmp_path, monkeypatch, variant):
    cfg_kw = {"read_only": True} if variant == "env" else {}
    if variant == "flag-file":
        cfg_kw["readonly_file"] = tmp_path / "ops" / "app-readonly"
    if variant == "kill":
        cfg_kw["kill_file"] = tmp_path / "ops" / "app-kill"
    client, c = hosted(tmp_path, monkeypatch, cfg_kw=cfg_kw)
    build_showcase(c, monkeypatch)
    if variant != "no-cookie":
        assert unlock(client).status_code == 200            # even with the code: read-only wins
    if variant in ("flag-file", "kill"):
        f = cfg_kw["readonly_file"] if variant == "flag-file" else cfg_kw["kill_file"]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("")
    if variant == "expired":
        c.clock.t = DEFAULT_LIVE_UNTIL + timedelta(seconds=1)
    expect = {"env": (403, "read_only"), "flag-file": (403, "read_only"), "kill": (503, "killed"),
              "expired": (503, "expired"), "no-cookie": (401, "access_required")}[variant]
    for path in SPENDING:
        r = client.post(path.replace("{pid}", "sc-calms21-x"),
                        **({"json": BODY} if "revise" in path else
                           {"files": {"video": ("v.mp4", c.video.read_bytes(), "video/mp4")},
                            "data": {"definitions": json.dumps({"a": "close"})}}))
        assert (r.status_code, r.json()["error"]) == expect, (variant, path, r.text)
    # a route nobody classified is closed too (here: a pretend new endpoint that would spend)
    @c.app.post("/api/new_spender")
    def new_spender():
        return {"spent": True}
    r = client.post("/api/new_spender")
    assert (r.status_code, r.json()["error"]) == expect
    assert spent_nothing(c)
    # browsing stays up in every one of these states
    assert client.get("/").status_code == 200 and client.get("/api/showcases").json()["showcases"]
    assert client.get("/api/projects/sc-calms21-x").json()["status"] == "done"
    assert client.get("/api/projects/sc-calms21-x/overlay").status_code == 200
    assert client.get("/api/projects/sc-calms21-x/audit").status_code == 200
    assert not c.cfg.ledger_path.exists() and not c.gw.calls


def test_read_only_flag_flips_without_a_restart(tmp_path, monkeypatch):
    flag = tmp_path / "ops" / "app-readonly"
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"readonly_file": flag})
    unlock(client)
    assert client.get("/api/health").json()["mode"] == "live"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    assert client.get("/api/health").json()["mode"] == "read-only"
    assert post_job(client, c.video).status_code == 403
    flag.unlink()
    assert client.get("/api/health").json()["mode"] == "live"
    assert post_job(client, c.video).status_code == 200


# ---- kill switch ----------------------------------------------------------------------------------------------
def test_kill_switch_stops_the_running_job_and_the_queue_and_keeps_the_showcase(tmp_path, monkeypatch):
    kill = tmp_path / "ops" / "app-kill"
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"kill_file": kill}, gw_kw={"delay": 0.4})
    build_showcase(c, monkeypatch)
    unlock(client)
    a = post_job(client, c.video).json()["id"]
    wait_for(lambda: c.manager.jobs[a].status == "running" and c.gw.calls, what="first model call")
    b = post_job(client, c.video).json()["id"]
    cc = post_job(client, c.video).json()["id"]
    assert c.manager.jobs[b].status == "queued" and c.app.state.guard.outstanding() > 0
    kill.parent.mkdir(parents=True, exist_ok=True)
    n_at_kill = len(c.gw.calls)
    kill.write_text("")                                              # the operator touches the file
    r = post_job(client, c.video)
    assert r.status_code == 503 and r.json()["error"] == "killed"
    assert wait_terminal(c.manager, a).status == "cancelled"
    assert wait_terminal(c.manager, b).status == "cancelled" and wait_terminal(c.manager, cc).status == "cancelled"
    assert "switched off" in c.manager.jobs[b].message
    assert not any(f"job:{b}|" in str(x.get("caller")) for x in c.gw.calls)        # the queued jobs never called a model
    assert c.app.state.guard.outstanding() == 0                       # every reservation was released
    assert len(c.gw.calls) - n_at_kill <= 2         # at most the calls already in flight when the file appeared
    assert client.get("/api/health").json()["mode"] == "killed"
    assert client.get("/api/projects/sc-calms21-x").status_code == 200
    kill.unlink()                                                     # and back, without a restart
    assert client.get("/api/health").json()["mode"] == "live"


# ---- auto-off -----------------------------------------------------------------------------------------------
def test_live_mode_switches_itself_off_after_the_deadline_by_clock(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    c.clock.t = datetime(2026, 11, 10, 23, 59, 58, tzinfo=ET)
    unlock(client)
    assert client.get("/api/health").json()["mode"] == "live"
    c.clock.t = datetime(2026, 11, 10, 23, 59, 59, tzinfo=ET)          # the stated moment itself is still live
    assert client.get("/api/health").json()["mode"] == "live"
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    c.clock.t = datetime(2026, 11, 11, 0, 0, 0, tzinfo=ET)
    h = client.get("/api/health").json()
    assert h["mode"] == "expired" and h["live_until"].startswith("2026-11-10T23:59:59")
    r = post_job(client, c.video)
    assert r.status_code == 503 and r.json()["error"] == "expired"
    assert client.post(f"/api/projects/{jid}/revise", json=REVISE).json()["error"] == "expired"
    assert client.get("/api/projects/sc-calms21-x").status_code == 200 and client.get("/api/showcases").status_code == 200
    page = client.get("/api/config").json()
    assert page["live"]["mode"] == "expired" and not page["live"]["can_submit"]
    n = len(c.gw.calls)
    assert post_job(client, c.video).status_code == 503 and len(c.gw.calls) == n


def test_expiry_cancels_queued_jobs(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, gw_kw={"delay": 0.3})
    unlock(client)
    a = post_job(client, c.video).json()["id"]
    wait_for(lambda: c.manager.jobs[a].status == "running" and c.gw.calls, what="first call")
    b = post_job(client, c.video).json()["id"]
    c.clock.t = DEFAULT_LIVE_UNTIL + timedelta(seconds=1)
    assert wait_terminal(c.manager, a).status == "cancelled" and wait_terminal(c.manager, b).status == "cancelled"
    assert c.app.state.guard.outstanding() == 0


# ---- caps ---------------------------------------------------------------------------------------------------
def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps({"time": START.isoformat(), "tag": "demo-reserve", **r}) + "\n")


def guard_for(tmp_path, **kw):
    cfg = AppConfig(root=tmp_path, tag="demo-reserve", **kw)
    return SpendGuard(cfg, AppLedger(cfg.ledger_path), now=lambda: START), cfg


def test_concurrent_reservations_cannot_pass_the_daily_cap(tmp_path):
    g, cfg = guard_for(tmp_path, daily_cap_usd=3.0, total_cap_usd=20.0)
    ok, refused = [], []

    def go(i):
        try:
            ok.append((f"j{i}", g.reserve(f"j{i}", 0.5)))        # estimate 0.5 -> ceiling max(1.0, 1.0) = 1.0
        except Refused as e:
            refused.append(e.code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 3 and len(refused) == 37 and set(refused) == {"daily_cap"}
    assert sum(v for _, v in ok) == pytest.approx(3.0) and g.outstanding() == pytest.approx(3.0)
    g.release(ok[0][0])                                          # a finished job frees its reservation
    assert g.reserve("later", 0.5) == pytest.approx(1.0)
    with pytest.raises(Refused):
        g.reserve("more", 0.5)


def test_total_cap_counts_everything_ever_spent_and_survives_a_restart(tmp_path):
    cfg = AppConfig(root=tmp_path, tag="demo-reserve", daily_cap_usd=3.0, total_cap_usd=20.0)
    write_rows(cfg.ledger_path, [{"caller": "job:old1|x", "cost_usd": 9.0,
                                  "time": (START - timedelta(days=3)).isoformat()},       # long ago: total only
                                 {"caller": "job:old2|x", "cost_usd": 10.0,
                                  "time": (START - timedelta(days=2)).isoformat()}])
    g = SpendGuard(cfg, AppLedger(cfg.ledger_path), now=lambda: START)                    # a fresh process
    s = g.status()
    assert s["spent_total_usd"] == 19.0 and s["spent_24h_usd"] == 0.0 and s["total_remaining_usd"] == 1.0
    assert s["daily_remaining_usd"] == 3.0 and s["remaining_usd"] == 1.0
    with pytest.raises(Refused) as e:
        g.reserve("j1", 1.5)
    assert e.value.code == "total_cap" and "total app spend cap" in e.value.message
    assert g.reserve("j1", 0.4) == pytest.approx(0.9)             # max(2 x 0.4, 0.4 + 0.5), within the $1.00 headroom
    assert g.outstanding() == pytest.approx(0.9)
    with pytest.raises(Refused):
        g.reserve("j2", 0.7)                                      # what j1 may still spend is committed


def test_only_the_apps_own_rows_on_its_tag_count(tmp_path):
    cfg = AppConfig(root=tmp_path, tag="demo-reserve", daily_cap_usd=3.0, total_cap_usd=20.0)
    write_rows(cfg.ledger_path, [
        {"caller": "job:a|x", "cost_usd": 1.0},                                          # app job on the tag: counts
        {"caller": "job:b|x", "cost_usd": 5.0, "tag": "dev"},                            # another tag: no
        {"caller": "scripts.record_demo:run", "cost_usd": 7.0},                          # the lead's recording: no
        {"caller": "ledger:abandoned-reservation", "cost_usd": 0.5, "abandoned_reservation": "r1",
         "maybe_billed": True},                                                          # a crash mid-call: counts
        {"caller": "ledger:abandoned-reservation", "cost_usd": 4.0, "abandoned_reservation": "r2", "tag": "dev"}])
    g = SpendGuard(cfg, AppLedger(cfg.ledger_path), now=lambda: START)
    assert g.spent_total() == pytest.approx(1.5) and g.spent_24h() == pytest.approx(1.5)


def test_daily_window_is_rolling_not_calendar(tmp_path):
    cfg = AppConfig(root=tmp_path, tag="demo-reserve", daily_cap_usd=3.0)
    write_rows(cfg.ledger_path, [{"caller": "job:a|x", "cost_usd": 2.9, "time": "2026-10-28T23:00:00+00:00"}])
    near = SpendGuard(cfg, AppLedger(cfg.ledger_path), now=lambda: datetime(2026, 10, 29, 1, tzinfo=timezone.utc))
    with pytest.raises(Refused):                  # two hours later and across UTC midnight: the money still counts
        near.reserve("j", 0.5)
    later = SpendGuard(cfg, AppLedger(cfg.ledger_path), now=lambda: datetime(2026, 10, 29, 23, 1, tzinfo=timezone.utc))
    assert later.reserve("j", 0.5) > 0


def test_job_gateway_reserves_the_ceiling_for_calls_in_flight(tmp_path, monkeypatch):
    """Eight parallel calls used to all pass the ceiling check before any of them had spent a cent."""
    monkeypatch.setattr(jobgw, "est_chat_usd", lambda *a, **k: 0.2)
    state = {"now": 0, "peak": 0}

    class Slow(FakeGateway):
        def chat(self, *a, **k):
            with self.lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            try:
                return super().chat(*a, **k)
            finally:
                with self.lock:
                    state["now"] -= 1
    fake = Slow(tmp_path / "s.jsonl", cost=0.2, delay=0.15)
    jg = JobGateway(fake, job_id="j1", tag="dev", max_cost_usd=0.5)
    out = []

    def go():
        try:
            jg.chat("meta", "muse-spark-1.3", [{"role": "user", "content": "x"}], effort="low")
            out.append("ok")
        except JobBudgetExceeded:
            out.append("stopped")
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("ok") == 2 and out.count("stopped") == 6
    assert state["peak"] <= 2 and len(fake.calls) == 2 and jg.total_cost == pytest.approx(0.4)
    assert jg.inflight_usd == pytest.approx(0.0)


def test_job_gateway_counts_a_cost_the_gateway_booked_for_a_failed_call(tmp_path):
    ledger = tmp_path / "s.jsonl"
    write_rows(ledger, [{"caller": "job:j1|x", "cost_usd": 0.45, "maybe_billed": True}])      # an ambiguous failure
    led = AppLedger(ledger)
    jg = JobGateway(FakeGateway(ledger, cost=0.01), job_id="j1", tag="dev", max_cost_usd=0.5,
                    spent=lambda: led.job_cost("j1"))
    with pytest.raises(JobBudgetExceeded):      # $0.45 booked + the next call's worst case (about $0.09) > $0.50
        jg.chat("meta", "muse-spark-1.3", [{"role": "user", "content": "x" * 3000}], effort="low", max_tokens=20000)


def test_the_job_ceiling_is_reserved_when_the_job_is_admitted(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 1.0}, gw_kw={"delay": 0.3})
    unlock(client)
    a = post_job(client, c.video).json()["id"]
    g = c.app.state.guard
    assert c.manager.jobs[a].ceiling_usd == pytest.approx(0.71, abs=0.02) and g.reserved(a) == c.manager.jobs[a].ceiling_usd
    wait_terminal(c.manager, a)
    assert g.reserved(a) is None and g.outstanding() == 0                       # released when the job ended


def test_caps_via_the_api_daily_and_total(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 3.0, "total_cap_usd": 0.5})
    unlock(client)
    r = post_job(client, c.video)                                      # estimate ~ $0.21, ceiling min($0.71, $0.5)
    assert r.status_code == 200
    r2 = post_job(client, c.video)                                     # nothing left of the $0.50 total
    assert r2.status_code == 429 and r2.json()["error"] == "total_cap"
    wait_terminal(c.manager, r.json()["id"])
    cap = client.get("/api/config").json()["cap"]
    assert cap["total_cap_usd"] == 0.5 and cap["total_remaining_usd"] < 0.5


# ---- retention ----------------------------------------------------------------------------------------------
def test_retention_timer_deletes_everything_a_job_left(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"maintenance_interval_s": 0.1})
    unlock(client)
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    d = c.cfg.jobs_dir / jid
    gwc = c.cfg.gateway_cache_root
    chunks = list(c.cfg.chunk_cache_dir.iterdir())
    assert d.exists() and chunks and (d / "cache_refs.json").exists()
    assert list(gwc.rglob("*.json.gz")) and list(gwc.rglob("*.txt.gz")) and list((gwc / "locks").iterdir())
    c.cfg.tmp_dir.mkdir(parents=True, exist_ok=True)                   # the planner's frames and an upload spool
    (c.cfg.tmp_dir / "etho_plan_x").mkdir()
    (c.cfg.tmp_dir / "etho_plan_x" / "f0.jpg").write_bytes(b"frame")
    (c.cfg.tmp_dir / "spool").write_bytes(b"video bytes")
    rows_before = len(c.app.state.ledger.job_rows(jid))
    # a revision's rows and cache entry are covered too
    (d / "revisions" / "1").mkdir(parents=True)
    write_rows(c.cfg.ledger_path, [{"caller": f"job:{jid}-r1|x", "cost_usd": 0.0, "endpoint": "/chat/completions",
                                    "request_key": "ab" * 32}])
    (gwc / "ab").mkdir(exist_ok=True)
    (gwc / "ab" / ("ab" * 32 + ".json.gz")).write_bytes(b"x")
    assert c.app.state.retention.run_once(time.time() + 23 * 3600)["removed_projects"] == 0   # 23 h: still there
    assert d.exists() and chunks[0].exists() and list(gwc.rglob("*.json.gz"))
    c.app.state.retention.clock = lambda: time.time() + 25 * 3600       # the timer thread, not startup or a submit
    wait_for(lambda: not d.exists(), timeout=10, what="the timer to delete the project")
    wait_for(lambda: not chunks[0].exists(), timeout=5, what="chunks")
    assert not list(gwc.rglob("*.gz")) and not list((gwc / "locks").glob("*"))
    assert not list(c.cfg.tmp_dir.iterdir())
    assert client.get(f"/api/projects/{jid}").status_code == 404 and jid not in c.manager.jobs
    ids = [json.loads(x)["file_id"] for x in (c.cfg.data_dir / "meta_files_undeleted.jsonl").read_text().splitlines()]
    assert ids and all(i.startswith("file-") for i in ids)              # Meta-side copies: recorded, not deletable
    assert len(c.app.state.ledger.job_rows(jid)) == rows_before         # the audit ledger is not rewritten


def test_retention_leaves_a_shared_gateway_cache_alone(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"gateway_cache_root": None, "public": False,
                                                        "access_code_file": None},
                       gw_kw={"cache_root": tmp_path / "shared"})
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    n = len(list((tmp_path / "shared").rglob("*.gz")))
    assert n
    assert c.app.state.retention.run_once(time.time() + 25 * 3600)["removed_projects"] == 1
    assert not (c.cfg.jobs_dir / jid).exists()
    assert len(list((tmp_path / "shared").rglob("*.gz"))) == n          # the benchmark's entries are never deleted


def test_retention_sweeps_orphans_in_the_private_cache_and_chunk_dir(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    old = c.cfg.gateway_cache_root / "zz" / "orphan.json.gz"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    chunk = c.cfg.chunk_cache_dir / "deadbeef"
    chunk.mkdir(parents=True)
    (chunk / "c000.mp4").write_bytes(b"x")
    fresh = c.cfg.gateway_cache_root / "zz" / "fresh.json.gz"
    fresh.write_bytes(b"x")
    rep = c.app.state.retention.run_once(time.time() + 25 * 3600)
    assert not old.exists() and not chunk.exists() and not fresh.exists() and rep["gateway_cache_files"] >= 2
    fresh2 = c.cfg.gateway_cache_root / "zz" / "fresh2.json.gz"
    fresh2.parent.mkdir(exist_ok=True)               # the emptied directory was swept too
    fresh2.write_bytes(b"x")
    c.app.state.retention.run_once()
    assert fresh2.exists()                                              # young entries stay


def test_retention_counts_from_the_upload_and_default_is_24h(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    assert c.cfg.retention_hours == 24.0
    unlock(client)
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    client.post(f"/api/projects/{jid}/revise", json=REVISE)             # activity later does not extend the life
    wait_for(lambda: not c.manager.active_for(jid), what="revision")
    rep = c.app.state.retention
    assert rep.run_once(time.time() + 23.5 * 3600)["removed_projects"] == 0
    assert rep.run_once(time.time() + 24.5 * 3600)["removed_projects"] == 1


# ---- privacy notice, health, nothing leaks ----------------------------------------------------------------
def test_privacy_notice_is_on_the_upload_page(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    html = client.get("/").text
    for needle in ("Privacy notice", "Meta's Model API in Standard mode", "does not use them for training",
                   "Fireworks", "deleted", 'id="ret-h"', "hours after upload", "cannot be deleted by this site",
                   "right to share"):
        assert needle in html, needle
    js = client.get("/static/home.js").text
    assert "retention_hours" in js                                       # the number of hours comes from the server
    assert client.get("/api/config").json()["retention_hours"] == 24.0
    assert html.index('id="privacy"') > html.index('id="form"')


def test_the_access_code_is_never_shown_sent_or_logged(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    client, c = hosted(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    unlock(client, "a-wrong-guess-123456")
    r = unlock(client)
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    bodies = [r.text, str(r.headers), str(dict(client.cookies))]
    for path in ("/", "/p/" + jid, "/static/home.js", "/static/common.js", "/static/project.js", "/static/index.html",
                 "/static/ethogram.js", "/static/app.css", "/api/config", "/api/health", "/api/showcases",
                 f"/api/projects/{jid}", f"/api/projects/{jid}/audit", f"/api/jobs/{jid}",
                 "/api/projects/sc-calms21-x", "/api/projects/sc-calms21-x/audit"):
        x = client.get(path)
        bodies += [x.text if "overlay" not in path else "", str(x.headers)]
    blob = "\n".join(bodies)
    assert CODE not in blob and "a-wrong-guess" not in blob
    assert CODE not in caplog.text and "a-wrong-guess" not in caplog.text
    assert any("access code accepted" in m for m in caplog.messages)     # the event is logged, never the code
    assert any("access code refused" in m for m in caplog.messages)
    log_files = list(tmp_path.rglob("*.log")) + list(tmp_path.rglob("calls.jsonl"))
    assert all(CODE not in p.read_text() for p in log_files)
    assert "codefile" not in blob and str(c.codefile) not in blob
    # nothing in the page's own source holds a code value or a path to the code file
    for f in (Path(__file__).resolve().parents[1] / "etho" / "app" / "static").iterdir():
        assert CODE not in f.read_text() and "access_code" not in f.read_text()


def test_health_reports_mode_sandbox_and_caps_without_secrets(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    h = client.get("/api/health").json()
    assert h["ok"] and h["mode"] == "live" and h["reasons"] == [] and h["sandbox"]["mode"] == "bwrap"
    assert h["caps"]["daily_cap_usd"] == 3.0 and h["caps"]["total_cap_usd"] == 20.0
    assert {"daily_remaining_usd", "total_remaining_usd", "reserved_usd", "spent_24h_usd", "spent_total_usd",
            "remaining_usd"} <= set(h["caps"])
    assert h["live_until"].startswith("2026-11-10T23:59:59") and h["access_required"] and h["retention_hours"] == 24.0
    assert {"queue", "running", "retention", "client_seen_as", "time"} <= set(h)
    text = json.dumps(h)
    for secret in (CODE, str(c.codefile), "KEY", "secret", "Bearer"):
        assert secret not in text, secret
    jid = post_job(client, c.video).json()["id"]
    wait_done(c, jid)
    h2 = client.get("/api/health").json()
    assert h2["caps"]["spent_24h_usd"] > 0 and h2["caps"]["remaining_usd"] < h["caps"]["remaining_usd"]


def test_nothing_from_one_visitors_upload_is_visible_to_another(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    a = wait_done(c, post_job(client, c.video).json()["id"])
    b = wait_done(c, post_job(client, c.video).json()["id"])
    audit_b = json.dumps(client.get(f"/api/projects/{b}/audit").json())
    proj_b = json.dumps(client.get(f"/api/projects/{b}").json())
    assert {d["source"]["source"] for d in client.get(f"/api/projects/{b}/audit").json()["detectors"]} <= {"cache", "seed", "fresh"}
    assert a not in audit_b and a not in proj_b                          # the shared detector cache never names job A
    assert a not in client.get("/api/showcases").text and a not in client.get("/api/config").text
    assert a not in client.get("/api/health").text and a not in client.get("/").text
    other = fresh_client(client)
    assert other.get(f"/api/jobs/{a}").status_code == 200                # a link is a capability (ADR-003 item 8) ...
    assert other.get("/api/jobs/jdoesnotexist").status_code == 404       # ... and there is no listing to find them by
    assert all(x not in other.get("/api/showcases").text for x in (a, b))
    assert not any((c.cfg.jobs_dir / a).rglob(b)) and b not in (c.cfg.jobs_dir / a / "job.json").read_text()


def fresh_client(client):
    from fastapi.testclient import TestClient
    return TestClient(client.app, base_url="https://testserver")


def test_the_handlers_refuse_on_their_own_even_if_the_middleware_let_a_request_through(tmp_path, monkeypatch):
    import re

    from etho.app import server as srv
    monkeypatch.setattr(srv, "SAFE_POST", re.compile(r".*"))          # pretend the middleware gate is bypassed
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"read_only": True})
    unlock(client)
    r = post_job(client, c.video)
    assert r.status_code == 403 and r.json()["error"] == "read_only"
    r = client.post("/api/projects/j1/revise", json=REVISE)
    assert r.status_code == 403 and r.json()["error"] == "read_only"
    assert spent_nothing(c)
    client2, c2 = hosted(tmp_path / "nocode", monkeypatch)             # live, but no code entered
    assert post_job(client2, c2.video).status_code == 401
    assert client2.post("/api/projects/j1/revise", json=REVISE).status_code == 401
    assert spent_nothing(c2)


def test_the_app_gateway_keeps_the_shared_ledger_but_can_use_a_private_cache(tmp_path):
    from etho.app.server import AppGatewayConfig
    from etho.gateway import Gateway
    shared = AppGatewayConfig(root=tmp_path)
    assert shared.cache_dir == tmp_path / "cache" / "gateway" and shared.ledger_path == tmp_path / "ops" / "spend.jsonl"
    private = AppGatewayConfig(root=tmp_path, cache_override=tmp_path / "mine")
    gw = Gateway(private)
    assert gw.cache.root == tmp_path / "mine" and gw.ledger.path == tmp_path / "ops" / "spend.jsonl"


def test_paths_come_from_env_not_from_the_users_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ETHO_SECRETS", str(tmp_path / "keys.env"))
    from etho.gateway import GatewayConfig
    assert GatewayConfig().secrets_path == tmp_path / "keys.env"
    cfg = AppConfig(root=tmp_path / "repo", data_root=tmp_path / "data", public=True)
    for p in (cfg.jobs_dir, cfg.detector_cache_dir, cfg.gateway_cache_root, cfg.work_dir, cfg.tmp_dir, cfg.kill_path,
              cfg.readonly_path, cfg.chunk_cache_dir):
        assert str(p).startswith(str(tmp_path)), p
