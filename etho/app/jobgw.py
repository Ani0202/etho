"""Per-job view of the gateway and of the spend ledger.

`JobGateway` wraps the one real `Gateway`: it forces the app's tag on every call (so the pipeline
modules' own hard-coded tags never leak into app spend), prefixes the `caller` string with
`job:<id>|` so each ledger row is attributable to a job, and keeps a per-call audit record.
It adds no provider access of its own: every call still goes through `Gateway`.

`AppLedger` tails `ops/spend.jsonl` incrementally and aggregates the rows whose caller starts
with `job:` by job id and by UTC day.
"""

from __future__ import annotations

import inspect
import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from etho.gateway import Gateway

JOB_PREFIX = "job:"
MAX_TEXT = 6000


class JobBudgetExceeded(Exception):
    """The job reached its own spend ceiling; no further model call is made."""


class JobCancelled(Exception):
    """The user cancelled the job; no further model call is made."""


def caller_for(job_id: str, caller: str) -> str:
    return f"{JOB_PREFIX}{job_id}|{caller}"


def job_of_caller(caller: str) -> str | None:
    if caller.startswith(JOB_PREFIX) and "|" in caller:
        return caller[len(JOB_PREFIX):caller.index("|")]
    return None


STAGE_OF_CALLER = (("etho.perception.planner", "plan"), ("etho.tracking.pipeline", "track"),
                   ("etho.rules.synthesize", "synthesize"), ("etho.rules.revise", "revise"),
                   ("etho.adjudication.tier1", "tier1"), ("etho.report", "report"))


def stage_of_caller(caller: str) -> str:
    body = caller.split("|", 1)[-1]
    for prefix, stage in STAGE_OF_CALLER:
        if body.startswith(prefix):
            return stage
    return "other"


def _text_of(messages: Any) -> str:
    """Prompt text for the audit trail; images and videos become placeholders (never base64)."""
    parts: list[str] = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(f"[{m.get('role')}] {c}")
        elif isinstance(c, list):
            for p in c:
                t = p.get("type")
                if t == "text":
                    parts.append(f"[{m.get('role')}] {p.get('text', '')}")
                elif t in ("image_url", "input_image"):
                    parts.append("[image]")
                elif t in ("video_url", "input_video"):
                    parts.append("[video]")
    return "\n".join(parts)


def _clip(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + f"... [{len(s) - MAX_TEXT} more chars]"


class JobGateway:
    """Drop-in for `Gateway` as far as the pipeline modules use it (chat, sam, responses, upload_file)."""

    def __init__(self, gw: Gateway, *, job_id: str, tag: str, calls_path: Path | None = None,
                 on_cost=None, max_cost_usd: float | None = None, cancelled=None):
        self._gw, self.job_id, self.tag = gw, job_id, tag
        self.max_cost_usd = max_cost_usd      # per-job ceiling, checked before every call
        self._cancelled = cancelled
        self.stage = "init"
        self.stage_cost: dict[str, float] = defaultdict(float)
        self.total_cost = 0.0
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self._calls_path = calls_path
        self._on_cost = on_cost

    # -- helpers -----------------------------------------------------------------------
    def _guard(self) -> None:
        if self._cancelled and self._cancelled():
            raise JobCancelled("cancelled by the user")
        if self.max_cost_usd is not None and self.total_cost >= self.max_cost_usd:
            raise JobBudgetExceeded(f"this job reached its spend ceiling of ${self.max_cost_usd:.2f} "
                                    f"(spent ${self.total_cost:.2f}); no further model calls")

    def _caller(self, caller: str | None) -> str:
        if caller is None:
            caller = "unknown"
            for fr in inspect.stack()[2:]:
                mod = fr.frame.f_globals.get("__name__", "")
                if not mod.startswith(("etho.gateway", "etho.app.jobgw")):
                    caller = f"{mod}:{fr.function}"
                    break
        return caller_for(self.job_id, caller)

    def _record(self, kind: str, caller: str, stage: str, cost: float, rec: dict) -> None:
        with self._lock:
            self.total_cost += cost
            self.stage_cost[stage] += cost
            rec = {"n": len(self.calls), "kind": kind, "stage": stage, "caller": caller, "tag": self.tag,
                   "cost_usd": round(cost, 6), **rec}
            self.calls.append(rec)
            if self._calls_path is not None:
                self._calls_path.parent.mkdir(parents=True, exist_ok=True)
                with self._calls_path.open("a") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
            total = self.total_cost
        if self._on_cost:
            self._on_cost(total)

    # -- gateway surface ---------------------------------------------------------------
    def chat(self, provider: str, model: str, messages: list[dict], *, effort: str, tag: str | None = None,
             caller: str | None = None, **kw):
        self._guard()
        caller = self._caller(caller)
        stage = self.stage
        res = self._gw.chat(provider, model, messages, effort=effort, tag=self.tag, caller=caller, **kw)
        self._record("chat", caller, stage, res.cost_usd, {
            "provider": provider, "model": model, "effort": effort, "cache_hit": res.cache_hit,
            "latency_s": round(res.latency_s, 2), "usage": res.usage, "reasoning_tokens": res.reasoning_tokens,
            "public_data": bool(kw.get("public_data")), "prompt": _clip(_text_of(messages)),
            "response": _clip(res.text or "")})
        return res

    def responses(self, model: str, input, *, effort: str, tag: str | None = None, caller: str | None = None, **kw):
        self._guard()
        caller = self._caller(caller)
        stage = self.stage
        res = self._gw.responses(model, input, effort=effort, tag=self.tag, caller=caller, **kw)
        self._record("responses", caller, stage, res.cost_usd, {
            "provider": "meta", "model": model, "effort": effort, "cache_hit": res.cache_hit,
            "latency_s": round(res.latency_s, 2), "usage": res.usage, "response": _clip(res.text or "")})
        return res

    def sam(self, phrase: str, *, tag: str | None = None, caller: str | None = None, **kw):
        self._guard()
        caller = self._caller(caller)
        stage = self.stage
        res = self._gw.sam(phrase, tag=self.tag, caller=caller, **kw)
        self._record("sam", caller, stage, res.cost_usd, {
            "provider": "meta", "model": "sam-3.1", "effort": None, "phrase": phrase,
            "cache_hit": res.cache_hit, "latency_s": round(res.latency_s, 2), "frames_billed": res.frames_billed,
            "public_data": bool(kw.get("public_data")), "n_frames": kw.get("n_frames")})
        return res

    def upload_file(self, path, *, tag: str | None = None, caller: str | None = None, **kw):
        self._guard()
        return self._gw.upload_file(path, tag=self.tag, caller=self._caller(caller), **kw)


class AppLedger:
    """Incremental reader of the gateway ledger, aggregated for app-attributed rows."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._offset = 0
        self._buf = b""
        self._lock = threading.Lock()
        self.by_job: dict[str, float] = defaultdict(float)
        self.by_day: dict[str, float] = defaultdict(float)
        self.rows_by_job: dict[str, list[dict]] = defaultdict(list)

    def refresh(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            size = self.path.stat().st_size
            if size < self._offset:  # truncated / rotated
                self._offset, self._buf = 0, b""
                self.by_job.clear()
                self.by_day.clear()
                self.rows_by_job.clear()
            with self.path.open("rb") as f:
                f.seek(self._offset)
                data = f.read()
            self._offset += len(data)
            lines = (self._buf + data).split(b"\n")
            self._buf = lines.pop()  # a partial last line stays buffered
            for line in lines:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                jid = job_of_caller(str(r.get("caller", "")))
                if jid is None:
                    continue
                cost = float(r.get("cost_usd", 0.0) or 0.0)
                self.by_job[jid] += cost
                self.by_day[str(r.get("time", ""))[:10]] += cost
                self.rows_by_job[jid].append(r)

    def job_cost(self, job_id: str) -> float:
        self.refresh()
        return self.by_job.get(job_id, 0.0)

    def job_rows(self, job_id: str) -> list[dict]:
        self.refresh()
        return list(self.rows_by_job.get(job_id, []))

    def spend_on(self, day: str) -> float:
        self.refresh()
        return self.by_day.get(day, 0.0)

    def spend_today(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return self.spend_on(now.strftime("%Y-%m-%d"))
