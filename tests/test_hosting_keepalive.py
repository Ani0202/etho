"""Uptime down/up alerts and the Funnel keepalive. No network, no tailscale, no sudo: subprocess is faked."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from etho.app import uptime
from etho.hosting import funnel

T0 = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
URL = "https://etho-vm.tail9158ab.ts.net"


def fake_fetch(results):
    it = iter(results)

    def f(url, timeout=10.0, public_dns=True):
        ok = next(it)
        return {"status": 200 if ok else None, "latency_s": 0.1, "ip": "199.38.181.54", "path": "public-dns",
                "body": json.dumps({"ok": True, "mode": "live"}) if ok else "",
                "error": None if ok else "TimeoutError: timed out"}
    return f


def run_checks(tmp_path, monkeypatch, results):
    monkeypatch.setattr(uptime, "fetch", fake_fetch(results))
    log = tmp_path / "ops" / "uptime.jsonl"
    for i in range(len(results)):
        uptime.check_once(URL, log=log, now=T0 + timedelta(minutes=15 * i))
    notes = tmp_path / "ops" / "notify.jsonl"
    return [json.loads(x) for x in notes.read_text().splitlines()] if notes.exists() else []


def test_down_alert_fires_once_on_second_consecutive_failure(tmp_path, monkeypatch):
    notes = run_checks(tmp_path, monkeypatch, [True, False, True, False, False, False, False])
    assert len(notes) == 1
    n = notes[0]
    assert n["source"] == "uptime" and n["kind"] == "down" and n["sent"] is False
    assert "since 2026-10-30T12:45:00+00:00" in n["message"] and "TimeoutError" in n["message"]
    assert len(n["message"]) < 200


def test_up_alert_on_first_ok_after_two_or_more_failures(tmp_path, monkeypatch):
    notes = run_checks(tmp_path, monkeypatch, [True, False, False, False, True, True])
    assert [n["kind"] for n in notes] == ["down", "up"]
    assert "after 3 failed checks (~45 min)" in notes[1]["message"] and notes[1]["sent"] is False


def test_single_failure_then_recovery_is_silent(tmp_path, monkeypatch):
    assert run_checks(tmp_path, monkeypatch, [True, False, True, False, True]) == []


def test_check_uses_get_and_records_path_and_ip(tmp_path, monkeypatch):
    seen = {}

    class Conn:
        def __init__(self, host, ip, **kw):
            pass

        def request(self, method, target, **kw):
            seen["method"], seen["target"] = method, target

        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda n=0: json.dumps({"ok": True, "mode": "live"}).encode())

        def close(self):
            pass
    monkeypatch.setattr(uptime, "resolve_public", lambda host, timeout=5: "199.38.181.54")
    monkeypatch.setattr(uptime, "_Pinned", Conn)
    row = uptime.check_once(URL, log=tmp_path / "u.jsonl", now=T0)
    assert seen == {"method": "GET", "target": "/api/health"}
    assert row["ok"] and row["path"] == "public-dns" and row["ip"] == "199.38.181.54"


# ---- funnel keepalive -----------------------------------------------------------------------------------------
ON = {"TCP": {"443": {"HTTPS": True}},
      "Web": {"etho-vm.tail9158ab.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}},
      "AllowFunnel": {"etho-vm.tail9158ab.ts.net:443": True}}


class Runner:
    def __init__(self, status=ON, status_rc=0, sudo_rc=0, sudo_err=""):
        self.status, self.status_rc, self.sudo_rc, self.sudo_err, self.calls = status, status_rc, sudo_rc, sudo_err, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == "sudo":
            return SimpleNamespace(returncode=self.sudo_rc, stdout="", stderr=self.sudo_err)
        return SimpleNamespace(returncode=self.status_rc, stdout=json.dumps(self.status), stderr="boom")

    def sudo_calls(self):
        return [c for c in self.calls if c[0] == "sudo"]


def notes(tmp_path):
    f = tmp_path / "ops" / "notify.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_is_serving_shapes():
    assert funnel.is_serving(ON)
    assert not funnel.is_serving({})
    assert not funnel.is_serving({**ON, "AllowFunnel": {"etho-vm.tail9158ab.ts.net:443": False}})     # tailnet only
    other = json.loads(json.dumps(ON))
    other["Web"]["etho-vm.tail9158ab.ts.net:443"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9000"
    assert not funnel.is_serving(other)
    assert not funnel.is_serving(None) and not funnel.is_serving({"AllowFunnel": {"x:443": True}})


def test_serving_does_nothing(tmp_path):
    r = Runner()
    assert funnel.run_once(tmp_path, run=r, now=T0) == "serving"
    assert r.sudo_calls() == [] and notes(tmp_path) == []


def test_offline_marker_wins_and_does_not_even_read_status(tmp_path):
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "funnel-off").write_text("")
    r = Runner(status={})
    assert funnel.run_once(tmp_path, run=r, now=T0) == "offline-marker"
    assert r.calls == [] and notes(tmp_path) == []


def test_reenables_when_off_logs_and_notifies(tmp_path):
    r = Runner(status={})
    assert funnel.run_once(tmp_path, run=r, now=T0) == "reenabled"
    assert r.sudo_calls() == [["sudo", "-n", "/usr/bin/tailscale", "funnel", "--bg", "8000"]]
    log = [json.loads(x) for x in (tmp_path / "ops" / "funnel.jsonl").read_text().splitlines()]
    assert log[0]["event"] == "reenabled" and log[0]["ok"] is True
    n = notes(tmp_path)
    assert len(n) == 1 and n[0]["source"] == "funnel" and n[0]["message"] == "Funnel was off; re-enabled"


def test_sudo_failure_notifies_and_is_rate_limited_to_one_attempt_per_10_min(tmp_path):
    r = Runner(status={}, sudo_rc=1, sudo_err="sudo: a password is required")
    assert funnel.run_once(tmp_path, run=r, now=T0) == "reenable-failed"
    assert funnel.run_once(tmp_path, run=r, now=T0 + timedelta(minutes=5)) == "rate-limited"
    assert len(r.sudo_calls()) == 1
    n = notes(tmp_path)
    assert len(n) == 1 and n[0]["message"].startswith("Funnel off and could not re-enable")
    assert len(n[0]["message"]) < 200
    assert "password" in n[0]["message"]
    assert funnel.run_once(tmp_path, run=r, now=T0 + timedelta(minutes=11)) == "reenable-failed"
    assert len(r.sudo_calls()) == 2


def test_status_command_failure_does_not_re_enable(tmp_path):
    r = Runner(status_rc=1)
    assert funnel.run_once(tmp_path, run=r, now=T0) == "status-error"
    assert r.sudo_calls() == []
    assert json.loads((tmp_path / "ops" / "funnel.jsonl").read_text())["event"] == "status-error"


def test_missing_tailscale_binary_is_handled(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("tailscale")
    assert funnel.run_once(tmp_path, run=run, now=T0) == "status-error"
