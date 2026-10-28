"""Whether the app may do work that costs money right now, and why not.

Mode, in order of precedence:
  killed     the kill-switch file exists: new work is refused, queued jobs are cancelled and the running job stops
             at its next model call. The showcase stays up.
  expired    now is past `cfg.live_until` (default on a public deployment: 2026-11-10 23:59:59 ET): same as killed.
  read-only  any of: ETHO_APP_READ_ONLY, the read-only flag file, (public only) no usable access code, (public only)
             the rule sandbox is not bubblewrap. New work is refused; jobs already admitted finish.
  live       none of the above.
Both files are looked at on every call, so an operator can flip them without a restart. Everything fails closed:
an unreadable state means refuse.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

from .access import AccessControl
from .config import AppConfig
from .guards import Refused

ISO_TTL_S = 30.0


class LiveState:
    def __init__(self, cfg: AppConfig, access: AccessControl, now: Callable[[], datetime] | None = None,
                 isolation: Callable[[], dict] | None = None):
        self.cfg, self.access = cfg, access
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._isolation = isolation
        self._iso: tuple[float, dict] | None = None

    def sandbox(self) -> dict:
        if self._isolation is None:
            return {"mode": "unknown", "detail": "not checked"}
        t = time.monotonic()
        if self._iso is None or t - self._iso[0] > ISO_TTL_S:
            try:
                st = dict(self._isolation())
            except Exception as e:   # SandboxUnavailable or anything else: not isolated
                st = {"mode": "unavailable", "detail": str(e)[:200]}
            self._iso = (t, st)
        return self._iso[1]

    @staticmethod
    def _exists(p) -> bool:
        try:
            return p.exists()
        except OSError:
            return True      # cannot tell: treat the switch as set

    def killed(self) -> bool:
        return self._exists(self.cfg.kill_path)

    def expired(self) -> bool:
        lu = self.cfg.live_until
        return lu is not None and self.now() > lu

    def stop_work(self) -> bool:
        """Kill switch or expiry: running and queued jobs must stop, not just new ones."""
        return self.killed() or self.expired()

    def snapshot(self) -> dict:
        cfg = self.cfg
        lu = cfg.live_until
        base = {"live_until": lu.isoformat() if lu else None, "access_required": self.access.required}
        if self.killed():
            return {**base, "mode": "killed", "reasons": ["the kill switch is on"]}
        if self.expired():
            return {**base, "mode": "expired", "reasons": [f"the live window ended at {lu.isoformat()}"]}
        reasons = []
        if cfg.read_only:
            reasons.append("ETHO_APP_READ_ONLY is set")
        if self._exists(cfg.readonly_path):
            reasons.append("the read-only flag file is present")
        if cfg.public:
            problem = self.access.problem()
            if problem:
                reasons.append(problem)
            iso = self.sandbox()
            if iso.get("mode") != "bwrap":
                reasons.append(f"rule sandbox isolation is {iso.get('mode')} "
                               "(bubblewrap is required for public live use)")
        return {**base, "mode": "read-only" if reasons else "live", "reasons": reasons}

    def refusal(self) -> Refused | None:
        """None when live work may start; otherwise the refusal to send."""
        m = self.snapshot()["mode"]
        if m == "live":
            return None
        if m == "killed":
            return Refused("live use has been switched off by the operator; the precomputed showcases are still "
                           "open", 503, "killed")
        if m == "expired":
            return Refused("the live window for this demo has ended; the precomputed showcases are still open", 503,
                           "expired")
        return Refused("this site is read-only: the precomputed showcases are open, uploads and revisions are "
                       "disabled", 403, "read_only")
