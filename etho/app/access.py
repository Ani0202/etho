"""Access code for live use, a short-lived signed cookie after entry, and brute-force throttling.

The code lives in a file named by `cfg.access_code_file`. It is read here and nowhere else: never rendered into a
page or script, never sent to a client, never written to a log, never kept in the environment. Entry is a POST of the
code; a correct code gets an HttpOnly, SameSite=Strict cookie that expires after `cfg.cookie_ttl_s`.

* The comparison is constant time: both sides are hashed with SHA-256 first, so neither the length nor any prefix of
  the code is observable through timing, then compared with `hmac.compare_digest`.
* The cookie is `<expiry>.<nonce>.<mac>`. The MAC key is random per process (a restart signs everyone out, which is
  fine for a two-hour cookie) and covers a fingerprint of the current code, so rotating the code invalidates every
  cookie at once. The MAC key is not derived from the code, so a stolen cookie cannot be used to guess it offline.
* Failed entries are throttled per client (a sliding window) and in total (so a spread-out guesser is slowed too).
  A locked-out client is refused even with the right code, so a lock-out reveals nothing. The cost of the global limit
  is that a flood of bad guesses can keep a real visitor from entering for up to an hour; browsing is unaffected.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from .config import MIN_ACCESS_CODE_LEN, AppConfig
from .guards import Refused

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
        """Live use needs a code on a public deployment, or wherever a code file is configured."""
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
        if not self.required:
            return True
        code = self.code()
        if code is None or not cookie:
            return False
        parts = cookie.split(".")
        if len(parts) != 3 or not parts[0].isdigit():
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

    def _locked_out(self, client: str, now: float) -> float | None:
        """Seconds until this client may try again, or None."""
        cfg = self.cfg
        q = self._fails.get(client)
        if q is not None:
            self._recent(q, now, cfg.access_window_s)
            if not q:
                del self._fails[client]
            elif len(q) >= cfg.access_max_failures:
                return cfg.access_window_s - (now - q[0])
        self._recent(self._global, now, cfg.access_global_window_s)
        if len(self._global) >= cfg.access_global_failures:
            return cfg.access_global_window_s - (now - self._global[0])
        return None

    def attempt(self, client: str, candidate: object) -> str:
        """Check a code. Returns a cookie value, or raises Refused (401 wrong code, 429 throttled, 503 no code set)."""
        code = self.code()
        if code is None:
            raise Refused("live use is not available: no valid access code is configured on the server", 503,
                          "access_unavailable")
        now = self.now().timestamp()
        with self._lock:
            wait = self._locked_out(client, now)
            if wait is not None:
                raise Refused(f"too many wrong codes; try again in {max(1, int(wait / 60) + 1)} min", 429,
                              "access_throttled", retry_after=wait)
            ok = isinstance(candidate, str) and len(candidate) <= MAX_CODE_LEN and hmac.compare_digest(
                _h(candidate), _h(code))
            if not ok:
                self._fails[client].append(now)
                self._global.append(now)
                if len(self._fails) > 2000:           # forget idle clients rather than grow without bound
                    for k in list(self._fails):
                        self._recent(self._fails[k], now, self.cfg.access_window_s)
                        if not self._fails[k]:
                            del self._fails[k]
                raise Refused("that access code is not valid", 401, "wrong_code")
            self._fails.pop(client, None)
        return self._issue(code)
