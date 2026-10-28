"""External uptime check for the public URL, and a summary of the log it writes.

`check` fetches `<public url>/api/health` and appends one JSON line to `ops/uptime.jsonl`:
  {time, url, status, latency_s, mode, ok, path, ip, error}
By default the host name is resolved through a PUBLIC resolver (DNS over HTTPS to 1.1.1.1) and the connection goes to
that address with the right TLS name. On a machine that is itself on the tailnet, MagicDNS answers its own
`*.ts.net` name with its tailnet address, which would test the node against itself and never touch Funnel's public
ingress; resolving publicly avoids that. `--system-dns` turns it off.

`report` summarizes the log for a checkpoint: checks, availability, outages, latency, modes seen.

    python -m etho.app.uptime check [--url URL] [--timeout 10] [--system-dns]
    python -m etho.app.uptime report [--since 24h | ISO time] [--json]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(os.environ.get("ETHO_ROOT") or Path(__file__).resolve().parents[2])
LOG = "ops/uptime.jsonl"
DOH = "https://1.1.1.1/dns-query"


def resolve_public(host: str, timeout: float = 5.0) -> str:
    """First A record for `host` from Cloudflare's DNS-over-HTTPS JSON API (not the system resolver)."""
    req = urllib.request.Request(f"{DOH}?name={host}&type=A", headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        answers = json.load(r).get("Answer", [])
    for a in answers:
        if a.get("type") == 1:
            return a["data"]
    raise OSError(f"no public A record for {host}")


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to a fixed address, validating the certificate for the original host name."""

    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port or 443), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def fetch(url: str, timeout: float = 10.0, public_dns: bool = True) -> dict:
    """GET `url`. Returns {status, latency_s, body, ip, path, error}; never raises."""
    u = urlparse(url)
    out = {"status": None, "latency_s": None, "body": "", "ip": None, "path": "system-dns", "error": None}
    t0 = time.monotonic()
    try:
        target = u.path or "/"
        if u.query:
            target += "?" + u.query
        if u.scheme == "https" and public_dns:
            out["ip"], out["path"] = resolve_public(u.hostname, timeout), "public-dns"
            conn = _Pinned(u.hostname, out["ip"], port=u.port or 443, timeout=timeout)
        elif u.scheme == "https":
            conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
        conn.request("GET", target, headers={"User-Agent": "etho-uptime/1", "Accept": "application/json"})
        r = conn.getresponse()
        out["status"], out["body"] = r.status, r.read(65536).decode("utf-8", "replace")
        conn.close()
    except Exception as e:            # DNS, TLS, timeout, reset: all one kind of failure for an uptime log
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    out["latency_s"] = round(time.monotonic() - t0, 3)
    return out


def check_once(url: str, *, timeout: float = 10.0, public_dns: bool = True, log: Path | None = None,
               now: datetime | None = None) -> dict:
    r = fetch(url.rstrip("/") + "/api/health", timeout, public_dns)
    mode = None
    try:
        body = json.loads(r["body"])
        mode = body.get("mode") if isinstance(body, dict) else None
        healthy = isinstance(body, dict) and body.get("ok") is True
    except ValueError:
        healthy = False
    row = {"time": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "url": url,
           "status": r["status"], "latency_s": r["latency_s"], "mode": mode,
           "ok": bool(r["status"] == 200 and healthy and mode), "path": r["path"], "ip": r["ip"]}
    if r["error"]:
        row["error"] = r["error"]
    elif not row["ok"]:
        row["error"] = f"HTTP {r['status']}, not a healthy /api/health body"
    f = log or ROOT / LOG
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as out:
        out.write(json.dumps(row) + "\n")
    return row


# ---- report ---------------------------------------------------------------------------------------------------
def parse_since(s: str, now: datetime) -> datetime:
    """'24h', '90m', '7d' or an ISO time (a naive one is read as UTC)."""
    s = s.strip()
    if s[-1:] in "mhd" and s[:-1].replace(".", "", 1).isdigit():
        return now - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[s[-1]]: float(s[:-1])})
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def read_rows(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                datetime.fromisoformat(r["time"])
                rows.append(r)
            except (ValueError, KeyError, TypeError):
                continue
    return rows


def summarize(rows: list[dict], since: datetime | None = None) -> dict:
    rows = [r for r in rows if since is None or datetime.fromisoformat(r["time"]) >= since]
    rows.sort(key=lambda r: r["time"])
    n = len(rows)
    if not n:
        return {"checks": 0}
    ok = [r for r in rows if r.get("ok")]
    lat = sorted(r["latency_s"] for r in ok if isinstance(r.get("latency_s"), (int, float)))

    def pct(p):
        return lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None
    outages, start = [], None            # an outage runs from its first failed check to the next good one
    for r in rows:
        t = datetime.fromisoformat(r["time"])
        if not r.get("ok") and start is None:
            start = (t, r.get("error"))
        elif r.get("ok") and start is not None:
            mins = round((t - start[0]).total_seconds() / 60, 1)
            outages.append({"from": start[0].isoformat(), "to": t.isoformat(), "minutes": mins, "error": start[1]})
            start = None
    if start is not None:
        outages.append({"from": start[0].isoformat(), "to": None, "minutes": None, "error": start[1]})
    modes: dict[str, int] = {}
    for r in rows:
        modes[str(r.get("mode"))] = modes.get(str(r.get("mode")), 0) + 1
    return {"checks": n, "ok": len(ok), "availability_pct": round(100 * len(ok) / n, 2),
            "first": rows[0]["time"], "last": rows[-1]["time"], "last_ok": bool(rows[-1].get("ok")),
            "last_mode": rows[-1].get("mode"),
            "latency_s": {"p50": pct(0.5), "p95": pct(0.95), "max": lat[-1] if lat else None},
            "outages": outages, "modes": modes, "url": rows[-1].get("url"),
            "paths": sorted({str(r.get("path")) for r in rows})}


def format_report(s: dict) -> str:
    if not s.get("checks"):
        return "uptime: no checks in this window"
    lat = s["latency_s"]
    out = [f"uptime: {s['ok']}/{s['checks']} checks ok ({s['availability_pct']}%) from {s['first']} to {s['last']}",
           f"  url {s['url']} via {', '.join(s['paths'])}; last check {'ok' if s['last_ok'] else 'FAILED'}, "
           f"mode {s['last_mode']}",
           f"  latency p50 {lat['p50']} s, p95 {lat['p95']} s, max {lat['max']} s; modes seen {s['modes']}"]
    if s["outages"]:
        out.append(f"  {len(s['outages'])} outage(s):")
        for o in s["outages"][-10:]:
            span = "still down" if o["to"] is None else f"{o['minutes']} min"
            out.append(f"    {o['from']} ({span}): {o['error']}")
    else:
        out.append("  no outages")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="etho.app.uptime")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--url", default=os.environ.get("ETHO_PUBLIC_URL", ""))
    c.add_argument("--timeout", type=float, default=10.0)
    c.add_argument("--system-dns", action="store_true", help="resolve with the system resolver (may bypass Funnel)")
    r = sub.add_parser("report")
    r.add_argument("--since", default=None, help="24h, 90m, 7d or an ISO time (default: everything)")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        pu = ROOT / "ops" / "public-url"
        url = a.url or (pu.read_text().strip() if pu.exists() else "")
        if not url:
            print("no public URL: pass --url, set ETHO_PUBLIC_URL, or write it to ops/public-url", file=sys.stderr)
            return 2
        row = check_once(url, timeout=a.timeout, public_dns=not a.system_dns)
        print(json.dumps(row))
        return 0 if row["ok"] else 1
    now = datetime.now(timezone.utc)
    s = summarize(read_rows(ROOT / LOG), parse_since(a.since, now) if a.since else None)
    print(json.dumps(s, indent=1) if a.json else format_report(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
