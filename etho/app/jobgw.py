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
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from etho.gateway import Gateway
from etho.gateway import prices as gw_prices
from etho.gateway.gateway import DEFAULT_MAX_TOKENS, estimate_llm_cost

JOB_PREFIX = "job:"
MAX_TEXT = 6000
FALLBACK_CALL_EST_USD = 0.05     # reserved for a call whose worst case cannot be computed (the gateway will judge it)


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


def est_chat_usd(provider: str, model: str, messages: list[dict], kw: dict) -> float:
    """Worst-case cost of one chat call (the gateway's own estimator over the same payload)."""
    try:
        body = {"messages": messages, **{k: v for k, v in kw.items() if isinstance(v, (str, list, dict))}}
        return estimate_llm_cost(provider, model, body, int(kw.get("max_tokens", DEFAULT_MAX_TOKENS)))
    except Exception:
        return FALLBACK_CALL_EST_USD


def est_responses_usd(model: str, input, kw: dict) -> float:
    try:
        return estimate_llm_cost("meta", model, {"input": input}, int(kw.get("max_output_tokens", DEFAULT_MAX_TOKENS)))
    except Exception:
        return FALLBACK_CALL_EST_USD


def est_sam_usd(kw: dict) -> float:
    try:
        n = kw.get("n_frames")
        return gw_prices.sam_video_cost(int(n)) if (n and kw.get("image") is None) else gw_prices.sam_image_cost(1)
    except Exception:
        return FALLBACK_CALL_EST_USD


class JobGateway:
    """Drop-in for `Gateway` as far as the pipeline modules use it (chat, sam, responses, upload_file).

    The per-job ceiling is a reservation, not just a check: before each call the call's worst-case cost is held
    against the ceiling (spent + calls in flight + this call must fit), so the many calls a job makes in parallel
    cannot jointly pass it. A call that does not fit waits for the ones in flight to settle; if nothing is in
    flight and it still does not fit, the job stops. `spent` (the ledger's total for this job) is also read, so a
    cost the gateway booked for a failed call counts toward the ceiling too."""

    def __init__(self, gw: Gateway, *, job_id: str, tag: str, calls_path: Path | None = None,
                 on_cost=None, max_cost_usd: float | None = None, cancelled=None, spent=None):
        self._gw, self.job_id, self.tag = gw, job_id, tag
        self.max_cost_usd = max_cost_usd      # per-job ceiling, reserved before every call
        self._cancelled = cancelled
        self._spent = spent                   # callable: ledger cost of this job so far
        self.stage = "init"
        self.stage_cost: dict[str, float] = defaultdict(float)
        self.total_cost = 0.0
        self.inflight_usd = 0.0
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._calls_path = calls_path
        self._on_cost = on_cost

    # -- helpers -----------------------------------------------------------------------
    def spent_usd(self) -> float:
        return max(self.total_cost, self._spent() if self._spent else 0.0)

    def _guard(self) -> None:
        if self._cancelled and self._cancelled():
            raise JobCancelled("cancelled by the user")
        if self.max_cost_usd is not None and self.spent_usd() >= self.max_cost_usd:
            raise JobBudgetExceeded(f"this job reached its spend ceiling of ${self.max_cost_usd:.2f} "
                                    f"(spent ${self.spent_usd():.2f}); no further model calls")

    def _reserve(self, est: float) -> None:
        """Hold `est` against the ceiling; wait while other calls in flight leave no room. Check and hold are one
        step under the lock, so concurrent callers cannot all see the same room."""
        cap = self.max_cost_usd
        while True:
            spent = self._spent() if self._spent else 0.0     # ledger read outside the lock
            with self._cv:
                if self._cancelled and self._cancelled():
                    raise JobCancelled("cancelled by the user")
                now_spent = max(self.total_cost, spent)
                if cap is None or now_spent + self.inflight_usd + est <= cap:
                    self.inflight_usd += est
                    return
                if self.inflight_usd <= 1e-12:
                    raise JobBudgetExceeded(
                        f"this job's spend ceiling is ${cap:.2f} (spent ${now_spent:.2f}); the next call could cost "
                        f"up to ${est:.3f}, so no further model calls")
                self._cv.wait(0.25)

    def _release(self, est: float) -> None:
        with self._cv:
            self.inflight_usd = max(0.0, self.inflight_usd - est)
            self._cv.notify_all()

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
        est = est_chat_usd(provider, model, messages, kw)
        self._reserve(est)
        try:
            res = self._gw.chat(provider, model, messages, effort=effort, tag=self.tag, caller=caller, **kw)
            self._record("chat", caller, stage, res.cost_usd, {
                "provider": provider, "model": model, "effort": effort, "cache_hit": res.cache_hit,
                "latency_s": round(res.latency_s, 2), "usage": res.usage, "reasoning_tokens": res.reasoning_tokens,
                "public_data": bool(kw.get("public_data")), "prompt": _clip(_text_of(messages)),
                "response": _clip(res.text or "")})
        finally:
            self._release(est)
        return res

    def responses(self, model: str, input, *, effort: str, tag: str | None = None, caller: str | None = None, **kw):
        self._guard()
        caller = self._caller(caller)
        stage = self.stage
        est = est_responses_usd(model, input, kw)
        self._reserve(est)
        try:
            res = self._gw.responses(model, input, effort=effort, tag=self.tag, caller=caller, **kw)
            self._record("responses", caller, stage, res.cost_usd, {
                "provider": "meta", "model": model, "effort": effort, "cache_hit": res.cache_hit,
                "latency_s": round(res.latency_s, 2), "usage": res.usage, "response": _clip(res.text or "")})
        finally:
            self._release(est)
        return res

    def sam(self, phrase: str, *, tag: str | None = None, caller: str | None = None, **kw):
        self._guard()
        caller = self._caller(caller)
        stage = self.stage
        est = est_sam_usd(kw)
        self._reserve(est)
        try:
            res = self._gw.sam(phrase, tag=self.tag, caller=caller, **kw)
            self._record("sam", caller, stage, res.cost_usd, {
                "provider": "meta", "model": "sam-3.1", "effort": None, "phrase": phrase,
                "cache_hit": res.cache_hit, "latency_s": round(res.latency_s, 2),
                "frames_billed": res.frames_billed, "public_data": bool(kw.get("public_data")),
                "n_frames": kw.get("n_frames")})
        finally:
            self._release(est)
        return res

    def upload_file(self, path, *, tag: str | None = None, caller: str | None = None, **kw):
        self._guard()
        return self._gw.upload_file(path, tag=self.tag, caller=self._caller(caller), **kw)


def row_epoch(t: Any) -> float:
    """Ledger row time as epoch seconds; an unparsable time counts as now (it then sits in every window)."""
    try:
        d = datetime.fromisoformat(str(t))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return time.time()


class AppLedger:
    """Incremental reader of the gateway ledger, aggregated for app-attributed rows.

    The caps (rolling 24 h and cumulative) are computed here from the ledger itself, so they survive restarts and
    see exactly what the gateway booked. A row counts toward them when its caller starts with `job:` (a call made
    for an app job) or when it is a reservation the gateway booked as abandoned (a process died mid-call; the
    gateway cannot say whose) and its tag is the app's tag; counting those errs on the safe side. Rows of other
    callers on the same tag (the lead's own demo recording on `demo-reserve`) do not count."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._offset = 0
        self._buf = b""
        self._lock = threading.Lock()
        self.by_job: dict[str, float] = defaultdict(float)
        self.by_day: dict[str, float] = defaultdict(float)
        self.rows_by_job: dict[str, list[dict]] = defaultdict(list)
        self._app_rows: list[tuple[float, str, float]] = []      # (epoch, tag, cost_usd)

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
                self._app_rows.clear()
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
                cost = float(r.get("cost_usd", 0.0) or 0.0)
                if jid is None:
                    if r.get("abandoned_reservation") and cost > 0:
                        self._app_rows.append((row_epoch(r.get("time")), str(r.get("tag", "")), cost))
                    continue
                self._app_rows.append((row_epoch(r.get("time")), str(r.get("tag", "")), cost))
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

    @staticmethod
    def _tag_ok(row_tag: str, tag: str | None) -> bool:
        return tag is None or row_tag in ("", tag)

    def spend_window(self, now: datetime | None = None, *, hours: float = 24.0, tag: str | None = None) -> float:
        """App spend in the `hours` before `now` (rolling), for rows on `tag`."""
        self.refresh()
        start = (now or datetime.now(timezone.utc)).timestamp() - hours * 3600
        with self._lock:      # rows dated after `now` (clock skew) count: that errs on the safe side
            return sum(c for t, tg, c in self._app_rows if t > start and self._tag_ok(tg, tag))

    def spend_total(self, tag: str | None = None) -> float:
        """All app spend ever booked on `tag` (the cumulative cap)."""
        self.refresh()
        with self._lock:
            return sum(c for _, tg, c in self._app_rows if self._tag_ok(tg, tag))
