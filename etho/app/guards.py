"""Abuse and spend guards: per-day app cap with a pre-estimate, and a per-client rate limit."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from .config import AppConfig
from .jobgw import AppLedger


def client_key(host: str | None) -> str:
    """Rate-limit key: the address as uvicorn reports it (X-Forwarded-For is never read here). An IPv6
    client is keyed on its /64, since one subscriber controls a whole /64."""
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return host or "unknown"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class Refused(Exception):
    def __init__(self, message: str, status: int, code: str, retry_after: float | None = None,
                 friendly: str | None = None):
        super().__init__(message)
        self.message, self.status, self.code, self.retry_after = message, status, code, retry_after
        self.friendly = friendly


class SpendGuard:
    """Spend caps for the app, computed from the shared gateway ledger (so they survive restarts), with
    reservations so that concurrent jobs cannot jointly pass them.

    Two caps, both over the app's own ledger rows on `cfg.tag`: `daily_cap_usd` over a rolling 24 hours and
    `total_cap_usd` over all time (None = unlimited). Admitting a job RESERVES its per-job ceiling,
    `min(max(2 x estimate, estimate + $0.50), headroom)`, and the job's gateway never spends past it (see
    `JobGateway`). At any moment (spent + sum over live jobs of ceiling - their spend) therefore stays inside both
    caps, however many jobs are queued or running. The gateway's own caps and tag budgets remain the backstop."""

    def __init__(self, cfg: AppConfig, ledger: AppLedger, now: Callable[[], datetime] | None = None):
        self.cfg, self.ledger = cfg, ledger
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._res: dict[str, float] = {}          # live job id -> reserved ceiling
        self._lock = threading.RLock()

    # ---- numbers ---------------------------------------------------------------------
    def spent_24h(self) -> float:
        return self.ledger.spend_window(self.now(), tag=self.cfg.tag)

    def spent_total(self) -> float:
        return self.ledger.spend_total(self.cfg.tag)

    def outstanding(self, exclude: str | None = None) -> float:
        """What live jobs may still spend: their reserved ceiling minus what they have spent."""
        with self._lock:
            res = {k: v for k, v in self._res.items() if k != exclude}
        return sum(max(0.0, c - self.ledger.job_cost(j)) for j, c in res.items())

    def headroom(self, outstanding_usd: float | None = None) -> tuple[float, str]:
        """(money still available under the tighter cap after what live jobs may still spend, which cap binds)."""
        out = self.outstanding() if outstanding_usd is None else outstanding_usd
        day = self.cfg.daily_cap_usd - self.spent_24h() - out
        if self.cfg.total_cap_usd is None:
            return max(0.0, day), "daily_cap"
        tot = self.cfg.total_cap_usd - self.spent_total() - out
        return (max(0.0, tot), "total_cap") if tot < day else (max(0.0, day), "daily_cap")

    def remaining(self, outstanding_usd: float = 0.0) -> float:
        return self.headroom(outstanding_usd)[0]

    def status(self) -> dict:
        out = self.outstanding()
        day, total = self.spent_24h(), self.spent_total()
        cfg = self.cfg
        return {"tag": cfg.tag, "daily_cap_usd": cfg.daily_cap_usd, "spent_24h_usd": round(day, 4),
                "daily_remaining_usd": round(max(0.0, cfg.daily_cap_usd - day - out), 4),
                "total_cap_usd": cfg.total_cap_usd, "spent_total_usd": round(total, 4),
                "total_remaining_usd": None if cfg.total_cap_usd is None
                else round(max(0.0, cfg.total_cap_usd - total - out), 4),
                "reserved_usd": round(out, 4), "remaining_usd": round(self.headroom(out)[0], 4)}

    # ---- checks ----------------------------------------------------------------------
    def _refuse_if(self, estimate_usd: float, outstanding_usd: float) -> dict:
        cfg = self.cfg
        day, total = self.spent_24h(), self.spent_total()
        info = {"estimate_usd": round(estimate_usd, 4), "spent_today_usd": round(day, 4),
                "spent_total_usd": round(total, 4), "outstanding_usd": round(outstanding_usd, 4),
                "cap_usd": cfg.daily_cap_usd, "total_cap_usd": cfg.total_cap_usd}
        if day + outstanding_usd + estimate_usd > cfg.daily_cap_usd:
            raise Refused(
                f"daily app spend cap: ${day:.2f} spent in the last 24 h + ${outstanding_usd:.2f} committed to "
                f"queued and running jobs + ${estimate_usd:.2f} estimate for this job exceeds the "
                f"${cfg.daily_cap_usd:.2f} cap", 429, "daily_cap")
        if cfg.total_cap_usd is not None and total + outstanding_usd + estimate_usd > cfg.total_cap_usd:
            raise Refused(
                f"total app spend cap: ${total:.2f} spent so far + ${outstanding_usd:.2f} committed + "
                f"${estimate_usd:.2f} estimate for this job exceeds the ${cfg.total_cap_usd:.2f} cap for this demo",
                429, "total_cap")
        return info

    def check(self, estimate_usd: float, outstanding_usd: float = 0.0) -> dict:
        """Refuse (without reserving) if this job could not fit; used for cheap early refusals."""
        return self._refuse_if(estimate_usd, outstanding_usd)

    def ceiling_for(self, estimate_usd: float, headroom_usd: float) -> float:
        cfg = self.cfg
        return min(max(cfg.job_ceiling_mult * estimate_usd, estimate_usd + cfg.job_ceiling_extra_usd), headroom_usd)

    def reserve(self, job_id: str, estimate_usd: float) -> float:
        """Atomically refuse or reserve this job's ceiling. Release it with `release` when the job ends."""
        with self._lock:
            out = self.outstanding()
            self._refuse_if(estimate_usd, out)
            ceiling = self.ceiling_for(estimate_usd, self.headroom(out)[0])
            self._res[job_id] = ceiling
            return ceiling

    def release(self, job_id: str) -> None:
        with self._lock:
            self._res.pop(job_id, None)

    def reserved(self, job_id: str) -> float | None:
        with self._lock:
            return self._res.get(job_id)

    def recheck_at_start(self, job_id: str, estimate_usd: float) -> float:
        """When a queued job starts: other spending may have used the room since it was admitted. Returns the
        ceiling to run under (never more than what was reserved), or raises Refused."""
        with self._lock:
            out = self.outstanding(exclude=job_id)
            self._refuse_if(estimate_usd, out)
            reserved = self._res.get(job_id)
            room = self.headroom(out)[0]
            self._res[job_id] = ceiling = min(room, reserved) if reserved is not None else \
                self.ceiling_for(estimate_usd, room)
            return ceiling


class RateLimiter:
    """Sliding window, in memory (one process), keyed by client address."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window_s:
            q.popleft()

    def _prune(self, now: float) -> None:
        if len(self._hits) < 256:
            return
        for k in list(self._hits):
            self._trim(self._hits[k], now)
            if not self._hits[k]:
                del self._hits[k]

    def check(self, client: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune(now)
            q = self._hits.get(client)
            if q is None:
                return
            self._trim(q, now)
            if not q:
                del self._hits[client]
                return
            if len(q) >= self.limit:
                wait = self.window_s - (now - q[0])
                raise Refused(f"rate limit: {self.limit} jobs per {self.window_s / 3600:g} h per client; "
                              f"try again in {wait / 60:.0f} min", 429, "rate_limited", retry_after=wait)

    def record(self, client: str) -> None:
        with self._lock:
            self._prune(self.clock())
            self._hits[client].append(self.clock())

    def release(self, client: str) -> None:
        """Undo the latest `record` (job rejected after the rate check)."""
        with self._lock:
            q = self._hits.get(client)
            if q:
                q.pop()
                if not q:
                    del self._hits[client]
