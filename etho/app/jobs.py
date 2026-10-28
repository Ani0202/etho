"""Job state machine, FIFO queue (one job runs at a time) and progress snapshots for SSE.

  queued -> running -> done | failed | cancelled
Stages (analysis):  validate, plan, track, synthesize, predict, tier1, report, overlay
Stages (revision):  revise, predict, tier1, report, overlay
A stage ends `done`, `skipped` (non-essential stage failed; the job goes on) or `failed` (the job stops).
One worker thread takes jobs in submission order; jobs wait in the queue, they never run concurrently
(the provider rate limits and the spend cap both assume it).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from etho.gateway import Gateway

from .backend import Backend, Ctx, StageSkipped
from .config import AppConfig
from .guards import Refused, SpendGuard
from .jobgw import AppLedger, JobGateway
from .store import read_json, resolve_project, write_json

ANALYZE_STAGES = ("validate", "plan", "track", "synthesize", "predict", "tier1", "report", "overlay")
REVISE_STAGES = ("revise", "predict", "tier1", "report", "overlay")
TERMINAL = ("done", "failed", "cancelled")


class QueueFull(Exception):
    pass


@dataclass
class Stage:
    name: str
    status: str = "pending"    # pending | running | done | skipped | failed
    elapsed_s: float = 0.0
    message: str = ""
    cost_usd: float = 0.0
    started: float | None = field(default=None, repr=False)   # monotonic, while running


@dataclass
class Job:
    id: str
    project_id: str
    kind: str                  # analyze | revise
    dir: Path
    rev: int | None = None
    stages: list[Stage] = field(default_factory=list)
    status: str = "queued"
    est_cost_usd: float = 0.0
    params: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_mono: float | None = None
    finished_mono: float | None = None
    run_s: float = 0.0           # wall-clock from start to end (queue wait excluded)
    outcome: str | None = field(default=None, repr=False)   # terminal status, applied by the worker
    error: str | None = None
    message: str = ""
    cost_usd: float = 0.0
    version: int = 0
    cancel_requested: bool = False
    gw: JobGateway | None = field(default=None, repr=False)

    def to_disk(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "kind": self.kind, "rev": self.rev,
                "status": self.status, "est_cost_usd": self.est_cost_usd, "params": self.params,
                "created_at": self.created_at, "run_s": self.run_s, "error": self.error, "message": self.message,
                "cost_usd": self.cost_usd,
                "stages": [{k: v for k, v in vars(s).items() if k != "started"} for s in self.stages]}

    @classmethod
    def from_disk(cls, d: dict, path: Path) -> Job:
        j = cls(d["id"], d["project_id"], d["kind"], path, d.get("rev"),
                [Stage(**s) for s in d.get("stages", [])], d["status"], d.get("est_cost_usd", 0.0),
                d.get("params", {}), d.get("created_at", 0.0))
        j.run_s, j.error, j.message, j.cost_usd = d.get("run_s", 0.0), d.get("error"), d.get("message", ""), \
            d.get("cost_usd", 0.0)
        return j


def new_job_id() -> str:
    return "j" + uuid.uuid4().hex[:12]


def sse(event: str, data: dict, event_id: int | None = None) -> str:
    """One Server-Sent Event frame."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


class JobManager:
    def __init__(self, cfg: AppConfig, backend: Backend, ledger: AppLedger, guard: SpendGuard,
                 gateway_factory=Gateway):
        self.cfg, self.backend, self.ledger, self.guard = cfg, backend, ledger, guard
        self._gateway_factory = gateway_factory
        self._gw: Gateway | None = None
        self.jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None

    # ---- lifecycle ------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="etho-jobs", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=5)

    def load_existing(self) -> None:
        """Re-read persisted jobs. Anything that was queued or running when the process died is marked
        failed: restarting never silently resumes (and re-spends)."""
        paths = list(self.cfg.jobs_dir.glob("*/job.json")) + list(self.cfg.jobs_dir.glob("*/revisions/*/job.json")) \
            + list(self.cfg.showcase_state_dir.glob("*/revisions/*/job.json"))
        for p in paths:
            d = read_json(p)
            if not d:
                continue
            j = Job.from_disk(d, p.parent)
            if j.status not in TERMINAL:
                j.status, j.error = "failed", "interrupted by an app restart"
                for s in j.stages:
                    if s.status in ("running", "pending"):
                        s.status = "failed" if s.status == "running" else "pending"
                self._persist(j)
            self.jobs[j.id] = j

    # ---- submission -----------------------------------------------------------------------
    def outstanding_usd(self) -> float:
        """What accepted-but-unfinished jobs may still spend (their estimate minus what they've spent)."""
        with self._cv:
            live = [j for j in self.jobs.values() if j.status in ("queued", "running")]
        return sum(max(0.0, j.est_cost_usd - self.job_cost(j)) for j in live)

    def active_for(self, project_id: str) -> Job | None:
        with self._cv:
            for j in self.jobs.values():
                if j.project_id == project_id and j.status in ("queued", "running"):
                    return j
        return None

    def submit(self, job: Job) -> Job:
        with self._cv:
            if len(self._queue) >= self.cfg.max_queue:
                raise QueueFull(f"{len(self._queue)} jobs are already waiting; try again later")
            job.stages = [Stage(n) for n in (ANALYZE_STAGES if job.kind == "analyze" else REVISE_STAGES)]
            self.jobs[job.id] = job
            self._queue.append(job.id)
            self._persist(job)
            self._touch_locked(job)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._cv:
            j = self.jobs.get(job_id)
            if not j or j.status in TERMINAL:
                return False
            if j.status == "queued":
                self._queue = deque(x for x in self._queue if x != job_id)
                j.status, j.message = "cancelled", "cancelled before it started"
            else:
                j.cancel_requested = True
                j.message = "cancelling after the current stage"
            self._persist(j)
            self._touch_locked(j)
            return True

    # ---- snapshots and change notification --------------------------------------------------
    def job_cost(self, job: Job) -> float:
        led = self.ledger.job_cost(job.id)
        if led > 0:
            return led
        return job.gw.total_cost if job.gw else job.cost_usd

    def queue_position(self, job: Job) -> int:
        with self._cv:
            try:
                return list(self._queue).index(job.id) + 1
            except ValueError:
                return 0

    def snapshot(self, job: Job) -> dict:
        now = time.monotonic()
        stages = []
        for s in job.stages:
            el = (now - s.started) if (s.status == "running" and s.started) else s.elapsed_s
            cost = job.gw.stage_cost.get(s.name, s.cost_usd) if (s.status == "running" and job.gw) else s.cost_usd
            stages.append({"name": s.name, "status": s.status, "elapsed_s": round(el, 1), "message": s.message,
                           "cost_usd": round(cost, 4)})
        cur = next((s for s in job.stages if s.status == "running"), None)
        running = job.status == "running" and job.started_mono is not None
        return {"id": job.id, "project_id": job.project_id, "kind": job.kind, "rev": job.rev, "status": job.status,
                "stage": cur.name if cur else None,
                "stage_index": job.stages.index(cur) + 1 if cur else sum(s.status in ("done", "skipped")
                                                                          for s in job.stages),
                "n_stages": len(job.stages), "message": (cur.message if cur and cur.message else job.message),
                "elapsed_s": round((now - job.started_mono) if running else job.run_s, 1),
                "queue_position": self.queue_position(job), "cost_usd": round(self.job_cost(job), 4),
                "est_cost_usd": job.est_cost_usd, "error": job.error, "stages": stages, "version": job.version}

    def _touch_locked(self, job: Job) -> None:
        job.version += 1
        self._cv.notify_all()

    def touch(self, job: Job) -> None:
        with self._cv:
            self._touch_locked(job)

    def wait_change(self, job: Job, last_version: int, timeout: float) -> int:
        with self._cv:
            self._cv.wait_for(lambda: job.version != last_version or self._stop, timeout)
            return job.version

    def _persist(self, job: Job) -> None:
        try:
            write_json(job.dir / "job.json", job.to_disk())
        except OSError:
            pass

    # ---- worker ---------------------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._queue or self._stop, 1.0)
                if self._stop:
                    return
                if not self._queue:
                    continue
                job = self.jobs[self._queue.popleft()]
                if job.status != "queued":
                    continue
                job.status, job.started_mono = "running", time.monotonic()
                self._touch_locked(job)
            try:
                self._run(job)
            except Exception as e:  # never let the worker die
                job.outcome, job.error = "failed", f"{type(e).__name__}: {e}"[:500]
            finally:
                # elapsed time and cost are fixed before the terminal status becomes visible, so a client
                # never sees a finished job with a smaller elapsed time than it saw while running
                job.finished_mono = time.monotonic()
                job.run_s = round(job.finished_mono - job.started_mono, 2)
                job.cost_usd = round(job.gw.total_cost if job.gw else job.cost_usd, 6)
                job.status = job.outcome or "failed"
                self._persist(job)
                self.touch(job)

    def _run(self, job: Job) -> None:
        project = resolve_project(self.cfg, job.project_id)
        if project is None:
            raise RuntimeError("project not found")
        if self._gw is None:
            self._gw = self._gateway_factory()
        outdir = project.base if job.kind == "analyze" else job.dir
        outdir.mkdir(parents=True, exist_ok=True)
        gw = JobGateway(self._gw, job_id=job.id, tag=self.cfg.tag, calls_path=outdir / "calls.jsonl",
                        on_cost=lambda _c: self.touch(job))
        job.gw = gw
        version = 0 if job.kind == "analyze" else int(job.rev)
        ctx = Ctx(self.cfg, project, job, gw, outdir, version, public_data=project.public_data,
                  say=lambda m: None)
        current: list[Stage] = []

        def say(msg: str) -> None:
            if current:
                current[0].message = msg
            job.message = msg
            self.touch(job)

        ctx.say = say
        self.backend.prepare(ctx)
        # The cap was checked at submission; check again now that the job actually starts (jobs queue).
        others = self.outstanding_usd() - max(0.0, job.est_cost_usd - self.job_cost(job))
        try:
            self.guard.check(job.est_cost_usd, others)
        except Refused as e:
            job.outcome, job.error = "failed", e.message
            return
        for st in job.stages:
            if job.cancel_requested:
                job.outcome, job.message = "cancelled", "cancelled"
                return
            current[:] = [st]
            st.status, st.started = "running", time.monotonic()
            gw.stage = st.name
            self.touch(job)
            try:
                getattr(self.backend, f"stage_{st.name}")(ctx)
                st.status = "done"
            except StageSkipped as e:
                st.status, st.message = "skipped", str(e)
            except Exception as e:
                st.status = "failed"
                job.outcome, job.error = "failed", f"{st.name}: {type(e).__name__}: {e}"[:600]
                st.message = job.error
                return
            finally:
                st.elapsed_s = round(time.monotonic() - st.started, 2)
                st.cost_usd = round(gw.stage_cost.get(st.name, 0.0), 6)
                st.started = None
                self._persist(job)
                self.touch(job)
        current.clear()
        self.backend.finalize(ctx, ledger_cost=self.ledger.job_cost(job.id) or None)
        job.outcome = "done"
