"""A gateway that serves recorded responses and nothing else.

It has the surface `etho.app.jobgw.JobGateway` uses (`chat`, `responses`, `sam`, `upload_file`, `delete_file`) and is
handed to the app through the same seam the tests use for their fake gateway, `create_app(gateway_factory=...)`.
It never builds `Gateway` or an HTTP client and reads no key (it borrows only the two result dataclasses), so
nothing in it can reach a provider. A call with no recording raises `ReplayMiss`; there is no fallback.

What comes back mirrors the live gateway's result objects, with the cost the call was billed when it was recorded
(`cost_usd`) and `latency_s` 0.0 (nothing waited on a network). `replayed=True` marks them.
"""

from __future__ import annotations

import gzip
import hashlib
import threading
from collections import defaultdict
from pathlib import Path

from etho.gateway.gateway import LLMResult, SamResult

from . import keys
from .index import ReplayIndex, ReplayMiss


class ReplayGateway:
    replay = True

    def __init__(self, index: ReplayIndex, scratch_dir: str | Path):
        self.index = index
        self.scratch = Path(scratch_dir)
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._seen: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self.served: list[dict] = []          # one record per replayed call: key, occurrence, method, caller
        self.misses: list[ReplayMiss] = []    # the app's stages may catch an exception; the demo checks this list

    # ---- lookup ------------------------------------------------------------------------------
    def _fetch(self, desc: dict, caller: str | None) -> dict:
        """The recorded response, or ReplayMiss. A miss is also remembered, and after the first one every later
        call fails too: some stages of the app catch an exception and carry on without the model (tier 1 keeps the
        rule labels), which a replay must never do silently. The demo checks `misses` when the job ends."""
        key = keys.key_of(desc)
        with self._lock:
            if self.misses:
                raise ReplayMiss("replay already failed on an earlier call; no further calls are served.\n"
                                 + str(self.misses[0]))
            occ = self._seen[key]
            self._seen[key] += 1
        e = self.index.get(key, occ)
        if e is None:
            miss = self.index.miss(desc, key, occ, caller)
            with self._lock:
                self.misses.append(miss)
            raise miss
        with self._lock:
            self.served.append({"key": key, "occ": occ, "method": desc["method"], "caller": desc["caller"]})
        return e["response"]

    # ---- the gateway surface -----------------------------------------------------------------
    def chat(self, provider, model, messages, *, effort, tag=None, caller=None, **kw) -> LLMResult:
        r = self._fetch(keys.chat_desc(provider, model, messages, effort, caller, kw), caller)
        return self._llm(r)

    def responses(self, model, input, *, effort, tag=None, caller=None, **kw) -> LLMResult:  # noqa: A002
        r = self._fetch(keys.responses_desc(model, input, effort, caller, kw), caller)
        return self._llm(r)

    @staticmethod
    def _llm(r: dict) -> LLMResult:
        res = LLMResult(r["text"], r["usage"], float(r["cost_usd"]), False, 0.0,
                        int(r.get("reasoning_tokens") or 0), {})
        res.replayed = True      # type: ignore[attr-defined]
        return res

    def sam(self, phrase, *, tag=None, caller=None, on_line=None, **kw) -> SamResult:
        r = self._fetch(keys.sam_desc(phrase, caller, kw), caller)
        h = hashlib.sha256(r["text"].encode()).hexdigest()[:24]
        path = self.scratch / f"sam-{h}.txt.gz"
        if not path.exists():
            tmp = path.with_suffix(f".tmp{threading.get_ident()}")
            with gzip.open(tmp, "wt") as f:
                f.write(r["text"])
            tmp.replace(path)
        res = SamResult(path, r.get("usage") or {}, float(r["cost_usd"]), False, 0.0, int(r.get("frames_billed") or 0))
        if on_line:
            for line in res.lines():
                on_line(line)
        return res

    def upload_file(self, path, *, tag=None, public_data=False, purpose="user_data", caller=None, **kw) -> str:
        duration_s, n_frames = probe(Path(path))
        r = self._fetch(keys.upload_desc(caller, purpose, duration_s, n_frames), caller)
        return r["id"]

    def delete_file(self, file_id, *, tag=None, caller=None, request_key=None) -> bool:
        return True       # nothing was uploaded


def probe(path: Path) -> tuple[float, int]:
    """(duration in seconds, frame count) of a clip, from the container: what an upload is keyed on."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
    finally:
        cap.release()
    return (n / fps if fps else 0.0), n


def factory(index: ReplayIndex, scratch_dir: str | Path):
    """`create_app(gateway_factory=...)` wants a zero-argument callable; the app calls it once and reuses the result."""
    gw = ReplayGateway(index, scratch_dir)
    return lambda: gw


__all__ = ["ReplayGateway", "ReplayMiss", "factory", "probe"]
