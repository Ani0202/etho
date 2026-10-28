"""Abuse and spend guards: per-day app cap with a pre-estimate, and a per-client rate limit."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from .config import AppConfig
from .jobgw import AppLedger


class Refused(Exception):
    def __init__(self, message: str, status: int, code: str, retry_after: float | None = None):
        super().__init__(message)
        self.message, self.status, self.code, self.retry_after = message, status, code, retry_after


class SpendGuard:
    """Refuses a job when (app spend today) + (what already-accepted jobs may still spend) + (this
    job's estimate) would exceed the daily cap. The gateway's own caps stay the real backstop."""

    def __init__(self, cfg: AppConfig, ledger: AppLedger, now: Callable[[], datetime] | None = None):
        self.cfg, self.ledger = cfg, ledger
        self.now = now or (lambda: datetime.now(timezone.utc))

    def remaining(self, outstanding_usd: float = 0.0) -> float:
        return max(0.0, self.cfg.daily_cap_usd - self.ledger.spend_today(self.now()) - outstanding_usd)

    def check(self, estimate_usd: float, outstanding_usd: float = 0.0) -> dict:
        spent = self.ledger.spend_today(self.now())
        info = {"estimate_usd": round(estimate_usd, 4), "spent_today_usd": round(spent, 4),
                "outstanding_usd": round(outstanding_usd, 4), "cap_usd": self.cfg.daily_cap_usd}
        if spent + outstanding_usd + estimate_usd > self.cfg.daily_cap_usd:
            raise Refused(
                f"daily app spend cap: ${spent:.2f} spent today + ${outstanding_usd:.2f} committed to queued "
                f"jobs + ${estimate_usd:.2f} estimate for this job exceeds the ${self.cfg.daily_cap_usd:.2f} cap",
                429, "daily_cap")
        return info


class RateLimiter:
    """Sliding window, in memory (one process), keyed by client address."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window_s:
            q.popleft()

    def check(self, client: str) -> None:
        with self._lock:
            now = self.clock()
            q = self._hits[client]
            self._trim(q, now)
            if len(q) >= self.limit:
                wait = self.window_s - (now - q[0])
                raise Refused(f"rate limit: {self.limit} jobs per {self.window_s / 3600:g} h per client; "
                              f"try again in {wait / 60:.0f} min", 429, "rate_limited", retry_after=wait)

    def record(self, client: str) -> None:
        with self._lock:
            self._hits[client].append(self.clock())

    def release(self, client: str) -> None:
        """Undo the latest `record` (job rejected after the rate check)."""
        with self._lock:
            if self._hits[client]:
                self._hits[client].pop()
