"""FastAPI app: upload form, job runner API with SSE progress, results and the correction loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from etho.app.headmw import HeadMiddleware
from etho.gateway import Gateway, GatewayConfig
from etho.rules.sandbox import SandboxUnavailable, isolation_status

from . import corrections as C
from . import progress as PG
from .access import COOKIE, AccessControl, shared_bucket
from .backend import FPS, Backend
from .config import AppConfig
from .deadline import UploadDeadline
from .guards import RateLimiter, Refused, SpendGuard, client_key
from .jobgw import AppLedger, stage_of_caller
from .jobs import TERMINAL, ClientBusy, Job, JobManager, QueueFull, new_job_id, sse
from .live import LiveState
from .retention import Retention
from .store import ID_RE, Project, fork_showcase, list_showcases, read_json, resolve_project, write_json
from .validate import ValidationError, check_definitions, check_extension, check_text, check_video

STATIC = Path(__file__).parent / "static"
STRIP_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.jpg$")
CALL_KEYS = ("attempt", "cost_usd", "latency_s", "cache_hit", "usage", "check", "revision_rejected", "n_corrections")
MULTIPART_SLACK = 1_000_000  # form fields and boundaries around the file
# POSTs that can never start a model call: entering or dropping the access code, and cancelling a job (cancelling
# only ever stops spending). Every other POST (and any other non-read method) is refused unless live use is on and
# the visitor has the access code: a route added later is closed by default.
SAFE_POST = re.compile(r"^/api/(access|access/logout|jobs/[A-Za-z0-9_-]+/cancel)$")
READ_METHODS = ("GET", "HEAD", "OPTIONS")
SSE_MAX_S = 1800.0
SAMPLE_FILE, SAMPLE_JSON = "calms21_dev_60s.mp4", "sample.json"
SAMPLE_ATTRIBUTION = "Clip from CalMS21 (Sun et al. 2021, Caltech), licensed CC BY 4.0"
SAMPLE_LINKS = {"dataset": "https://data.caltech.edu/records/s0vdx-0k302",
                "license": "https://creativecommons.org/licenses/by/4.0/"}
SAMPLE_TRIMMED = "This clip was trimmed to 60 s and re-encoded from a longer CalMS21 recording."

# Pages and API responses: scripts only from this origin, no inline script, no framing by other sites.
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "media-src 'self' blob:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'")
# The report is generated HTML with no script: lock it down completely (it may only be framed by this origin).
REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; "
              "frame-ancestors 'self'; sandbox allow-same-origin")


def default_definitions(cfg: AppConfig) -> dict:
    d = read_json(cfg.root / "configs" / "calms21_definitions.json", {})
    return {"behaviors": d.get("behaviors", {}), "subject": d.get("subject", ""), "partner": d.get("partner", "")}


# What the browser UI shows for each refusal: a plain sentence that says what happened and that the showcase is still
# open. API clients still get the structured error and status; `friendly` is extra.
SHARED_BUCKET_WARNING = ("every visitor appears as one client (a loopback or Tailscale address): the per-client job "
                         "limit and the wrong-code throttle are one shared bucket, so ETHO_APP_RATE_JOBS is a total")
OPEN = "The precomputed showcase runs stay open."
FRIENDLY = {
    "daily_cap": "Today's live budget for this demo is used up, so uploads and revisions are paused. " + OPEN +
                 " Live use reopens as the 24-hour window moves on.",
    "total_cap": "The live budget for this demo is used up, so uploads and revisions are closed. " + OPEN,
    "read_only": "This site is a read-only showcase: uploads and revisions are switched off. " + OPEN,
    "killed": "Live scoring is switched off right now. " + OPEN,
    "expired": "The live window for this demo has ended. " + OPEN,
    "access_required": "Live scoring needs the access code from the submission notes. Enter it on the home page. " +
                       OPEN,
    "access_unavailable": "Live scoring is not available right now. " + OPEN,
    "access_throttled": "Too many wrong codes from your connection; please wait a few minutes and try again.",
    "wrong_code": "That access code is not valid.",
    "queue_full": "The scoring queue is full right now. Please try again in a few minutes. " + OPEN,
    "rate_limited": "You have reached the hourly limit for scoring jobs. Please try again later. " + OPEN,
    "busy": "The server is busy with other uploads. Please try again in a moment.",
}


CONSENT_TEXT = "I have the right to upload this video, and it contains no people"
CONSENT_FRIENDLY = ("Please tick the box \"" + CONSENT_TEXT + "\" before uploading. Etho only scores animal video "
                    "that you may share and that shows no people. Nothing was kept or spent. " + OPEN)
_TRUE = ("1", "true", "yes", "on")


def friendly_map(cfg: AppConfig) -> dict[str, str]:
    """The sentence for each refusal code. Without open mode it is `FRIENDLY` itself. In open mode every limit also says
    where to go next (the showcase on this site, `make demo` from the repo for an offline replay with no keys) and
    when it resets (the next day for the daily and per-visitor limits; never for the total cap)."""
    if not cfg.open:
        return FRIENDLY
    offline = ("An offline replay demo (`make demo`, no keys needed) is described in the project README: "
               + cfg.readme_url)
    show = "The precomputed showcase runs stay open on this site (home page)."
    hours = cfg.rate_window_s / 3600
    window = "24 hours" if abs(hours - 24) < 1e-9 else f"{hours:g} hours"
    return {
        **FRIENDLY,
        "daily_cap": "Today's live budget for this demo is used up, so uploads and revisions are paused. " + show +
                     " " + offline + " Live use reopens as the 24-hour budget window moves on: please try again "
                     "tomorrow.",
        "total_cap": "The live budget for this demo is used up, so live use is over for this demo: uploads and "
                     "revisions are closed for good. " + show + " " + offline,
        "rate_limited": f"You have used your {cfg.rate_limit_jobs} live runs for the last {window} (a revision counts "
                        f"as a run). Your limit resets {window} after each run, so please try again tomorrow. " +
                        show + " " + offline,
        "queue_full": "Etho is already scoring other videos (at most "
                      f"{cfg.max_in_flight or cfg.max_queue} jobs at a time, one video takes about two minutes). "
                      "Please try again in a few minutes. " + show + " " + offline,
        "consent_required": CONSENT_FRIENDLY,
        "client_busy": "You already have a run in progress: please wait for it to finish (its page shows the "
                       "progress), then start the next one. " + show,
        "too_many_rejected": "Several of your uploads were rejected in the last hour (a file that is not a video, "
                             "a missing consent box, or a file that is too large), so uploads from your connection "
                             "are paused for a while. Please try again in about an hour. " + show + " " + offline,
        "upload_timeout": "The upload took too long to arrive, so it was stopped. Nothing was kept or spent. Please "
                          "try again on a faster connection, or use a shorter clip. " + show,
        **{code: f"{what} Nothing was kept or spent. Please upload an mp4, mov or avi of up to "
                 f"{cfg.max_duration_s:g} seconds and {cfg.max_upload_mb:g} MB; the sample clip on the home page "
                 f"works as it is."
           for code, what in {
               "bad_type": "That file type is not supported.",
               "unreadable": "That file could not be read as a video.",
               "bad_container": "That file is not the kind of video its name says.",
               "no_video": "That file has no video in it.",
               "too_long": f"That video is longer than the {cfg.max_duration_s:g} second limit.",
               "too_large": f"That file is larger than the {cfg.max_upload_mb:g} MB limit.",
           }.items()},
    }


def err(status: int, code: str, message: str, headers: dict | None = None, friendly: str | None = None,
        table: dict[str, str] | None = None) -> JSONResponse:
    table = FRIENDLY if table is None else table
    body = {"error": code, "message": message}
    if friendly or code in table:
        body["friendly"] = friendly or table[code]
    return JSONResponse(body, status_code=status, headers=headers)


def refusal(e: Refused, table: dict[str, str] | None = None) -> JSONResponse:
    h = {"Retry-After": str(int(e.retry_after) + 1)} if e.retry_after else None
    return err(e.status, e.code, e.message, h, e.friendly, table)


@dataclass
class AppGatewayConfig(GatewayConfig):
    """The gateway's config with an optional private response cache (ops/ and the ledger stay the shared ones, so
    the caps and tag budgets are still enforced across every process)."""
    cache_override: Path | None = None

    @property
    def cache_dir(self) -> Path:
        return self.cache_override or GatewayConfig.cache_dir.fget(self)


def create_app(cfg: AppConfig | None = None, *, backend: Backend | None = None, gateway_factory=None,
               ledger: AppLedger | None = None, start_worker: bool = True, clock=None, now=None,
               isolation=None) -> FastAPI:
    """`now` (a callable returning an aware datetime) drives the rolling cap window, the live-window expiry and
    cookie expiry; `clock` (monotonic seconds) drives the per-client rate limit; both exist for tests."""
    cfg = cfg or AppConfig()
    try:
        _iso = isolation_status()
        logging.getLogger("etho.app").log(logging.INFO if _iso["mode"] == "bwrap" else logging.WARNING,
                                          "rule sandbox isolation: %s (%s)", _iso["mode"], _iso["detail"])
    except SandboxUnavailable as e:
        logging.getLogger("etho.app").error("rule sandbox misconfigured, jobs will fail: %s", e)
    fr = friendly_map(cfg)

    def E(status, code, message, headers=None, friendly=None):
        return err(status, code, message, headers, friendly, fr)

    def refuse(e):
        return refusal(e, fr)

    ledger = ledger or AppLedger(cfg.ledger_path, cfg.caller_prefix)
    guard = SpendGuard(cfg, ledger, now=now)
    access = AccessControl(cfg, now=now)
    live = LiveState(cfg, access, now=now, isolation=isolation or isolation_status_safe)
    limiter = RateLimiter(cfg.rate_limit_jobs, cfg.rate_window_s, **({"clock": clock} if clock else {}))
    # rejected uploads (a bad file, no consent, too large) per client per hour; 0 = no limit
    rejects = RateLimiter(max(1, cfg.max_rejects_per_hour), 3600.0, **({"clock": clock} if clock else {}))
    uploads_by_client: dict[str, int] = {}
    uploads_lock = threading.Lock()
    if gateway_factory is None:
        def gateway_factory():
            return Gateway(AppGatewayConfig(root=cfg.root, cache_override=cfg.gateway_cache_root))
    manager = JobManager(cfg, backend or Backend(cfg), ledger, guard, gateway_factory, live=live)
    manager.load_existing()
    retention = Retention(cfg, manager, ledger)
    try:
        retention.run_once(startup=True)
    except Exception:                  # a bad file or ledger row must not stop the service from starting
        logging.getLogger("etho.app").exception("startup retention pass failed; the timer will retry")
    admit_lock = threading.Lock()
    upload_sem = threading.BoundedSemaphore(cfg.max_concurrent_uploads)
    probe_sem = threading.BoundedSemaphore(cfg.max_concurrent_probes)
    sse_open: dict[str, int] = {}
    sse_lock = threading.Lock()
    last_cleanup = [time.monotonic()]

    @asynccontextmanager
    async def lifespan(app):
        yield
        retention.stop()
        manager.stop()

    app = FastAPI(title="Etho", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.cfg, app.state.manager, app.state.ledger = cfg, manager, ledger
    app.state.guard, app.state.limiter = guard, limiter
    app.state.access, app.state.live, app.state.retention = access, live, retention
    if start_worker:
        manager.start()
        retention.start()

    def snap(j: Job) -> dict:
        """A job snapshot for the browser: five phases and a plain-language failure message."""
        return PG.view(manager.snapshot(j), fr, cfg.public)

    def who(request: Request) -> str:
        return client_key(request.client.host if request.client else None)

    def spend_refusal(request: Request) -> Refused | None:
        """Why this request may not start work that costs money (None = it may). Order: is live use on at all
        (read-only, kill switch, live window), then does the visitor hold a valid access cookie."""
        r = live.refusal()
        if r is not None:
            return r
        if not access.valid(request.cookies.get(COOKIE)):
            return Refused("live use needs the access code: enter it on the home page first", 401, "access_required")
        return None

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, e: ValidationError):
        return E(e.status, e.code, e.message)

    @app.exception_handler(Refused)
    async def _refused(_: Request, e: Refused):
        return refuse(e)

    @app.exception_handler(C.CorrectionError)
    async def _corr(_: Request, e: C.CorrectionError):
        return E(400, "bad_corrections", str(e))

    @app.middleware("http")
    async def gate(request: Request, call_next):
        """Cheap refusals first, before any body is read; then security headers on every response."""
        resp = None
        if request.method not in READ_METHODS and not SAFE_POST.match(request.url.path):
            gated = spend_refusal(request)
            if gated is not None:
                resp = refuse(gated)          # before any body is read
        if resp is None and request.method == "POST":
            cl, te = request.headers.get("content-length"), request.headers.get("transfer-encoding")
            if request.url.path == "/api/jobs":
                limit = cfg.max_upload_mb * 1e6 + MULTIPART_SLACK
                if cl is None or te:
                    resp = E(411, "length_required", "Content-Length is required for uploads")
                elif not cl.isdigit() or int(cl) > limit:
                    resp = E(413, "too_large", f"upload is larger than the {cfg.max_upload_mb:g} MB limit")
                else:
                    try:
                        cid = who(request)
                        if cfg.max_rejects_per_hour:
                            try:
                                rejects.check(cid)
                            except Refused as e:
                                raise Refused(f"{cfg.max_rejects_per_hour} rejected uploads in the last hour; try "
                                              "again later", 429, "too_many_rejected",
                                              retry_after=e.retry_after) from e
                        limiter.check(cid)
                        if cfg.max_jobs_per_client and manager.client_jobs(cid) >= cfg.max_jobs_per_client:
                            raise Refused("you already have a run in progress", 429, "client_busy")
                        if manager.queue_len() >= cfg.max_queue:
                            raise Refused(f"{manager.queue_len()} jobs are already waiting; try again later", 503,
                                          "queue_full")
                        if cfg.max_in_flight and manager.in_flight() >= cfg.max_in_flight:
                            raise Refused(f"{manager.in_flight()} jobs are already queued or running; try again in "
                                          "a few minutes", 503, "queue_full")
                        guard.check(cfg.estimate_job_usd(1.0, 1), manager.outstanding_usd())   # cap already spent
                    except Refused as e:
                        resp = refuse(e)
                if resp is None:
                    cid = who(request)
                    with uploads_lock:
                        mine = uploads_by_client.get(cid, 0)
                        own = not cfg.max_uploads_per_client or mine < cfg.max_uploads_per_client
                        if own:
                            uploads_by_client[cid] = mine + 1
                    if not own:
                        resp = refuse(Refused("you already have an upload in progress", 429, "client_busy"))
                    elif not upload_sem.acquire(blocking=False):
                        resp = E(503, "busy", "too many uploads in progress; try again in a moment")
                        with uploads_lock:
                            _drop(uploads_by_client, cid)
                    else:
                        try:
                            resp = await call_next(request)
                            if cfg.max_rejects_per_hour and resp.status_code in (400, 408, 413, 415, 422):
                                rejects.record(cid)
                        finally:
                            upload_sem.release()
                            with uploads_lock:
                                _drop(uploads_by_client, cid)
            elif te:
                resp = E(411, "length_required", "Content-Length is required")
            elif cl and (not cl.isdigit() or int(cl) > cfg.max_json_body_bytes):
                resp = E(413, "too_large", f"request body is larger than {cfg.max_json_body_bytes // 1000} KB")
        if resp is None:
            resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if "content-security-policy" not in resp.headers:
            resp.headers["Content-Security-Policy"] = PAGE_CSP
        return resp

    app.add_middleware(UploadDeadline, path="/api/jobs", seconds=cfg.upload_deadline_s)
    app.add_middleware(HeadMiddleware)           # added last = outermost: HEAD is served as GET, minus the body

    # ---- pages -------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/p/{pid}", include_in_schema=False)
    def project_page(pid: str):
        return FileResponse(STATIC / "project.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- config and showcases ----------------------------------------------------------------
    def cap_status() -> dict:
        """The caps for display; a ledger that cannot be read shows as no budget left (the caps close then too)."""
        try:
            return guard.status()
        except Exception:
            logging.getLogger("etho.app").exception("could not read the spend ledger")
            return {"tag": cfg.tag, "daily_cap_usd": guard.daily_cap(), "spent_24h_usd": None,
                    "daily_remaining_usd": 0.0, "total_cap_usd": cfg.total_cap_usd, "spent_total_usd": None,
                    "total_remaining_usd": 0.0 if cfg.total_cap_usd is not None else None, "reserved_usd": None,
                    "remaining_usd": 0.0, "ledger_error": True}

    def capped_by() -> str | None:
        """'daily_cap' / 'total_cap' when not even the smallest job fits, else None."""
        try:
            room, which = guard.headroom()
        except Exception:
            return "daily_cap"
        return which if room < cfg.estimate_job_usd(1.0, 1) else None

    def is_admin(request: Request) -> bool:
        """The operator's view: a visitor holding a valid access cookie, or the access code in X-Etho-Admin (checked
        and throttled like any code entry)."""
        # In open mode `access.valid` is true for everyone, so the operator view needs a real cookie or the header.
        if (access.valid_cookie if cfg.open else access.valid)(request.cookies.get(COOKIE)):
            return True
        hdr = request.headers.get("x-etho-admin")
        if not hdr:
            return False
        try:
            access.attempt(who(request), hdr)
            return True
        except Refused:
            return False

    def live_view(request: Request) -> dict:
        st = live.snapshot()
        granted = access.valid(request.cookies.get(COOKIE))
        capped = capped_by() if st["mode"] == "live" else None
        message = {"killed": fr["killed"], "expired": fr["expired"],
                   "read-only": fr["read_only"]}.get(st["mode"], fr.get(capped or "", ""))
        return {"mode": st["mode"], "live_until": st["live_until"], "capped": capped, "message": message,
                "access_required": st["access_required"], "access_granted": granted,
                "can_submit": st["mode"] == "live" and granted and capped is None}

    @app.get("/api/config")
    def api_config(request: Request):
        d = default_definitions(cfg)
        cap = cap_status()
        sandbox = {"mode": live.sandbox().get("mode") if cfg.public else isolation_status_safe().get("mode")}
        return {"sandbox": sandbox, "live": live_view(request),
                "limits": {"max_upload_mb": cfg.max_upload_mb, "max_duration_s": cfg.max_duration_s,
                           "extensions": [".mp4", ".mov", ".avi"], "max_behaviors": cfg.max_behaviors,
                           "rate_limit_jobs": cfg.rate_limit_jobs, "rate_window_s": cfg.rate_window_s,
                           "max_in_flight": cfg.max_in_flight or None,
                           "consent_required": cfg.consent_required,
                           "consent_text": CONSENT_TEXT if cfg.consent_required else None},
                "visitor": {"runs_used": limiter.usage(who(request)), "runs_limit": cfg.rate_limit_jobs,
                            "window_s": cfg.rate_window_s},
                "queue": {"in_flight": manager.in_flight(), "waiting": manager.queue_len()},
                "retention_hours": cfg.retention_hours,
                "defaults": d,
                "text": {"tagline": cfg.tagline, "tile_title": cfg.tile_title, "tile_value": cfg.tile_value,
                         "tile_sub": cfg.tile_sub},
                "cap": {"cap_usd": cap["daily_cap_usd"], "spent_today_usd": cap["spent_24h_usd"],
                        "remaining_usd": cap["remaining_usd"], "total_cap_usd": cfg.total_cap_usd,
                        "total_remaining_usd": cap["total_remaining_usd"]},
                "estimate": {"sam_usd_per_s": cfg.est_sam_usd_per_s, "tier1_usd_per_s": cfg.est_tier1_usd_per_s,
                             "fixed_usd": cfg.est_fixed_usd, "per_behavior_usd": cfg.est_per_behavior_usd}}

    # ---- access code, health ------------------------------------------------------------------
    @app.post("/api/access")
    async def api_access(request: Request):
        if not access.required:
            return E(400, "no_code_needed", "this server does not ask for an access code")
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return E(400, "bad_json", "body must be JSON")
        code = payload.get("code") if isinstance(payload, dict) else None
        client = who(request)
        try:
            token = access.attempt(client, code)
        except Refused as e:
            log = logging.getLogger("etho.app.access")        # never the code itself, only who and what happened
            log.warning("access code refused for %s: %s", client, e.code)
            if (d := access.delay_for(e)) > 0:                  # slows a guesser; the right code is never delayed
                await asyncio.sleep(d)
            return refuse(e)
        logging.getLogger("etho.app.access").info("access code accepted for %s", client)
        resp = JSONResponse({"granted": True, "expires_in_s": cfg.cookie_ttl_s})
        resp.set_cookie(COOKIE, token, max_age=int(cfg.cookie_ttl_s), httponly=True, samesite="strict", path="/",
                        secure=cfg.public or request.url.scheme == "https")
        return resp

    @app.post("/api/access/logout")
    def api_access_logout():
        resp = JSONResponse({"granted": False})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/health")
    def api_health(request: Request):
        """Anyone gets the mode and the budget left. The operator (a valid access cookie, or the code in the
        `X-Etho-Admin` header) also gets reasons, sandbox, queue, retention and how the client address looks."""
        st = live.snapshot()
        cap = cap_status()
        out = {"ok": True, "mode": st["mode"], "live_until": st["live_until"],
               "caps": {"remaining_usd": cap["remaining_usd"], "daily_remaining_usd": cap["daily_remaining_usd"],
                        "total_remaining_usd": cap["total_remaining_usd"]}}
        if cfg.open:
            out["open"] = True
        if not is_admin(request):
            return out
        who_key = who(request)
        shared = shared_bucket(who_key)
        out.update({"reasons": st["reasons"], "access_required": st["access_required"],
                    "access_problem": access.problem(), "sandbox": live.sandbox(), "caps": cap,
                    "queue": manager.queue_len(),
                    "running": any(j.status == "running" for j in list(manager.jobs.values())),
                    "retention": retention.last, "retention_hours": cfg.retention_hours,
                    "client_seen_as": who_key,
                    "shared_client_bucket": shared,
                    "warnings": ([SHARED_BUCKET_WARNING] if shared else []),
                    "ledger_bad_rows": ledger.bad_rows, "ledger_unreadable": ledger.unreadable,
                    "time": (now() if now else datetime.now(timezone.utc)).isoformat()})
        return out

    @app.get("/api/showcases")
    def api_showcases():
        return {"showcases": list_showcases(cfg)}

    # ---- submit ------------------------------------------------------------------------------
    @app.post("/api/jobs")
    def api_submit(request: Request, video: Annotated[UploadFile, File()], definitions: Annotated[str, Form()],
                   subject: Annotated[str, Form()] = "", partner: Annotated[str, Form()] = "",
                   consent: Annotated[str, Form()] = ""):
        if (gated := spend_refusal(request)) is not None:     # also checked here: the handler is safe on its own
            raise gated
        # Open mode: the visitor must have said they may upload this video and that it shows no people. Checked before
        # the file is read, written or probed, so a refused upload costs nothing and leaves nothing behind.
        if cfg.consent_required and consent.strip().lower() not in _TRUE:
            raise ValidationError("the consent field is missing: confirm that you have the right to upload this "
                                  "video and that it contains no people", 400, "consent_required")
        ext = check_extension(video.filename or "")
        try:
            raw_defs = json.loads(definitions)
        except json.JSONDecodeError as e:
            raise ValidationError("definitions must be JSON") from e
        defs = check_definitions(raw_defs, cfg)
        subject = check_text(subject, "subject", "the resident animal")
        partner = check_text(partner, "partner", "the other animal")
        if time.monotonic() - last_cleanup[0] > 600:
            last_cleanup[0] = time.monotonic()
            manager.cleanup()
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
            if not probe_sem.acquire(timeout=15):
                raise Refused("too many videos are being inspected; try again in a moment", 503, "busy")
            try:
                # ffprobe (under bwrap when public): real frame count, duration, decodable; nothing spent
                info = check_video(dest, cfg, confine=cfg.public)
            finally:
                probe_sem.release()
            est = cfg.estimate_job_usd(info.duration_s, len(defs))
            cid = who(request)
            with admit_lock:
                limiter.check(cid)
                guard.check(est, manager.outstanding_usd())
                limiter.record(cid)
                inp = {"filename": re.sub(r"[^\w.\- ]", "_", os.path.basename(video.filename or "upload"))[:80],
                       "definitions": defs, "subject": subject, "partner": partner, "public_data": False,
                       "video": {**info.as_dict(), "name": os.path.basename(video.filename or "upload")[:80]},
                       "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "est_cost_usd": est}
                if cfg.consent_required:
                    inp["consent"] = True
                write_json(base / "input.json", inp)
                try:
                    # reserves the spend ceiling
                    manager.submit(Job(jid, jid, "analyze", base, est_cost_usd=est, client=cid))
                except (QueueFull, Refused):
                    limiter.release(cid)
                    raise
        except BaseException as e:
            shutil.rmtree(base, ignore_errors=True)
            if isinstance(e, ClientBusy):
                raise Refused(str(e), 429, "client_busy") from e
            if isinstance(e, QueueFull):
                raise Refused(str(e), 503, "queue_full") from e
            raise
        if cfg.consent_required:      # only the fact that it was given, never who gave it
            logging.getLogger("etho.app").info("upload consent given for job %s", jid)
        return {"id": jid, "project_id": jid, "url": f"/p/{jid}", "est_cost_usd": est,
                "queue_position": manager.queue_position(manager.jobs[jid])}

    # ---- jobs --------------------------------------------------------------------------------
    def job_or_404(jid: str) -> Job | None:
        return manager.jobs.get(jid) if ID_RE.match(jid) else None

    @app.get("/api/jobs/{jid}")
    def api_job(jid: str):
        j = job_or_404(jid)
        return snap(j) if j else E(404, "not_found", "no such job")

    @app.post("/api/jobs/{jid}/cancel")
    def api_cancel(jid: str):
        return {"cancelled": manager.cancel(jid)} if job_or_404(jid) else E(404, "not_found", "no such job")

    @app.get("/api/jobs/{jid}/events")
    async def api_events(jid: str, request: Request):
        j = job_or_404(jid)
        if not j:
            return E(404, "not_found", "no such job")
        cid = who(request)
        with sse_lock:
            if sse_open.get(cid, 0) >= cfg.max_sse_per_client:
                return E(429, "too_many_streams", "too many open progress streams from this client")
            sse_open[cid] = sse_open.get(cid, 0) + 1

        async def gen():
            """No threads: poll the job's version counter in the event loop. A snapshot is sent on every state
            change and at least once a second (so elapsed time and cost keep moving)."""
            last_v, last_t, t0 = -1, 0.0, time.monotonic()
            try:
                while True:
                    v = j.version                        # read before the snapshot: a change after it is re-sent
                    now = time.monotonic()
                    if v != last_v or now - last_t >= 1.0:
                        sn = snap(j)
                        terminal = sn["status"] in TERMINAL    # one source for status and the fields sent with it
                        yield sse("end" if terminal else "progress", sn, sn["version"])
                        last_v, last_t = v, now
                        if terminal:
                            return
                    if now - t0 > SSE_MAX_S or await request.is_disconnected():
                        return
                    await asyncio.sleep(0.25)
            finally:
                with sse_lock:
                    sse_open[cid] = max(0, sse_open.get(cid, 1) - 1)
                    if not sse_open[cid]:
                        del sse_open[cid]

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- projects ----------------------------------------------------------------------------
    def project_or_none(pid: str) -> Project | None:
        return resolve_project(cfg, pid)

    def version_arg(v: int | None) -> int:
        return 0 if v is None or v < 0 else v          # the original unless ?v= says otherwise

    @app.get("/api/projects/{pid}")
    def api_project(pid: str):
        p = project_or_none(pid)
        j = manager.jobs.get(pid)
        if p is None:
            return E(404, "not_found", "no such project")
        res = p.result()
        if res is None:
            if j is None:
                return E(404, "not_found", "no such project")
            return {"id": pid, "status": j.status, "job": snap(j), "error": None if cfg.public else j.error}
        versions = [res["versions"][0]] + [r for n in p.revision_numbers() if (r := p.revision(n))]
        active = manager.active_for(pid)
        last_rev = [x for x in manager.jobs.values() if x.project_id == pid and x.kind == "revise"]
        last_rev.sort(key=lambda x: x.rev or 0)
        out = {k: v for k, v in res.items() if k != "versions"}
        out.update({"planner": PG.planner_view(res.get("plan")), "status": "done", "versions": versions,
                    "latest_version": versions[-1]["v"],
                    "active_job": snap(active) if active else None,
                    "last_revision_job": snap(last_rev[-1]) if last_rev else None})
        return out

    def versioned_file(pid: str, kind: str, v: int | None):
        p = project_or_none(pid)
        if p is None or p.result() is None:
            return None, None
        f = p.version_file(version_arg(v), kind)
        return (p, f) if f.exists() else (p, None)

    @app.get("/api/projects/{pid}/overlay")
    def api_overlay(pid: str, v: int | None = None):
        p, f = versioned_file(pid, "overlay", v)
        if f is None:
            return E(404, "not_found", "no overlay video for this version")
        return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "no-cache"})

    @app.get("/api/projects/{pid}/report")
    def api_report(pid: str, v: int | None = None):
        p, f = versioned_file(pid, "report", v)
        if f is None:
            return E(404, "not_found", "no report for this version")
        return HTMLResponse(f.read_text(), headers={"Content-Security-Policy": REPORT_CSP})

    @app.get("/api/projects/{pid}/strip/{name}")
    def api_strip(pid: str, name: str):
        p = project_or_none(pid)
        if p is None or not STRIP_RE.match(name):
            return E(404, "not_found", "no such strip")
        for d in (p.strips_dir, p.base / "strips"):
            if (d / name).is_file():
                return FileResponse(d / name, media_type="image/jpeg")
        return E(404, "not_found", "no such strip")

    @app.get("/api/projects/{pid}/audit")
    def api_audit(pid: str, v: int | None = None):
        p = project_or_none(pid)
        res = p.result() if p else None
        if res is None:
            return E(404, "not_found", "no such project")
        return audit_view(p, res, version_arg(v), ledger)

    def version_record(p: Project, res: dict, v: int) -> dict | None:
        return res["versions"][0] if v == 0 else p.revision(v)

    @app.get("/api/projects/{pid}/trace")
    def api_trace(pid: str, v: int | None = None, behavior: str | None = None, s0: int | None = None,
                  s1: int | None = None):
        """Without `behavior`: every tier-1 window and how it was treated (to mark the ethogram). With `behavior`,
        `s0`, `s1` (a final bout, in samples): the steps that produced it (rule result, frame-strip verdicts, which
        step decided the label)."""
        p = project_or_none(pid)
        res = p.result() if p else None
        if res is None:
            return E(404, "not_found", "no such project")
        ver = version_arg(v)
        rec = version_record(p, res, ver)
        if rec is None:
            return E(404, "not_found", "no such version")
        names = res["behaviors"]["names"]
        verdicts = PG.read_verdicts(p.version_file(ver, "verdicts"), PG.allow_for(names))
        if behavior is None:
            return {"version": ver, "fps": res["fps"], **PG.trace_index(verdicts)}
        if behavior not in names or s0 is None or s1 is None or s1 <= s0:
            return E(400, "bad_bout", "behavior, s0 and s1 (samples, s1 > s0) must name a bout")
        return {"version": ver, "fps": res["fps"], **PG.bout_trace(rec, verdicts, behavior, s0, s1, res["fps"])}

    # ---- sample clip for judges --------------------------------------------------------------
    def sample_meta() -> dict | None:
        """The sample clip's record, only when the clip file itself is there (made by scripts/make_sample_clip.py)."""
        m = read_json(cfg.sample_dir / SAMPLE_JSON)
        if not isinstance(m, dict) or not (cfg.sample_dir / SAMPLE_FILE).is_file():
            return None
        return m

    @app.get("/api/sample")
    def api_sample():
        m = sample_meta()
        d = default_definitions(cfg)
        return {"available": m is not None, "attribution": SAMPLE_ATTRIBUTION, "links": SAMPLE_LINKS,
                "trimmed_note": SAMPLE_TRIMMED, "clip": m, "definitions": d["behaviors"],
                "subject": d["subject"], "partner": d["partner"],
                "download": "/api/sample/clip" if m else None,
                "message": None if m else "The sample clip has not been prepared on this machine "
                                          "(scripts/make_sample_clip.py)."}

    @app.get("/api/sample/clip")
    def api_sample_clip():
        if sample_meta() is None:
            return E(404, "not_found", "no sample clip on this machine")
        return FileResponse(cfg.sample_dir / SAMPLE_FILE, media_type="video/mp4", filename=SAMPLE_FILE,
                            headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/api/projects/{pid}/revise")
    async def api_revise(pid: str, request: Request):
        # A revision (of a showcase too) makes model calls that cost money: live use and the code are required.
        if (gated := spend_refusal(request)) is not None:
            return refuse(gated)
        if project_or_none(pid) is None:
            return E(404, "not_found", "no such project")
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return E(400, "bad_json", "body must be JSON")
        if not isinstance(payload, dict):
            return E(400, "bad_json", "body must be a JSON object")
        return await asyncio.to_thread(revise_sync, request, pid, payload)

    def revise_sync(request: Request, pid: str, payload: dict):
        cid = who(request)
        with admit_lock:                      # check-then-create must be atomic: one revision at a time per project
            p = project_or_none(pid)
            res = p.result() if p else None
            if res is None:
                return E(404, "not_found", "no such project")
            if manager.active_for(p.id):
                return E(409, "busy", "a job is already running for this video")
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
            est = cfg.estimate_revise_usd(res["video"]["duration_s"], len({c["behavior"] for c in cumulative}))
            limiter.check(cid)
            guard.check(est, manager.outstanding_usd())
            if manager.queue_len() >= cfg.max_queue:
                raise Refused(f"{manager.queue_len()} jobs are already waiting; try again later", 503, "queue_full")
            forked = None
            if p.kind == "showcase":              # a visitor's private, revisable copy of the precomputed run
                forked = p = fork_showcase(cfg, p, new_job_id())
            n = p.next_revision()
            d = p.work / "revisions" / str(n)
            d.mkdir(parents=True, exist_ok=True)
            job = Job(f"{p.id}-r{n}", p.id, "revise", d, rev=n, est_cost_usd=est, client=cid,
                      params={"corrections": cumulative, "base_version": latest,
                              "user_corrections": prior_user + [c.model_dump() for c in cs]})
            try:
                manager.submit(job)               # reserves the spend ceiling
            except (QueueFull, Refused) as e:
                shutil.rmtree(forked.base if forked else d, ignore_errors=True)
                if isinstance(e, Refused):
                    raise
                if isinstance(e, ClientBusy):
                    raise Refused(str(e), 429, "client_busy") from e
                raise Refused(str(e), 503, "queue_full") from e
            limiter.record(cid)
        return {"job_id": job.id, "project_id": p.id, "rev": n, "est_cost_usd": est,
                "n_corrections": len(cumulative), "forked": forked is not None}

    return app


def _drop(d: dict[str, int], key: str) -> None:
    """One fewer in-flight item for `key`; the entry goes when it reaches zero."""
    d[key] = d.get(key, 1) - 1
    if d[key] <= 0:
        del d[key]


def isolation_status_safe() -> dict:
    try:
        return isolation_status()
    except SandboxUnavailable as e:
        return {"mode": "unavailable", "detail": str(e)[:200]}


def audit_view(p: Project, res: dict, v: int, ledger: AppLedger) -> dict:
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
    prov = spec.get("provenance", {})
    dets = [{"behavior": d["behavior"], "model": d.get("model"), "prompt_hash": d.get("prompt_hash"),
             "source": prov.get(d["behavior"]), "code": d["code"],
             "calls": [{k: c[k] for k in CALL_KEYS if k in c} for c in d.get("calls", [])]}
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
            s = stage_of_caller(str(r.get("caller")))
            by_stage[s] = by_stage.get(s, 0.0) + float(r.get("cost_usd", 0.0) or 0.0)
            n_calls += 1
    return {"version": v, "plan": res.get("plan"), "detectors": dets, "calls": calls, "verdicts": verdicts,
            "tracking": {k: track.get(k) for k in ("phrase", "timings", "cost_usd", "cache_hits", "app_chunk_s")},
            "definitions": res["behaviors"]["definitions"], "subject": res.get("subject"),
            "partner": res.get("partner"),
            "ledger": {"rows": n_calls, "by_stage_usd": {k: round(x, 6) for k, x in by_stage.items()},
                       "total_usd": round(sum(by_stage.values()), 6)},
            "sandbox": {"mode": isolation_status_safe().get("mode")},
            "note": res.get("audit_note")}


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(description="Etho web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--forwarded-allow-ips", default="127.0.0.1",
                    help="proxy addresses uvicorn trusts for X-Forwarded-For (rate limits key on the rewritten "
                         "client address); the default trusts only a proxy on this machine")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = AppConfig()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(cfg.work_dir)  # tier 1 reads cache/chunks relative to the working directory (public: the app's own)
    if cfg.public:          # the planner's sampled frames and upload spools go where retention sweeps them
        cfg.tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ["TMPDIR"] = str(cfg.tmp_dir)
        tempfile.tempdir = str(cfg.tmp_dir)
    app = create_app(cfg)
    st = app.state.live.snapshot()
    logging.getLogger("etho.app").info(
        "%smode %s %s; tag %s; caps $%.2f per 24 h (%s in the judging window) and %s in total; retention %g h; "
        "live until %s; per-visitor limit %s runs per %g h; at most %s jobs in flight",
        "OPEN (no access code) " if cfg.open else "", st["mode"], st["reasons"], cfg.tag, cfg.daily_cap_usd,
        "unchanged" if cfg.judging_daily_cap_usd is None else f"${cfg.judging_daily_cap_usd:.2f}",
        "no" if cfg.total_cap_usd is None else f"${cfg.total_cap_usd:.2f}", cfg.retention_hours, st["live_until"],
        cfg.rate_limit_jobs, cfg.rate_window_s / 3600, cfg.max_in_flight or "any number of")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info", proxy_headers=True,
                forwarded_allow_ips=a.forwarded_allow_ips)


if __name__ == "__main__":
    main()
