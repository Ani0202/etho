"""Job state machine, FIFO queue (one job runs at a time) and progress snapshots for SSE.

  queued -> running -> done | failed | cancelled
Stages (analysis):  validate, plan, track, synthesize, predict, tier1, report, overlay
Stages (revision):  revise, predict, tier1, report, overlay
A stage ends `done`, `skipped` (non-essential stage failed; the job goes on) or `failed` (the job stops).
One worker thread takes jobs in submission order; jobs wait in the queue, they never run concurrently
(the provider rate limits and the spend cap both assume it).
Within a job, stages in `PARALLEL_GROUPS` run at the same time (the report's methods-paragraph model call and
the overlay render share nothing). Every stage records its wall-clock window (`start_s`/`end_s`, seconds after
the job started), and a job's total time is job end minus job start, never the sum of its stages.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from etho.gateway import Gateway

from . import tuning
from .backend import Backend, Ctx, StageSkipped
from .config import AppConfig
from .guards import Refused, SpendGuard
from .jobgw import AppLedger, JobBudgetExceeded, JobCancelled, JobGateway, caller_for
from .metafiles import delete_job_files
from .retention import project_created, purge_project
from .store import read_json, resolve_project, write_json

ANALYZE_STAGES = ("validate", "plan", "track", "synthesize", "predict", "tier1", "report", "overlay")
REVISE_STAGES = ("revise", "predict", "tier1", "report", "overlay")
TERMINAL = ("done", "failed", "cancelled")
# Consecutive stages that run concurrently: the first name starts the group, the others join it. Model-call
# cost during a group is attributed to the first stage (the others make no model calls).
PARALLEL_GROUPS = (("report", "overlay"),)


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
    start_s: float | None = None   # wall-clock window, seconds after the job started (queue wait excluded)
    end_s: float | None = None


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
    ceiling_usd: float = 0.0       # reserved against the spend caps when the job was admitted
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


def stage_groups(stages: list[Stage]) -> list[list[Stage]]:
    """Split the stage list into steps; each step is a list of stages that run at the same time."""
    out: list[list[Stage]] = []
    i = 0
    while i < len(stages):
        group = [stages[i]]
        for g in PARALLEL_GROUPS:
            if stages[i].name == g[0]:
                while i + len(group) < len(stages) and stages[i + len(group)].name in g[1:]:
                    group.append(stages[i + len(group)])
        out.append(group)
        i += len(group)
    return out


def new_job_id() -> str:
    return "j" + uuid.uuid4().hex[:12]


def sse(event: str, data: dict, event_id: int | None = None) -> str:
    """One Server-Sent Event frame."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


class JobManager:
    def __init__(self, cfg: AppConfig, backend: Backend, ledger: AppLedger, guard: SpendGuard,
                 gateway_factory=Gateway, live=None):
        self.cfg, self.backend, self.ledger, self.guard = cfg, backend, ledger, guard
        self.live = live                       # LiveState or None; its kill switch / expiry stops work
        self._gateway_factory = gateway_factory
        self._gw: Gateway | None = None
        self.jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._cv = threading.Condition()
        self._persist_lock = threading.Lock()
        self._tls = threading.local()          # the stage whose thread is calling `say`
        self._stop = False
        self._thread: threading.Thread | None = None
        self._files_thread: threading.Thread | None = None
        self._purging: set[str] = set()      # projects retention is deleting right now

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
        paths = list(self.cfg.jobs_dir.glob("*/job.json")) + list(self.cfg.jobs_dir.glob("*/revisions/*/job.json"))
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

    def queue_len(self) -> int:
        with self._cv:
            return len(self._queue)

    def _busy_locked(self, project_id: str | None = None) -> bool:
        return any(j.status in ("queued", "running") and (project_id is None or j.project_id == project_id)
                   for j in self.jobs.values())

    def busy(self) -> bool:
        """Is any job queued or running (so shared directories must not be swept)?"""
        with self._cv:
            return self._busy_locked()

    def cleanup(self, now: float | None = None) -> list[str]:
        """Delete projects (uploads, tracks, overlays, revisions, their chunk videos and gateway-cache entries)
        older than `retention_hours` counted from the upload, unless a job for them is queued or running. Returns
        the removed ids. Called at startup and by the retention timer (retention.py)."""
        now = now or time.time()
        limit = self.cfg.retention_hours * 3600
        removed = []
        if not self.cfg.jobs_dir.exists():
            return removed
        for d in list(self.cfg.jobs_dir.iterdir()):
            if not d.is_dir():
                continue
            born = project_created(d)
            if born is None:                    # no job record (a crash before the first one): go by file times
                born = max((p.stat().st_mtime for p in d.rglob("*") if not p.is_symlink()), default=0.0)
                born = max(born, d.stat().st_mtime)
            if now - born > limit:
                with self._cv:     # decided under the lock: a job (a revision) queued a moment ago is seen here, and
                    if self._busy_locked(d.name) or d.name in self._purging:   # one submitted later is refused
                        continue
                    self._purging.add(d.name)
                    ids = {j.id for j in self.jobs.values() if j.project_id == d.name} | {d.name}
                try:
                    ids |= {f"{d.name}-r{x.name}" for x in (d / "revisions").glob("*") if x.name.isdigit()}
                    purge_project(self.cfg, self.ledger, d, sorted(ids), gw=self._files_gateway())
                    tuning.forget_under(d)
                    removed.append(d.name)
                finally:
                    with self._cv:
                        self._purging.discard(d.name)
        with self._cv:
            for jid in [k for k, j in self.jobs.items() if j.project_id in removed]:
                del self.jobs[jid]
        return removed

    # ---- Meta Files API copies ------------------------------------------------------------
    def _files_gateway(self):
        """The gateway used to delete uploaded clips (None if it cannot be built; the ids are then recorded)."""
        if self._gw is None:
            try:
                self._gw = self._gateway_factory()
            except Exception:
                return None
        return self._gw

    def delete_uploaded_files(self, job: Job) -> dict | None:
        """Delete the Files API copies this job uploaded. Only for a deployment with its own gateway cache (the
        cache entry holding the id is forgotten afterwards, which a shared cache must not lose). Idempotent."""
        if not self.cfg.private_gateway_cache:
            return None
        gw = self._files_gateway()
        if gw is None:
            return None
        try:
            who = caller_for(job.id, "etho.app.metafiles", self.cfg.caller_prefix)
            return delete_job_files(self.cfg, self.ledger, gw, [job.id], caller=who)
        except Exception:
            return None

    # ---- submission -----------------------------------------------------------------------
    def outstanding_usd(self) -> float:
        """What accepted-but-unfinished jobs may still spend: their reserved ceiling minus what they have spent."""
        return self.guard.outstanding()

    def active_for(self, project_id: str) -> Job | None:
        with self._cv:
            for j in self.jobs.values():
                if j.project_id == project_id and j.status in ("queued", "running"):
                    return j
        return None

    def submit(self, job: Job) -> Job:
        """Queue a job and reserve its spend ceiling against the caps in the same step. Raises QueueFull, or Refused
        when the daily or total cap leaves no room."""
        with self._cv:
            if len(self._queue) >= self.cfg.max_queue:
                raise QueueFull(f"{len(self._queue)} jobs are already waiting; try again later")
            if job.project_id in self._purging:
                raise QueueFull("this project is being deleted (its retention time is over)")
            job.ceiling_usd = self.guard.reserve(job.id, job.est_cost_usd)
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
                self.guard.release(job_id)
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
                           "cost_usd": round(cost, 4), "start_s": s.start_s, "end_s": s.end_s})
        cur = next((s for s in job.stages if s.status == "running"), None)
        running = job.status == "running" and job.started_mono is not None
        return {"id": job.id, "project_id": job.project_id, "kind": job.kind, "rev": job.rev, "status": job.status,
                "stage": cur.name if cur else None,
                "running_stages": [s.name for s in job.stages if s.status == "running"],
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
            with self._persist_lock:    # stages of a parallel group persist from different threads
                write_json(job.dir / "job.json", job.to_disk())
        except OSError:
            pass

    # ---- worker ---------------------------------------------------------------------------
    def stopped_by_operator(self) -> bool:
        return self.live is not None and self.live.stop_work()

    def _cancel_queued_for_stop(self) -> None:
        """Kill switch or expiry: nothing queued may start."""
        with self._cv:
            for jid in list(self._queue):
                j = self.jobs.get(jid)
                if j is not None and j.status == "queued":
                    self._queue = deque(x for x in self._queue if x != jid)
                    self.guard.release(jid)
                    j.status, j.message = "cancelled", "stopped: live use was switched off before this job started"
                    self._persist(j)
                    self._touch_locked(j)

    def _worker(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._queue or self._stop, 1.0)
                if self._stop:
                    return
            if self.stopped_by_operator():
                self._cancel_queued_for_stop()
            with self._cv:
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
                self.guard.release(job.id)        # the reservation goes before the terminal status becomes visible
                job.status = job.outcome or "failed"
                self._persist(job)
                self.touch(job)
                t = getattr(self, "_files_thread", None)
                if t is not None:
                    t.join(timeout=60)
                    self._files_thread = None
                if job.kind == "analyze":
                    self.delete_uploaded_files(job)       # a failed or cancelled job's uploads; no-op when done

    def _run(self, job: Job) -> None:
        project = resolve_project(self.cfg, job.project_id)
        if project is None:
            raise RuntimeError("project not found")
        if self._gw is None:
            self._gw = self._gateway_factory()
        outdir = project.base if job.kind == "analyze" else job.dir
        outdir.mkdir(parents=True, exist_ok=True)
        # The caps were checked and the job's ceiling reserved at submission; check again now that it actually starts
        # (jobs queue, and other spending may have used the room). The ceiling is what was reserved, or less.
        if self.stopped_by_operator():
            job.outcome, job.message = "cancelled", "stopped: live use was switched off"
            return
        try:
            ceiling = self.guard.recheck_at_start(job.id, job.est_cost_usd)
        except Refused as e:
            job.outcome, job.error = "failed", e.message
            return
        gw = JobGateway(self._gw, job_id=job.id, tag=self.cfg.tag, calls_path=outdir / "calls.jsonl",
                        on_cost=lambda _c: self.touch(job), max_cost_usd=ceiling,
                        cancelled=lambda: job.cancel_requested or self.stopped_by_operator(),
                        spent=lambda: self.ledger.job_cost(job.id), prefix=self.cfg.caller_prefix)
        job.gw = gw
        version = 0 if job.kind == "analyze" else int(job.rev)
        ctx = Ctx(self.cfg, project, job, gw, outdir, version, public_data=project.public_data,
                  say=lambda m: None)

        def say(msg: str) -> None:
            # the stage whose thread is talking; a helper thread started by a stage falls back to a running one
            st = getattr(self._tls, "stage", None) or next((s for s in job.stages if s.status == "running"), None)
            if st is not None:
                st.message = msg
            job.message = msg
            self.touch(job)

        ctx.say = say
        self.backend.prepare(ctx)
        for group in stage_groups(job.stages):
            if job.cancel_requested or self.stopped_by_operator():
                job.outcome, job.message = "cancelled", "cancelled"
                return
            gw.stage = group[0].name
            if len(group) == 1:
                errs = [(group[0], self._exec_stage(job, ctx, gw, group[0]))]
            else:
                with ThreadPoolExecutor(len(group), thread_name_prefix="etho-stage") as ex:
                    errs = list(zip(group, ex.map(lambda s: self._exec_stage(job, ctx, gw, s), group), strict=True))
            failed = False
            for st, e in errs:
                if e is not None:
                    self._fail_stage(job, st, e)
                    failed = True
            if failed:
                return
            if any(st.name == "track" for st in group):    # the clips are no longer needed: delete them now
                self._files_thread = threading.Thread(target=self.delete_uploaded_files, args=(job,),
                                                      name="etho-delete-files", daemon=True)
                self._files_thread.start()
        self.backend.finalize(ctx, ledger_cost=self.ledger.job_cost(job.id) or None)
        job.outcome = "done"

    def _exec_stage(self, job: Job, ctx: Ctx, gw: JobGateway, st: Stage) -> Exception | None:
        """Run one stage on the calling thread and record its status and wall-clock window.
        Returns the exception that ended it (None for done or skipped); the caller decides what it means."""
        self._tls.stage = st
        st.status, st.started = "running", time.monotonic()
        st.start_s = round(st.started - job.started_mono, 2) if job.started_mono is not None else None
        self.touch(job)
        err: Exception | None = None
        try:
            getattr(self.backend, f"stage_{st.name}")(ctx)
            st.status = "done"
        except StageSkipped as e:
            st.status, st.message = "skipped", str(e)
        except Exception as e:
            err = e
        finally:
            now = time.monotonic()
            st.elapsed_s = round(now - st.started, 2)
            st.end_s = round(now - job.started_mono, 2) if job.started_mono is not None else None
            st.cost_usd = round(gw.stage_cost.get(st.name, 0.0), 6)
            st.started = None
            self._tls.stage = None
            if err is not None:
                st.status = "failed"
            self._persist(job)
            self.touch(job)
        return err

    def _fail_stage(self, job: Job, st: Stage, e: Exception) -> None:
        """Mark a stage failed; the first failure in stage order decides how the job ends."""
        if isinstance(e, JobCancelled):
            st.message = "cancelled"
            job.outcome = job.outcome or "cancelled"
            job.message = "cancelled"
        elif isinstance(e, JobBudgetExceeded):
            st.message = str(e)
            if job.outcome is None:
                job.outcome, job.error = "failed", f"{st.name}: {e}"
        else:
            msg = f"{st.name}: {type(e).__name__}: {e}"[:600]
            st.message = msg
            if job.outcome is None:
                job.outcome, job.error = "failed", msg
        self._persist(job)
        self.touch(job)
