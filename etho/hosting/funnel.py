"""Funnel keepalive: re-enable `tailscale funnel --bg 8000` if the public Funnel is not serving the app.

Both Oct 28-29 outages were a manual `tailscale funnel reset`. This runs from cron every 5 minutes (no sudo to read the
status; `sudo -n` only to re-enable). `ops/funnel-off` is the operator's "stay offline" marker and wins over everything.
At most one re-enable attempt per 10 minutes, state in ops/.funnel-state.json. Rows go to ops/funnel.jsonl; operator
notifications go to ops/notify.jsonl.

`tailscale funnel status --json` shape on this machine (Funnel on):
  {"TCP": {"443": {"HTTPS": true}},
   "Web": {"<host>.<tailnet>.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}},
   "AllowFunnel": {"<host>.<tailnet>.ts.net:443": true}}
With Funnel off there is no Web/AllowFunnel entry (empty or missing keys).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from etho.hosting.notify import notify

ROOT = Path(os.environ.get("ETHO_ROOT") or Path(__file__).resolve().parents[2])
PORT = 8000
MIN_GAP_S = 600
SUDO_CMD = ["sudo", "-n", "/usr/bin/tailscale", "funnel", "--bg", str(PORT)]
_BACKENDS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


def is_serving(status: object) -> bool:
    """True if some host:port is Funnel-enabled and its "/" handler proxies to the local app."""
    if not isinstance(status, dict):
        return False
    allow, web = status.get("AllowFunnel"), status.get("Web")
    if not isinstance(allow, dict) or not isinstance(web, dict):
        return False
    for hp, on in allow.items():
        site = web.get(hp)
        if on is not True or not isinstance(site, dict):
            continue
        handlers = site.get("Handlers")
        h = handlers.get("/") if isinstance(handlers, dict) else None
        proxy = h.get("Proxy") if isinstance(h, dict) else None
        if isinstance(proxy, str) and proxy.rstrip("/") in _BACKENDS:
            return True
    return False


def read_status(run=subprocess.run) -> tuple[object, str | None]:
    try:
        p = run(["tailscale", "funnel", "status", "--json"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"{type(e).__name__}: {e}"[:120]
    if p.returncode != 0:
        return None, f"status exit {p.returncode}: {(p.stderr or '').strip()[:100]}"
    try:
        return json.loads(p.stdout or "{}"), None
    except ValueError:
        return None, "status output is not JSON"


def _log(root: Path, now: datetime, **kw) -> None:
    with (root / "ops" / "funnel.jsonl").open("a") as f:
        f.write(json.dumps({"time": now.isoformat(timespec="seconds"), **kw}) + "\n")


def run_once(root: Path = ROOT, *, run=subprocess.run, now: datetime | None = None) -> str:
    """Returns: offline-marker, serving, rate-limited, status-error, reenabled or reenable-failed."""
    now = now or datetime.now(timezone.utc)
    ops = root / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    if (ops / "funnel-off").exists():
        return "offline-marker"
    status, err = read_status(run)
    if err is not None:      # cannot tell: do not re-enable blindly, leave a trace
        _log(root, now, event="status-error", error=err)
        return "status-error"
    if is_serving(status):
        return "serving"
    state_f = ops / ".funnel-state.json"
    try:
        last = datetime.fromisoformat(json.loads(state_f.read_text())["last_attempt"])
    except (OSError, ValueError, KeyError, TypeError):
        last = None
    if last is not None and (now - last).total_seconds() < MIN_GAP_S:
        return "rate-limited"
    state_f.write_text(json.dumps({"last_attempt": now.isoformat(timespec="seconds")}))
    try:
        p = run(SUDO_CMD, capture_output=True, text=True, timeout=60)
        rc, out = p.returncode, ((p.stderr or "") + (p.stdout or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        rc, out = 1, f"{type(e).__name__}: {e}"
    if rc == 0:
        _log(root, now, event="reenabled", ok=True)
        notify(ops / "notify.jsonl", "funnel", "reenabled", "Funnel was off; re-enabled", now)
        return "reenabled"
    detail = " ".join(out.split())[:90]
    _log(root, now, event="reenable-failed", ok=False, rc=rc, error=detail)
    notify(ops / "notify.jsonl", "funnel", "down", f"Funnel off and could not re-enable: rc {rc} {detail}", now)
    return "reenable-failed"


def main() -> int:
    r = run_once(Path(os.environ.get("ETHO_ROOT") or ROOT))
    print(r)
    return 1 if r in ("reenable-failed", "status-error") else 0


if __name__ == "__main__":
    sys.exit(main())
