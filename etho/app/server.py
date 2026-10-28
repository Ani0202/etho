"""FastAPI app: upload form, job runner API with SSE progress, results and the correction loop."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import corrections as C
from .backend import FPS, Backend
from .config import AppConfig
from .guards import RateLimiter, Refused, SpendGuard
from .jobgw import AppLedger
from .jobs import TERMINAL, Job, JobManager, QueueFull, new_job_id, sse
from .store import ID_RE, Project, list_showcases, read_json, resolve_project, write_json
from .validate import ValidationError, check_definitions, check_extension, check_text, check_video

STATIC = Path(__file__).parent / "static"
STRIP_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.jpg$")
CALL_KEYS = ("attempt", "cost_usd", "latency_s", "cache_hit", "usage", "check", "revision_rejected", "n_corrections")
MULTIPART_SLACK = 1_000_000  # form fields and boundaries around the file


def default_definitions(cfg: AppConfig) -> dict:
    d = read_json(cfg.root / "configs" / "calms21_definitions.json", {})
    return {"behaviors": d.get("behaviors", {}), "subject": d.get("subject", ""), "partner": d.get("partner", "")}


def client_id(request: Request, cfg: AppConfig) -> str:
    if cfg.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def err(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status, headers=headers)


def create_app(cfg: AppConfig | None = None, *, backend: Backend | None = None, gateway_factory=None,
               ledger: AppLedger | None = None, start_worker: bool = True,
               clock=None, now=None) -> FastAPI:
    cfg = cfg or AppConfig()
    ledger = ledger or AppLedger(cfg.ledger_path)
    guard = SpendGuard(cfg, ledger, now=now)
    limiter = RateLimiter(cfg.rate_limit_jobs, cfg.rate_window_s, **({"clock": clock} if clock else {}))
    if gateway_factory is None:
        from etho.gateway import Gateway
        gateway_factory = Gateway
    manager = JobManager(cfg, backend or Backend(cfg), ledger, guard, gateway_factory)
    manager.load_existing()
    admit_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.stop()

    app = FastAPI(title="Etho", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.cfg, app.state.manager, app.state.ledger = cfg, manager, ledger
    app.state.guard, app.state.limiter = guard, limiter
    if start_worker:
        manager.start()

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, e: ValidationError):
        return err(e.status, e.code, e.message)

    @app.exception_handler(Refused)
    async def _refused(_: Request, e: Refused):
        h = {"Retry-After": str(int(e.retry_after) + 1)} if e.retry_after else None
        return err(e.status, e.code, e.message, h)

    @app.exception_handler(C.CorrectionError)
    async def _corr(_: Request, e: C.CorrectionError):
        return err(400, "bad_corrections", str(e))

    @app.middleware("http")
    async def upload_size_gate(request: Request, call_next):
        """Refuse an oversized upload from its declared length, before the body is parsed or stored."""
        if request.method == "POST" and request.url.path == "/api/jobs":
            cl = request.headers.get("content-length")
            if cl is None:
                return err(411, "length_required", "Content-Length is required for uploads")
            if not cl.isdigit() or int(cl) > cfg.max_upload_mb * 1e6 + MULTIPART_SLACK:
                return err(413, "too_large", f"upload is larger than the {cfg.max_upload_mb:g} MB limit")
        return await call_next(request)

    # ---- pages -------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/p/{pid}", include_in_schema=False)
    def project_page(pid: str):
        return FileResponse(STATIC / "project.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- config and showcases ----------------------------------------------------------------
    @app.get("/api/config")
    def api_config():
        d = default_definitions(cfg)
        spent = ledger.spend_today(guard.now())
        return {"limits": {"max_upload_mb": cfg.max_upload_mb, "max_duration_s": cfg.max_duration_s,
                           "extensions": [".mp4", ".mov", ".avi"], "max_behaviors": cfg.max_behaviors,
                           "rate_limit_jobs": cfg.rate_limit_jobs, "rate_window_s": cfg.rate_window_s},
                "defaults": d,
                "cap": {"cap_usd": cfg.daily_cap_usd, "spent_today_usd": round(spent, 4),
                        "remaining_usd": round(guard.remaining(manager.outstanding_usd()), 4)},
                "estimate": {"sam_usd_per_s": cfg.est_sam_usd_per_s, "tier1_usd_per_s": cfg.est_tier1_usd_per_s,
                             "fixed_usd": cfg.est_fixed_usd, "per_behavior_usd": cfg.est_per_behavior_usd}}

    @app.get("/api/showcases")
    def api_showcases():
        return {"showcases": list_showcases(cfg)}

    # ---- submit ------------------------------------------------------------------------------
    @app.post("/api/jobs")
    def api_submit(request: Request, video: Annotated[UploadFile, File()], definitions: Annotated[str, Form()],
                   subject: Annotated[str, Form()] = "", partner: Annotated[str, Form()] = ""):
        ext = check_extension(video.filename or "")
        try:
            raw_defs = json.loads(definitions)
        except json.JSONDecodeError as e:
            raise ValidationError("definitions must be JSON") from e
        defs = check_definitions(raw_defs, cfg)
        subject = check_text(subject, "subject", "the resident animal")
        partner = check_text(partner, "partner", "the other animal")
        jid = new_job_id()
        base = cfg.jobs_dir / jid
        base.mkdir(parents=True, exist_ok=False)
        dest = base / f"upload{ext}"
        try:
            size = 0
            with dest.open("wb") as f:
                while chunk := video.file.read(1 << 20):
                    size += len(chunk)
                    if size > cfg.max_upload_mb * 1e6:
                        raise ValidationError(f"upload is larger than the {cfg.max_upload_mb:g} MB limit", 413,
                                              "too_large")
                    f.write(chunk)
            info = check_video(dest, cfg)            # ffprobe: duration, size, decodable; nothing spent yet
            est = cfg.estimate_job_usd(info.duration_s, len(defs))
            cid = client_id(request, cfg)
            with admit_lock:
                limiter.check(cid)
                guard.check(est, manager.outstanding_usd())
                limiter.record(cid)
                inp = {"filename": re.sub(r"[^\w.\- ]", "_", os.path.basename(video.filename or "upload"))[:80],
                       "definitions": defs, "subject": subject, "partner": partner, "public_data": False,
                       "video": {**info.as_dict(), "name": os.path.basename(video.filename or "upload")[:80]},
                       "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "est_cost_usd": est}
                write_json(base / "input.json", inp)
                try:
                    manager.submit(Job(jid, jid, "analyze", base, est_cost_usd=est))
                except QueueFull:
                    limiter.release(cid)
                    raise
        except BaseException as e:
            shutil.rmtree(base, ignore_errors=True)
            if isinstance(e, QueueFull):
                raise Refused(str(e), 503, "queue_full") from e
            raise
        return {"id": jid, "project_id": jid, "url": f"/p/{jid}", "est_cost_usd": est,
                "queue_position": manager.queue_position(manager.jobs[jid])}

    # ---- jobs --------------------------------------------------------------------------------
    def job_or_404(jid: str) -> Job | None:
        return manager.jobs.get(jid) if ID_RE.match(jid) else None

    @app.get("/api/jobs/{jid}")
    def api_job(jid: str):
        j = job_or_404(jid)
        return manager.snapshot(j) if j else err(404, "not_found", "no such job")

    @app.post("/api/jobs/{jid}/cancel")
    def api_cancel(jid: str):
        return {"cancelled": manager.cancel(jid)} if job_or_404(jid) else err(404, "not_found", "no such job")

    @app.get("/api/jobs/{jid}/events")
    async def api_events(jid: str, request: Request):
        j = job_or_404(jid)
        if not j:
            return err(404, "not_found", "no such job")

        async def gen():
            last = -1
            while True:
                snap = manager.snapshot(j)
                terminal = j.status in TERMINAL
                yield sse("end" if terminal else "progress", snap, snap["version"])
                if terminal or await request.is_disconnected():
                    return
                last = j.version
                # wake on any state change, else tick once a second so elapsed time and cost keep moving
                await asyncio.to_thread(manager.wait_change, j, last, 1.0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- projects ----------------------------------------------------------------------------
    def project_or_none(pid: str) -> Project | None:
        return resolve_project(cfg, pid)

    def version_arg(p: Project, v: int | None) -> int:
        return p.latest_version() if v is None else v

    @app.get("/api/projects/{pid}")
    def api_project(pid: str):
        p = project_or_none(pid)
        j = manager.jobs.get(pid)
        if p is None:
            return err(404, "not_found", "no such project")
        res = p.result()
        if res is None:
            if j is None:
                return err(404, "not_found", "no such project")
            return {"id": pid, "status": j.status, "job": manager.snapshot(j), "error": j.error}
        versions = [res["versions"][0]] + [r for n in p.revision_numbers() if (r := p.revision(n))]
        active = manager.active_for(pid)
        last_rev = [x for x in manager.jobs.values() if x.project_id == pid and x.kind == "revise"]
        last_rev.sort(key=lambda x: x.rev or 0)
        out = {k: v for k, v in res.items() if k != "versions"}
        out.update({"status": "done", "versions": versions, "latest_version": versions[-1]["v"],
                    "active_job": manager.snapshot(active) if active else None,
                    "last_revision_job": manager.snapshot(last_rev[-1]) if last_rev else None})
        return out

    def versioned_file(pid: str, kind: str, v: int | None):
        p = project_or_none(pid)
        if p is None or p.result() is None:
            return None, None
        v = version_arg(p, v)
        f = p.version_file(v, kind)
        return (p, f) if f.exists() else (p, None)

    @app.get("/api/projects/{pid}/overlay")
    def api_overlay(pid: str, v: int | None = None):
        p, f = versioned_file(pid, "overlay", v)
        if f is None:
            return err(404, "not_found", "no overlay video for this version")
        return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "no-cache"})

    @app.get("/api/projects/{pid}/report")
    def api_report(pid: str, v: int | None = None):
        p, f = versioned_file(pid, "report", v)
        if f is None:
            return err(404, "not_found", "no report for this version")
        return HTMLResponse(f.read_text(), headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                       "frame-ancestors 'self'"})

    @app.get("/api/projects/{pid}/strip/{name}")
    def api_strip(pid: str, name: str):
        p = project_or_none(pid)
        if p is None or not STRIP_RE.match(name):
            return err(404, "not_found", "no such strip")
        f = p.strips_dir / name
        if not f.exists():
            return err(404, "not_found", "no such strip")
        return FileResponse(f, media_type="image/jpeg")

    @app.get("/api/projects/{pid}/audit")
    def api_audit(pid: str, v: int | None = None):
        p = project_or_none(pid)
        res = p.result() if p else None
        if res is None:
            return err(404, "not_found", "no such project")
        return audit_view(p, res, version_arg(p, v), ledger, manager)

    @app.post("/api/projects/{pid}/revise")
    async def api_revise(pid: str, request: Request):
        p = project_or_none(pid)
        res = p.result() if p else None
        if res is None:
            return err(404, "not_found", "no such project")
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return err(400, "bad_json", "body must be JSON")
        if not isinstance(payload, dict):
            return err(400, "bad_json", "body must be a JSON object")
        return await asyncio.to_thread(revise_sync, request, p, res, payload)

    def revise_sync(request: Request, p: Project, res: dict, payload: dict):
        if manager.active_for(p.id):
            return err(409, "busy", "a job is already running for this video")
        names = res["behaviors"]["names"]
        latest = p.latest_version()
        prev_rev = p.revision(latest) if latest else None
        prior_user = (prev_rev or {}).get("user_corrections", [])
        prior = (prev_rev or {}).get("corrections", [])
        cs = C.validate(payload, names=names, duration_s=res["video"]["duration_s"], fps=FPS,
                        max_total=cfg.max_corrections, existing=len(prior_user))
        labels = np.load(p.version_file(latest, "labels"))["labels"]
        new = C.to_revise_format(cs, labels, names, FPS)
        cumulative = prior + new
        affected = {c["behavior"] for c in cumulative}
        est = cfg.estimate_revise_usd(res["video"]["duration_s"], len(affected))
        cid = client_id(request, cfg)
        with admit_lock:
            limiter.check(cid)
            guard.check(est, manager.outstanding_usd())
            n = p.next_revision()
            d = p.work / "revisions" / str(n)
            d.mkdir(parents=True, exist_ok=True)
            job = Job(f"{p.id}-r{n}", p.id, "revise", d, rev=n, est_cost_usd=est,
                      params={"corrections": cumulative,
                              "user_corrections": prior_user + [c.model_dump() for c in cs]})
            try:
                manager.submit(job)
            except QueueFull as e:
                shutil.rmtree(d, ignore_errors=True)
                raise Refused(str(e), 503, "queue_full") from e
            limiter.record(cid)
        return {"job_id": job.id, "rev": n, "est_cost_usd": est, "n_corrections": len(cumulative)}

    return app


def audit_view(p: Project, res: dict, v: int, ledger: AppLedger, manager: JobManager) -> dict:
    """Everything needed to audit a result: models, efforts, prompts, detector code, every call's cost."""
    versions = [0] + p.revision_numbers()
    calls = []
    for ver in versions:
        f = p.version_dir(ver) / "calls.jsonl"
        if f.exists():
            for line in f.read_text().splitlines():
                try:
                    calls.append({"version": ver, **json.loads(line)})
                except json.JSONDecodeError:
                    pass
    spec = read_json(p.version_file(v, "detectors"), {})
    dets = [{"behavior": d["behavior"], "model": d.get("model"), "prompt_hash": d.get("prompt_hash"),
             "code": d["code"], "calls": [{k: c[k] for k in CALL_KEYS if k in c} for c in d.get("calls", [])]}
            for d in spec.get("detectors", [])]
    vf = p.version_file(v, "verdicts")
    verdicts = []
    if vf.exists():
        for line in vf.read_text().splitlines()[:3000]:
            try:
                x = json.loads(line)
            except json.JSONDecodeError:
                continue
            verdicts.append({k: x.get(k) for k in ("s0", "s1", "rule_label", "source", "label", "confidence",
                                                   "reason", "cost_usd")} |
                            {"strip": f"{x['seq']}_{x['s0']}_{x['s1']}.jpg"})
    track = read_json(p.seq_dir / "tracking.json", {})
    job_ids = [p.id] + [f"{p.id}-r{n}" for n in p.revision_numbers()]
    by_stage: dict[str, float] = {}
    n_calls = 0
    for jid in job_ids:
        for r in ledger.job_rows(jid):
            from .jobgw import stage_of_caller
            s = stage_of_caller(str(r.get("caller")))
            by_stage[s] = by_stage.get(s, 0.0) + float(r.get("cost_usd", 0.0) or 0.0)
            n_calls += 1
    return {"version": v, "plan": res.get("plan"), "detectors": dets, "calls": calls, "verdicts": verdicts,
            "tracking": {k: track.get(k) for k in ("phrase", "timings", "cost_usd", "cache_hits")},
            "definitions": res["behaviors"]["definitions"], "subject": res.get("subject"),
            "partner": res.get("partner"),
            "ledger": {"rows": n_calls, "by_stage_usd": {k: round(x, 6) for k, x in by_stage.items()},
                       "total_usd": round(sum(by_stage.values()), 6)},
            "note": res.get("audit_note")}


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(description="Etho web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    cfg = AppConfig()
    os.chdir(cfg.root)  # tier 1 reads cache/chunks relative to the repo root
    uvicorn.run(create_app(cfg), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
