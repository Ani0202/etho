"""Append-only spend ledger (ops/spend.jsonl) with cross-process cap enforcement.

Every model call reserves its worst-case cost before it is sent (an "in-flight" file under
ops/inflight/), then settles with the actual cost from the provider's usage. Cap checks count
settled spend plus all live reservations, under an exclusive file lock, so parallel workers can't
jointly overshoot a cap. A reservation whose process died without settling may still have been
billed, so it is booked at its estimate ("abandoned"), never silently dropped.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import CapExceeded
from .prices import ALERT_FRACTIONS, HARD_CAPS, META_TAG_BUDGETS


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Ledger:
    def __init__(self, ops_dir: Path, caps: dict[str, float] | None = None,
                 tag_budgets: dict[str, float] | None = None):
        self.ops_dir = ops_dir
        self.path = ops_dir / "spend.jsonl"
        self.alerts_path = ops_dir / "alerts.jsonl"
        self.inflight_dir = ops_dir / "inflight"
        self.lock_path = ops_dir / ".spend.lock"
        self.caps = dict(HARD_CAPS if caps is None else caps)
        self.tag_budgets = dict(META_TAG_BUDGETS if tag_budgets is None else tag_budgets)
        self.inflight_dir.mkdir(parents=True, exist_ok=True)

    # ---- reading -------------------------------------------------------------------------
    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def totals(self) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
        """Settled spend by provider and by (provider, tag)."""
        by_provider: dict[str, float] = defaultdict(float)
        by_tag: dict[tuple[str, str], float] = defaultdict(float)
        for r in self.rows():
            c = float(r.get("cost_usd", 0.0))
            by_provider[r["provider"]] += c
            by_tag[(r["provider"], r.get("tag", "?"))] += c
        return by_provider, by_tag

    def _inflight(self) -> list[dict]:
        """Live reservations. A file that is unreadable, not JSON, or lacks the fields a reservation has (a half
        written or tampered file) is skipped and logged, never raised on: it must not stop every other call."""
        out = []
        for p in self.inflight_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                ok = (isinstance(d, dict) and isinstance(d.get("id"), str) and "/" not in d["id"]
                      and isinstance(d.get("provider"), str)
                      and isinstance(d.get("tag"), str) and isinstance(d.get("pid"), int)
                      and not isinstance(d.get("pid"), bool) and isinstance(d.get("est"), (int, float))
                      and not isinstance(d.get("est"), bool) and 0 <= d["est"] < float("inf"))
            except (OSError, ValueError, RecursionError):
                ok = False
            if ok:
                out.append(d)
            else:
                print(f"gateway: ignoring malformed reservation file {p.name}", file=sys.stderr)
        return out

    def _reap_abandoned(self) -> None:
        """Book reservations of dead processes at their estimate (call with the lock held)."""
        for d in self._inflight():
            if _pid_alive(int(d.get("pid", -1))):
                continue
            row = {"time": utcnow(), "provider": d["provider"], "model": "?", "tag": d["tag"],
                   "caller": "ledger:abandoned-reservation", "cost_usd": round(float(d["est"]), 6),
                   "cache_hit": False, "abandoned_reservation": d["id"], "maybe_billed": True}
            with self.path.open("a") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            (self.inflight_dir / f"{d['id']}.json").unlink(missing_ok=True)

    @contextmanager
    def _locked(self):
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    # ---- reserve / settle -----------------------------------------------------------------
    def reserve(self, provider: str, tag: str, est_cost: float) -> str:
        if not (est_cost >= 0 and est_cost < float("inf")):
            raise CapExceeded(f"invalid cost estimate {est_cost!r}")
        with self._locked():
            self._reap_abandoned()
            by_provider, by_tag = self.totals()
            inflight = self._inflight()
            pending_p = sum(d["est"] for d in inflight if d["provider"] == provider)
            pending_t = sum(d["est"] for d in inflight
                            if d["provider"] == provider and d["tag"] == tag)
            cap = self.caps.get(provider)
            if cap is None:
                raise CapExceeded(f"no hard cap configured for provider {provider!r}")
            committed = by_provider.get(provider, 0.0) + pending_p
            if committed + est_cost > cap:
                raise CapExceeded(
                    f"{provider}: spent+pending ${committed:.4f} + this call (est ${est_cost:.4f}) "
                    f"would exceed the hard cap ${cap:.2f}")
            if provider == "meta":
                budget = self.tag_budgets[tag]
                tag_committed = by_tag.get((provider, tag), 0.0) + pending_t
                if tag_committed + est_cost > budget:
                    raise CapExceeded(
                        f"meta tag {tag!r}: spent+pending ${tag_committed:.4f} + est ${est_cost:.4f} "
                        f"would exceed its ${budget:.2f} budget (moving money between tags is an ADR)")
            rid = uuid.uuid4().hex
            (self.inflight_dir / f"{rid}.json").write_text(json.dumps(
                {"id": rid, "provider": provider, "tag": tag, "est": est_cost,
                 "ts": time.time(), "pid": os.getpid()}))
            return rid

    def settle(self, reservation: str | None, row: dict) -> None:
        row = {"time": utcnow(), **row}
        with self._locked():
            before_p, before_t = self.totals()
            with self.path.open("a") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            if reservation:
                (self.inflight_dir / f"{reservation}.json").unlink(missing_ok=True)
            self._check_alerts(row, before_p, before_t)

    def _check_alerts(self, row: dict, before_p: dict, before_t: dict) -> None:
        cost = float(row.get("cost_usd", 0.0))
        if cost <= 0:
            return
        provider, tag = row["provider"], row.get("tag", "?")
        checks = [(f"provider:{provider}", before_p.get(provider, 0.0), self.caps.get(provider),
                   ALERT_FRACTIONS)]
        if provider == "meta" and tag in self.tag_budgets:
            checks.append((f"meta-tag:{tag}", before_t.get((provider, tag), 0.0),
                           self.tag_budgets[tag], (0.8,)))
        for name, before, limit, fracs in checks:
            if not limit:
                continue
            for frac in fracs:
                if before < frac * limit <= before + cost:
                    alert = {"time": utcnow(), "what": name, "crossed": frac,
                             "spent": round(before + cost, 4), "limit": limit,
                             "action": "ESCALATE to Aniket (CLAUDE.md decision protocol)"}
                    with self.alerts_path.open("a") as f:
                        f.write(json.dumps(alert) + "\n")
                    print(f"ETHO SPEND ALERT: {name} crossed {int(frac * 100)}% "
                          f"(${before + cost:.2f} of ${limit:.2f}). Escalate.", file=sys.stderr)
