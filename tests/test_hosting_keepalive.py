"""Uptime down/up alerts and the Funnel keepalive. No network, no tailscale, no sudo: subprocess is faked."""

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from etho.app import uptime
from etho.hosting import funnel
from tests.markers import needs_git

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
    assert len(notes(tmp_path)) == 1                       # same error: no second notification
    state = json.loads((tmp_path / "ops" / ".funnel-state.json").read_text())
    assert state["fail_count"] == 2 and "password" in state["last_error"]
    funnel.run_once(tmp_path, run=r, now=T0 + timedelta(hours=7))
    assert len(notes(tmp_path)) == 2                       # again after 6 h
    r.sudo_err = "sudo: another error"
    funnel.run_once(tmp_path, run=r, now=T0 + timedelta(hours=8))
    assert len(notes(tmp_path)) == 3                       # a changed error notifies at once
    r.status = ON
    assert funnel.run_once(tmp_path, run=r, now=T0 + timedelta(hours=9)) == "serving"
    assert not (tmp_path / "ops" / ".funnel-state.json").exists()


def test_marker_created_during_status_read_wins(tmp_path):
    marker = tmp_path / "ops" / "funnel-off"

    class R(Runner):
        def __call__(self, cmd, **kw):
            res = super().__call__(cmd, **kw)
            marker.write_text("")                          # operator acts while we read the status
            return res
    r = R(status={})
    assert funnel.run_once(tmp_path, run=r, now=T0) == "offline-marker"
    assert r.sudo_calls() == [] and not (tmp_path / "ops" / ".funnel-state.json").exists()


def test_naive_state_timestamp_is_utc(tmp_path):
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / ".funnel-state.json").write_text(json.dumps({"last_attempt": "2026-10-30T11:58:00"}))
    r = Runner(status={})
    assert funnel.run_once(tmp_path, run=r, now=T0) == "rate-limited"


def test_persistent_status_error_notifies_after_three_then_rate_limited(tmp_path):
    r = Runner(status_rc=1)
    for i in range(2):
        funnel.run_once(tmp_path, run=r, now=T0 + timedelta(minutes=5 * i))
    assert notes(tmp_path) == []
    for i in range(2, 6):
        funnel.run_once(tmp_path, run=r, now=T0 + timedelta(minutes=5 * i))
    assert len(notes(tmp_path)) == 1 and "unreadable" in notes(tmp_path)[0]["message"]
    assert r.sudo_calls() == []


@needs_git
def test_every_bin_script_in_install_autostart_is_executable_in_git():
    root = Path(__file__).resolve().parents[1]
    names = set(re.findall(r"\$root/bin/([\w-]+)", (root / "bin" / "install-autostart").read_text()))
    assert {"lead-watchdog", "codex-queue", "uptime-check", "funnel-keepalive"} <= names
    out = subprocess.run(["git", "ls-files", "-s", "--"] + [f"bin/{n}" for n in sorted(names)], cwd=root,
                         capture_output=True, text=True, check=True).stdout.splitlines()
    modes = {line.split("\t")[1]: line.split()[0] for line in out}
    assert modes == {f"bin/{n}": "100755" for n in names}


def test_alert_failure_does_not_break_check_and_garbage_lines_are_tolerated(tmp_path, monkeypatch, capsys):
    log = tmp_path / "ops" / "uptime.jsonl"
    log.parent.mkdir()
    log.write_bytes(b'garbage\n[1]\n5\n{"time": "2026-10-30T11:30:00", "ok": false, "error": "x"}\n{"time": "2026-10')
    monkeypatch.setattr(uptime, "fetch", fake_fetch([False]))
    row = uptime.check_once(URL, log=log, now=T0)
    assert row["ok"] is False
    lines = log.read_text().splitlines()
    assert json.loads(lines[-1])["time"] == T0.isoformat()          # truncated line did not swallow the new row
    n = [json.loads(x) for x in (log.parent / "notify.jsonl").read_text().splitlines()]
    assert len(n) == 1 and n[0]["kind"] == "down"                  # naive earlier timestamp read as UTC
    monkeypatch.setattr(uptime, "_alert", lambda *a: 1 / 0)
    monkeypatch.setattr(uptime, "fetch", fake_fetch([True]))
    assert uptime.check_once(URL, log=log, now=T0 + timedelta(minutes=15))["ok"] is True
    assert "alert failed" in capsys.readouterr().err


def test_status_command_failure_does_not_re_enable(tmp_path):
    r = Runner(status_rc=1)
    assert funnel.run_once(tmp_path, run=r, now=T0) == "status-error"
    assert r.sudo_calls() == []
    assert json.loads((tmp_path / "ops" / "funnel.jsonl").read_text())["event"] == "status-error"


def test_missing_tailscale_binary_is_handled(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("tailscale")
    assert funnel.run_once(tmp_path, run=run, now=T0) == "status-error"
