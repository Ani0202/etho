"""Every way live use can be unavailable (a cap, read-only, kill switch, expiry, no code, a full queue, a rate limit)
ends in a plain sentence on the page and the showcase still open: never an HTTP error page or raw JSON in the UI.
The server gives each refusal a `friendly` sentence and /api/config a per-state `message`; static/common.js turns every
failure into one of those sentences. API clients still get the structured status and error code."""

import json
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from etho.app.config import DEFAULT_LIVE_UNTIL
from tests.app_env import post_job
from tests.markers import needs_bwrap
from tests.test_app_hosting import REVISE, hosted, unlock, write_rows
from tests.test_app_showcase import BODY, build_showcase

STATIC = Path(__file__).resolve().parents[1] / "etho" / "app" / "static"
SHOWCASE_NOTE = "showcase"          # every message says the showcase stays open


def sentence(s: str) -> bool:
    """A sentence for people: not JSON, not a status line, not a stack or a path."""
    return (len(s) > 20 and s.strip()[0] not in "{[<" and not re.search(r"HTTP \d{3}|Traceback|\{\"|Error:|/home/", s))


def check_refusal(r, status, code):
    assert r.status_code == status and r.json()["error"] == code, r.text
    f = r.json()["friendly"]
    assert sentence(f), f
    return f


def check_ui_state(client, *, capped=None, mode="live"):
    lv = client.get("/api/config").json()["live"]
    assert lv["can_submit"] is False and lv["mode"] == mode and lv["capped"] == capped
    assert sentence(lv["message"]) and SHOWCASE_NOTE in lv["message"].lower(), lv
    assert client.get("/api/showcases").status_code == 200 and client.get("/").status_code == 200   # still browsable
    return lv["message"]


def test_read_only_renders_a_message_and_keeps_the_showcase(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"read_only": True})
    build_showcase(c, monkeypatch)
    unlock(client)
    check_ui_state(client, mode="read-only")
    check_refusal(post_job(client, c.video), 403, "read_only")
    check_refusal(client.post("/api/projects/sc-calms21-x/revise", json=BODY), 403, "read_only")
    assert client.get("/api/projects/sc-calms21-x").json()["status"] == "done"


def test_kill_switch_and_expiry_render_a_message(tmp_path, monkeypatch):
    kill = tmp_path / "ops" / "app-kill"
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"kill_file": kill})
    unlock(client)
    kill.parent.mkdir(parents=True, exist_ok=True)
    kill.write_text("")
    check_ui_state(client, mode="killed")
    check_refusal(post_job(client, c.video), 503, "killed")
    kill.unlink()
    c.clock.t = DEFAULT_LIVE_UNTIL + timedelta(seconds=1)
    check_ui_state(client, mode="expired")
    check_refusal(post_job(client, c.video), 503, "expired")


def test_no_code_yet_and_a_wrong_code_render_a_message(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    check_refusal(post_job(client, c.video), 401, "access_required")
    check_refusal(client.post("/api/projects/j1/revise", json=REVISE), 401, "access_required")
    check_refusal(client.post("/api/access", json={"code": "wrong-wrong-wrong-wrong"}), 401, "wrong_code")
    lv = client.get("/api/config").json()["live"]
    assert lv["mode"] == "live" and lv["access_required"] and lv["can_submit"] is False        # the unlock card shows


def test_no_usable_code_or_no_bubblewrap_renders_a_message(tmp_path, monkeypatch):
    client, c = hosted(tmp_path / "nocode", monkeypatch, code="short")
    check_ui_state(client, mode="read-only")
    check_refusal(client.post("/api/access", json={"code": "anything-at-all-12345"}), 503, "access_unavailable")
    client, c = hosted(tmp_path / "nobwrap", monkeypatch, bwrap=False)
    unlock(client)
    check_ui_state(client, mode="read-only")


def test_the_daily_and_total_caps_fall_back_to_the_showcase_with_a_message(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 3.0, "total_cap_usd": 20.0})
    unlock(client)
    assert client.get("/api/config").json()["live"]["can_submit"] is True
    write_rows(c.cfg.ledger_path, [{"caller": f"{c.cfg.caller_prefix}old|x", "cost_usd": 2.99, "tag": "dev"}])
    msg = check_ui_state(client, capped="daily_cap")
    assert "budget" in msg and "used up" in msg
    check_refusal(post_job(client, c.video), 429, "daily_cap")
    assert not c.gw.calls

    client2, c2 = hosted(tmp_path / "t", monkeypatch, cfg_kw={"daily_cap_usd": 9.0, "total_cap_usd": 1.0})
    unlock(client2)
    write_rows(c2.cfg.ledger_path, [{"caller": f"{c2.cfg.caller_prefix}old|x", "cost_usd": 0.99, "tag": "dev"}])
    check_ui_state(client2, capped="total_cap")
    check_refusal(post_job(client2, c2.video), 429, "total_cap")


@needs_bwrap
def test_admission_refusals_render_a_message(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"max_queue": 0})                  # a full queue
    unlock(client)
    check_refusal(post_job(client, c.video), 503, "queue_full")
    client, c = hosted(tmp_path / "r", monkeypatch, cfg_kw={"rate_limit_jobs": 1})       # an hourly limit
    unlock(client)
    assert post_job(client, c.video).status_code == 200
    check_refusal(post_job(client, c.video), 429, "rate_limited")
    client, c = hosted(tmp_path / "t", monkeypatch, cfg_kw={"access_throttle_delay_s": 0.0, "access_max_failures": 1})
    client.post("/api/access", json={"code": "x" * 20})
    check_refusal(client.post("/api/access", json={"code": "x" * 20}), 429, "access_throttled")


@needs_bwrap
def test_a_job_estimate_over_the_remaining_budget_is_refused_with_a_message(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"daily_cap_usd": 0.5})          # a 6 s upload is estimated at $0.21
    unlock(client)
    assert post_job(client, c.video).status_code == 200                                # ceiling reserved: $0.46
    r = post_job(client, c.video)
    check_refusal(r, 429, "daily_cap")


def test_every_refusal_code_the_server_can_send_has_a_friendly_sentence():
    from etho.app.server import FRIENDLY
    for code in ("daily_cap", "total_cap", "read_only", "killed", "expired", "access_required", "access_unavailable",
                 "access_throttled", "wrong_code", "queue_full", "rate_limited", "busy"):
        assert sentence(FRIENDLY[code]), code
    for code in ("daily_cap", "total_cap", "read_only", "killed", "expired", "access_required", "queue_full",
                 "rate_limited"):
        assert SHOWCASE_NOTE in FRIENDLY[code].lower(), code


def test_the_pages_use_the_server_sentences_and_react_to_every_live_off_code():
    common = (STATIC / "common.js").read_text()
    home, project = (STATIC / "home.js").read_text(), (STATIC / "project.js").read_text()
    codes = re.search(r"LIVE_OFF = new Set\(\[(.*?)\]\)", common).group(1)
    for code in ("daily_cap", "total_cap", "read_only", "killed", "expired", "access_required", "access_unavailable"):
        assert f'"{code}"' in codes
    assert "body.friendly" in common and "lv.message" in home and "lv.message" in project
    assert "LIVE_OFF.has" in home and "LIVE_OFF.has" in project
    assert not re.search(r"\.innerHTML\s*=", home + project + common)      # messages are set as text, never as markup


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_api_helper_turns_every_failure_into_a_sentence(tmp_path):
    """Runs static/common.js's api() against fake responses: JSON refusals, a proxy's HTML 502, a dropped connection."""
    script = tmp_path / "run.js"
    script.write_text(r"""
const fs = require("fs"), vm = require("vm");
const results = [];
const cases = [
  ["json-friendly", () => ({ ok: false, status: 429, json: async () => ({ error: "daily_cap", message: "daily app spend cap: $3.00 ...", friendly: "Today's live budget is used up. The showcase stays open." }) })],
  ["json-message-only", () => ({ ok: false, status: 400, json: async () => ({ error: "bad_json", message: "body must be JSON" }) })],
  ["proxy-502-html", () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError("Unexpected token <"); } })],
  ["404-html", () => ({ ok: false, status: 404, json: async () => { throw new SyntaxError("x"); } })],
  ["json-array", () => ({ ok: false, status: 500, json: async () => [1, 2] })],
  ["network", () => { throw new TypeError("Failed to fetch"); }],
  ["ok", () => ({ ok: true, status: 200, json: async () => ({ fine: 1 }) })],
];
(async () => {
  for (const [name, make] of cases) {
    const ctx = { fetch: async () => make(), document: {}, console };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(process.argv[2], "utf8") + "\nthis.api = api; this.LIVE_OFF = LIVE_OFF;", ctx);
    try { const v = await ctx.api("/x"); results.push({ name, ok: true, v }); }
    catch (e) { results.push({ name, ok: false, message: e.message, status: e.status, code: e.code }); }
  }
  console.log(JSON.stringify(results));
})();
""")
    out = subprocess.run([NODE, str(script), str(STATIC / "common.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = {r["name"]: r for r in json.loads(out.stdout)}
    assert res["json-friendly"]["message"] == "Today's live budget is used up. The showcase stays open."
    assert res["json-friendly"]["code"] == "daily_cap" and res["json-friendly"]["status"] == 429
    assert res["json-message-only"]["message"] == "body must be JSON"
    for name in ("proxy-502-html", "404-html", "json-array", "network"):
        m = res[name]["message"]
        assert sentence(m) and "502" not in m and "Unexpected" not in m and "Failed to fetch" not in m, (name, m)
    assert "reload" in res["proxy-502-html"]["message"].lower() and res["network"]["status"] == 0
    assert res["ok"]["ok"] is True


def test_a_showcase_and_its_pages_stay_servable_in_every_off_state(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, cfg_kw={"read_only": True})
    build_showcase(c, monkeypatch)
    for path in ("/", "/p/sc-calms21-x", "/static/home.js", "/api/showcases", "/api/projects/sc-calms21-x",
                 "/api/projects/sc-calms21-x/report", "/api/projects/sc-calms21-x/audit"):
        r = client.get(path)
        assert r.status_code == 200, path
