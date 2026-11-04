"""Access code for live use, a short-lived signed cookie after entry, and brute-force throttling.

The code lives in a file named by `cfg.access_code_file`. It is read here and nowhere else: never rendered into a
page or script, never sent to a client, never written to a log, never kept in the environment. Entry is a POST of the
code; a correct code gets an HttpOnly, SameSite=Strict cookie that expires after `cfg.cookie_ttl_s`.

* The comparison is constant time: both sides are hashed with SHA-256 first, so neither the length nor any prefix of
  the code is observable through timing, then compared with `hmac.compare_digest`.
* The cookie is `<expiry>.<nonce>.<mac>`. The MAC key is random per process (a restart signs everyone out, which is
  fine for a two-hour cookie) and covers a fingerprint of the current code, so rotating the code invalidates every
  cookie at once. The MAC key is not derived from the code, so a stolen cookie cannot be used to guess it offline.
* Wrong entries are throttled per client (a sliding window: 429 and a server-side delay) and counted in total (a
  warning in the log and a delay past the limit).
  Only wrong codes are throttled; the right code is never refused, so no one can lock a real visitor out.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from .config import MIN_ACCESS_CODE_LEN, AppConfig
from .guards import Refused

log = logging.getLogger("etho.app.access")
COOKIE = "etho_live"
MAX_CODE_LEN = 256


def _h(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8", "replace")).digest()


class AccessControl:
    def __init__(self, cfg: AppConfig, now: Callable[[], datetime] | None = None):
        self.cfg = cfg
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._key = os.urandom(32)
        self._lock = threading.Lock()
        self._file_sig: tuple | None = None
        self._code: str | None = None
        self._problem: str | None = None
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()

    # ---- the code ---------------------------------------------------------------------
    def _load(self) -> None:
        """Read the code file if it changed (call with the lock held)."""
        path = self.cfg.access_code_file
        if path is None:
            self._code, self._problem, self._file_sig = None, "no access code file is configured", None
            return
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
            if sig == self._file_sig:
                return
            text = path.read_text().strip()
        except OSError:
            self._code, self._problem, self._file_sig = None, "the access code file cannot be read", None
            return
        self._file_sig = sig
        if len(text) < MIN_ACCESS_CODE_LEN:
            self._code, self._problem = None, f"the access code is shorter than {MIN_ACCESS_CODE_LEN} characters"
        else:
            self._code, self._problem = text, None

    def code(self) -> str | None:
        with self._lock:
            self._load()
            return self._code

    @property
    def required(self) -> bool:
        """Live use needs a code on a public deployment, or wherever a code file is configured. Never in open mode
        (ETHO_APP_OPEN), where uploads need no code and no login."""
        if self.cfg.open:
            return False
        return self.cfg.public or self.cfg.access_code_file is not None

    def problem(self) -> str | None:
        """Why live use cannot be unlocked (None = it can, or no code is required)."""
        if not self.required:
            return None
        with self._lock:
            self._load()
            return self._problem

    def _fingerprint(self, code: str) -> str:
        return hmac.new(self._key, b"fp|" + code.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:16]

    # ---- cookie -----------------------------------------------------------------------
    def _mac(self, exp: str, nonce: str, code: str) -> str:
        return hmac.new(self._key, f"v1|{exp}|{nonce}|{self._fingerprint(code)}".encode(), hashlib.sha256).hexdigest()

    def _issue(self, code: str) -> str:
        exp, nonce = str(int(self.now().timestamp() + self.cfg.cookie_ttl_s)), os.urandom(8).hex()
        return f"{exp}.{nonce}.{self._mac(exp, nonce, code)}"

    def valid(self, cookie: str | None) -> bool:
        """True when no code is required, or the cookie is unexpired and signed for the current code."""
        return (not self.required) or self.valid_cookie(cookie)

    def valid_cookie(self, cookie: str | None) -> bool:
        """True only for an unexpired cookie signed for the current code, whether or not a code is required. The
        operator's view (health details) uses this, so open mode does not make every visitor an operator."""
        code = self.code()
        if code is None or not cookie:
            return False
        parts = cookie.split(".")
        if len(parts) != 3 or not (parts[0].isascii() and parts[0].isdigit()):
            return False
        exp, nonce, mac = parts
        if int(exp) <= self.now().timestamp():
            return False
        return hmac.compare_digest(mac, self._mac(exp, nonce, code))

    # ---- entry ------------------------------------------------------------------------
    @staticmethod
    def _recent(q: deque, now: float, window: float) -> None:
        while q and now - q[0] >= window:
            q.popleft()

    def attempt(self, client: str, candidate: object) -> str:
        """Check a code. Returns a cookie value, or raises Refused (401 wrong code, 429 too many wrong codes from this
        client, 503 no code set). Throttling applies to WRONG codes only: the right code always works, so nobody can
        lock a real visitor out by guessing. A throttled wrong guess answers 429 and the server also delays it
        (`delay_for`), which is what slows a guesser; the code has about 140 bits, so the throttle is a courtesy."""
        code = self.code()
        if code is None:
            raise Refused("live use is not available: no valid access code is configured on the server", 503,
                          "access_unavailable")
        now = self.now().timestamp()
        cfg = self.cfg
        ok = isinstance(candidate, str) and len(candidate) <= MAX_CODE_LEN and hmac.compare_digest(
            _h(candidate), _h(code))
        with self._lock:
            if ok:
                self._fails.pop(client, None)
                return self._issue(code)
            q = self._fails[client]
            self._recent(q, now, cfg.access_window_s)
            q.append(now)
            self._recent(self._global, now, cfg.access_global_window_s)
            self._global.append(now)
            if len(self._fails) > 2000:           # forget idle clients rather than grow without bound
                for k in list(self._fails):
                    self._recent(self._fails[k], now, cfg.access_window_s)
                    if not self._fails[k]:
                        del self._fails[k]
            if len(self._global) == cfg.access_global_failures:
                log.warning("access code: %d wrong codes in the last hour from all clients", len(self._global))
            if len(q) > cfg.access_max_failures:
                wait = cfg.access_window_s - (now - q[0])
                raise Refused(f"too many wrong codes; wait {max(1, int(wait / 60) + 1)} min before trying again", 429,
                              "access_throttled", retry_after=wait)
        raise Refused("that access code is not valid", 401, "wrong_code")

    def delay_for(self, refused: Refused) -> float:
        """Seconds the server should wait before answering a refused entry (0 for anything but a wrong guess that
        was throttled, per client or over the whole server)."""
        if refused.code == "access_throttled":
            return self.cfg.access_throttle_delay_s
        if refused.code == "wrong_code" and len(self._global) >= self.cfg.access_global_failures:
            return self.cfg.access_throttle_delay_s
        return 0.0


def shared_bucket(client: str) -> bool:
    """True when the client key is a loopback or Tailscale (CGNAT 100.64.0.0/10) address: behind a proxy that does not
    pass the visitor's address, every visitor then shares one bucket for the per-client limits."""
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")
