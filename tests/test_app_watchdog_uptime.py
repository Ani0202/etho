"""bin/app-watchdog (hung-service restart), the external uptime check and its report. A local fake health server and a
fake systemctl stand in for the real ones; nothing leaves the machine."""

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from etho.app import uptime

ROOT = Path(__file__).resolve().parents[1]


class Health(BaseHTTPRequestHandler):
    mode = "ok"          # ok | hang | 500 | bad-body | ok-readonly
    delay = 0.0

    def do_GET(self):
        if self.path != "/api/health":
            self.send_response(404)
            self.end_headers()
            return
        m = type(self).mode
        if m == "hang":
            time.sleep(type(self).delay or 5)
            return
        code, body = 200, {"ok": True, "mode": "read-only" if m == "ok-readonly" else "live"}
        if m == "500":
            code, body = 500, {"ok": False}
        if m == "bad-body":
            body = {"status": "fine"}
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    Health.mode, Health.delay = "ok", 0.0
    s = HTTPServer(("127.0.0.1", 0), Health)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


@pytest.fixture
def fake_systemctl(tmp_path):
    f = tmp_path / "systemctl"
    calls = tmp_path / "calls.txt"
    f.write_text(f'#!/usr/bin/env bash\necho "$@" >> {calls}\n'
                 f'[ "$1" = "is-active" ] && [ -f {tmp_path}/inactive ] && exit 3\nexit 0\n')
    f.chmod(0o755)
    return f, calls


def watchdog(tmp_path, url, systemctl, **env):
    e = {**os.environ, "ETHO_HEALTH_URL": url + "/api/health", "ETHO_SYSTEMCTL": str(systemctl),
         "ETHO_WATCHDOG_STATE": str(tmp_path / "state"), "ETHO_WATCHDOG_LOG": str(tmp_path / "wd.log"),
         "ETHO_WATCHDOG_TIMEOUT": "1", **env}
    return subprocess.run([str(ROOT / "bin" / "app-watchdog")], env=e, capture_output=True, text=True, timeout=30)


def restarts(calls):
    return [x for x in (calls.read_text().splitlines() if calls.exists() else []) if x.startswith("restart")]


def test_a_healthy_service_is_left_alone(tmp_path, server, fake_systemctl):
    sc, calls = fake_systemctl
    for _ in range(3):
        assert watchdog(tmp_path, server, sc).returncode == 0
    assert restarts(calls) == []
    Health.mode = "ok-readonly"                                  # read-only is healthy: the showcase is being served
    watchdog(tmp_path, server, sc)
    assert restarts(calls) == []


@pytest.mark.parametrize("mode", ["hang", "500", "bad-body"])
def test_two_failed_checks_in_a_row_restart_the_service(tmp_path, server, fake_systemctl, mode):
    sc, calls = fake_systemctl
    Health.mode = mode
    watchdog(tmp_path, server, sc)
    assert restarts(calls) == []                                 # one failure is not enough
    watchdog(tmp_path, server, sc)
    assert restarts(calls) == ["restart etho-app"]
    assert "restarting etho-app after 2 failed health checks" in (tmp_path / "wd.log").read_text()


def test_a_good_check_in_between_resets_the_count(tmp_path, server, fake_systemctl):
    sc, calls = fake_systemctl
    Health.mode = "500"
    watchdog(tmp_path, server, sc)
    Health.mode = "ok"
    watchdog(tmp_path, server, sc)
    Health.mode = "500"
    watchdog(tmp_path, server, sc)
    assert restarts(calls) == []


def test_restarts_are_spaced_out(tmp_path, server, fake_systemctl):
    sc, calls = fake_systemctl
    Health.mode = "500"
    for _ in range(6):
        watchdog(tmp_path, server, sc)
    assert len(restarts(calls)) == 1                             # the 120 s minimum gap stops a restart loop
    assert "not restarting" in (tmp_path / "wd.log").read_text()
    for _ in range(2):
        watchdog(tmp_path, server, sc, ETHO_WATCHDOG_MIN_GAP="0")
    assert len(restarts(calls)) == 2


def test_a_service_stopped_on_purpose_is_not_restarted(tmp_path, server, fake_systemctl):
    sc, calls = fake_systemctl
    (tmp_path / "inactive").write_text("")
    Health.mode = "500"
    for _ in range(4):
        watchdog(tmp_path, server, sc)
    assert restarts(calls) == []


def test_a_refused_connection_counts_as_a_failure(tmp_path, fake_systemctl):
    sc, calls = fake_systemctl
    for _ in range(2):
        watchdog(tmp_path, "http://127.0.0.1:9", sc)
    assert restarts(calls) == ["restart etho-app"]


# ---- external uptime check ---------------------------------------------------------------------------------
def test_check_writes_one_line_per_check_with_status_latency_and_mode(tmp_path, server):
    log = tmp_path / "ops" / "uptime.jsonl"
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    row = uptime.check_once(server, log=log, now=now)
    assert row["ok"] is True and row["status"] == 200 and row["mode"] == "live" and row["url"] == server
    assert row["time"] == "2026-11-05T12:00:00+00:00" and 0 <= row["latency_s"] < 5 and row["path"] == "system-dns"
    Health.mode = "500"
    bad = uptime.check_once(server, log=log, now=now + timedelta(minutes=15))
    assert bad["ok"] is False and bad["status"] == 500 and "error" in bad
    Health.mode = "hang"
    Health.delay = 3
    slow = uptime.check_once(server, timeout=0.5, log=log, now=now + timedelta(minutes=30))
    assert slow["ok"] is False and slow["status"] is None and "timed out" in slow["error"].lower()
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert [r["ok"] for r in rows] == [True, False, False] and all({"time", "url", "status", "latency_s", "mode"} <= set(r)
                                                                    for r in rows)


def test_check_reports_a_dead_host_without_raising(tmp_path):
    row = uptime.check_once("http://127.0.0.1:9", timeout=1, log=tmp_path / "u.jsonl")
    assert row["ok"] is False and row["status"] is None and row["error"]


def test_public_url_checks_resolve_with_a_public_resolver_not_the_system_one(monkeypatch):
    seen = {}
    monkeypatch.setattr(uptime, "resolve_public", lambda host, timeout=5: seen.setdefault("host", host) and "203.0.113.7")

    class Conn:
        def __init__(self, host, ip, **kw):
            seen["pinned"] = (host, ip, kw.get("port"))

        def request(self, *a, **k):
            seen["request"] = a

        def getresponse(self):
            raise OSError("stop here")

        def close(self):
            pass
    monkeypatch.setattr(uptime, "_Pinned", Conn)
    r = uptime.fetch("https://etho.tail1234.ts.net/api/health")
    assert seen["host"] == "etho.tail1234.ts.net" and seen["pinned"] == ("etho.tail1234.ts.net", "203.0.113.7", 443)
    assert r["path"] == "public-dns" and r["ip"] == "203.0.113.7" and "OSError" in r["error"]
    seen.clear()
    uptime.fetch("https://etho.tail1234.ts.net/x", public_dns=False)              # --system-dns skips the resolver
    assert "host" not in seen


def test_cli_check_needs_a_url_and_exits_nonzero_on_failure(tmp_path, server):
    env = {**os.environ, "ETHO_ROOT": str(tmp_path)}
    env.pop("ETHO_PUBLIC_URL", None)
    py = [str(ROOT / ".venv" / "bin" / "python"), "-m", "etho.app.uptime"]
    if not Path(py[0]).exists():
        py[0] = "python3"
    r = subprocess.run([*py, "check"], env=env, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2 and "no public URL" in r.stderr
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "public-url").write_text(server + "\n")
    r = subprocess.run([*py, "check"], env=env, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True
    assert len((tmp_path / "ops" / "uptime.jsonl").read_text().splitlines()) == 1
    Health.mode = "500"
    assert subprocess.run([*py, "check"], env=env, capture_output=True, text=True, cwd=ROOT).returncode == 1
    out = subprocess.run([*py, "report"], env=env, capture_output=True, text=True, cwd=ROOT).stdout
    assert "1/2 checks ok (50.0%)" in out and "1 outage" in out


# ---- report --------------------------------------------------------------------------------------------------
def rows_at(start, spec):
    out = []
    for i, (ok, lat, mode) in enumerate(spec):
        t = (start + timedelta(minutes=15 * i)).isoformat(timespec="seconds")
        r = {"time": t, "url": "https://x.ts.net", "status": 200 if ok else None, "latency_s": lat, "mode": mode if ok else None,
             "ok": ok, "path": "public-dns"}
        if not ok:
            r["error"] = "TimeoutError: timed out"
        out.append(r)
    return out


def test_report_counts_availability_outages_latency_and_modes():
    start = datetime(2026, 11, 4, 21, tzinfo=timezone.utc)
    rows = rows_at(start, [(True, 0.2, "live")] * 4 + [(False, 10.0, None)] * 3 + [(True, 0.4, "live")] * 2
                   + [(True, 0.3, "read-only")] + [(False, 10.0, None)])
    s = uptime.summarize(rows)
    assert s["checks"] == 11 and s["ok"] == 7 and s["availability_pct"] == pytest.approx(63.64)
    assert s["last_ok"] is False and s["modes"]["live"] == 6 and s["modes"]["read-only"] == 1
    assert s["outages"][0]["minutes"] == 45.0 and s["outages"][0]["error"].startswith("TimeoutError")
    assert s["outages"][1]["to"] is None                                  # the last one is still going
    assert s["latency_s"]["p50"] == 0.2 and s["latency_s"]["max"] == 0.4
    text = uptime.format_report(s)
    assert "7/11 checks ok (63.64%)" in text and "2 outage(s)" in text and "still down" in text
    only_recent = uptime.summarize(rows, since=start + timedelta(hours=2))
    assert only_recent["checks"] == 3
    assert uptime.format_report(uptime.summarize([])) == "uptime: no checks in this window"


def test_report_parses_windows_and_survives_junk_lines(tmp_path):
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    assert uptime.parse_since("24h", now) == now - timedelta(hours=24)
    assert uptime.parse_since("7d", now) == now - timedelta(days=7)
    assert uptime.parse_since("2026-11-04T21:00:00-05:00", now) == datetime(2026, 11, 5, 2, tzinfo=timezone.utc)
    f = tmp_path / "u.jsonl"
    f.write_text("junk\n" + json.dumps(rows_at(now, [(True, 0.1, "live")])[0]) + "\n{}\n[1]\n")
    assert len(uptime.read_rows(f)) == 1


def test_the_bin_wrappers_exist_and_are_executable():
    for n in ("app-watchdog", "uptime-check", "uptime-report"):
        p = ROOT / "bin" / n
        assert p.exists() and os.access(p, os.X_OK), n
    assert subprocess.run(["bash", "-n", str(ROOT / "bin" / "app-watchdog")]).returncode == 0
