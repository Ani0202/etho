"""Retention: everything made for an upload is deleted `cfg.retention_hours` (default 24 h) after the upload.

A periodic timer (`Retention`, not only startup or submit) removes, for every project older than that:
  1. the project directory: upload, clean video, tracks, strips, overlay, report, revisions, calls, job.json;
  2. the project's chunk videos in `cache/chunks` (recorded in `<project>/cache_refs.json` when they were cut);
  3. the project's entries in the gateway response cache (SAM output with masks, planner / adjudication replies,
     the Files API id), found through the `request_key` of the project's rows in the shared ledger. Only a private
     cache (`cfg.gateway_cache_root`, the public default) is touched: the shared repo cache also holds the
     benchmark's entries, which this app must never delete;
  4. the Meta Files API copies of the SAM clips (`Gateway.delete_file`, `DELETE /v1/files/{id}`). They are normally
     deleted right after tracking (metafiles.py); this pass retries what is left and lists only the ids it could not
     delete in `<data>/meta_files_undeleted.jsonl` (ids only, no content).
and, as a backstop for orphans (a crash between steps), sweeps the private gateway cache, the app's own chunk dir and
the app's temp dir for anything older than the retention window.

What this cannot delete, and says so:
  * A Files API copy whose delete failed (the list above), and anything Meta or Fireworks keep in their own logs.
  * Ledger rows in ops/spend.jsonl (job id, time, model, token counts, cost, SAM phrase, request hash). They hold no
    video, prompt or answer text and are the audit trail the spend caps are computed from.
  * Detector code in the detector cache: code written for a definition, containing no video or user data. The job id
    inside an entry is never shown to another visitor.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from etho.gateway.cache import DiskCache

from .config import AppConfig
from .jobgw import AppLedger, caller_for
from .metafiles import delete_job_files, record_undeleted
from .store import read_json, write_json

log = logging.getLogger("etho.app.retention")
CHUNK_REFS = "cache_refs.json"
KEY_RE = re.compile(r"^[0-9a-f]{64}$")
TMP_MAX_AGE_S = 3600.0
_refs_lock = threading.Lock()


# ---- recording what a project put in shared places ----------------------------------------------------------
def note_chunk_dirs(cfg: AppConfig, project_base: Path, chunk_paths, *, src: Path) -> None:
    """Remember the chunk directories cut for a project so retention can delete them with it. Only chunks cut from
    the project's OWN video (a file inside its directory) are recorded: a revision of a showcase copy cuts chunks of
    the showcase's source, a directory other visitors share, and that is never recorded or deleted here."""
    if not _within(Path(src), Path(project_base)) or Path(src).is_symlink():
        return
    dirs = {str(Path(p).resolve().parent) for p in chunk_paths}
    if not dirs:
        return
    with _refs_lock:
        f = Path(project_base) / CHUNK_REFS
        cur = read_json(f, {}) or {}
        have = set(cur.get("chunk_dirs", []))
        if dirs - have:
            write_json(f, {"chunk_dirs": sorted(have | dirs)})


def project_created(d: Path) -> float | None:
    """When the upload happened: the earliest `created_at` of the project's jobs (its analysis and revisions)."""
    ts = []
    for p in [d / "job.json", *d.glob("revisions/*/job.json")]:
        j = read_json(p)
        if isinstance(j, dict) and isinstance(j.get("created_at"), (int, float)) and j["created_at"] > 0:
            ts.append(float(j["created_at"]))
    return min(ts) if ts else None


def _newest_mtime(d: Path) -> float:
    m = d.stat().st_mtime
    for p in d.rglob("*"):
        try:
            if not p.is_symlink():
                m = max(m, p.stat().st_mtime)
        except OSError:
            pass
    return m


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


# ---- the gateway cache ---------------------------------------------------------------------------------------
def cache_files(root: Path, key: str) -> list[Path]:
    """Cache files for one request key (DiskCache layout), plus its lock file."""
    return [root / key[:2] / f"{key}.json.gz", root / key[:2] / f"{key}.txt.gz", root / "locks" / f"{key}.lock"]


def _meta_file_id(root: Path, key: str) -> str | None:
    try:
        with gzip.open(root / key[:2] / f"{key}.json.gz", "rt") as f:
            v = json.load(f)
        return str(v["id"]) if isinstance(v, dict) and v.get("id") else None
    except (OSError, ValueError, KeyError):
        return None


def project_job_ids(d: Path) -> set[str]:
    """Ids of the jobs a project directory holds: its analysis (named like the directory) and its revisions."""
    ids = {d.name}
    ids |= {f"{d.name}-r{x.name}" for x in (d / "revisions").glob("*") if x.name.isdigit()}
    return ids


def key_in_use_by_other_projects(cfg: AppConfig, ledger: AppLedger, d: Path, extra_job_ids, key: str) -> bool:
    """Does a project OTHER than `d` still reference this request key? Every other project directory counts (it is
    not yet purged, so it is unexpired, busy, or later in the same pass), plus `extra_job_ids` (callable or iterable:
    jobs the manager knows that belong to other projects whose directory still exists). Cache entries are content-
    addressed, so two projects with the same inputs share them. Evaluated afresh on every call: callers hold the
    key's lock, and a job that registered the key since an earlier look has its ledger row by then."""
    ids = set(extra_job_ids() if callable(extra_job_ids) else extra_job_ids)
    if cfg.jobs_dir.is_dir():
        for o in cfg.jobs_dir.iterdir():
            if o.is_dir() and o.name != d.name:
                ids |= project_job_ids(o)
    return any(r.get("request_key") == key for jid in ids for r in ledger.job_rows(jid))


def purge_project(cfg: AppConfig, ledger: AppLedger, d: Path, job_ids: list[str], gw=None,
                  other_job_ids=()) -> dict:
    """Delete one project's Meta Files copies (when `gw` is a gateway), chunk dirs and gateway-cache entries, then
    its directory. Order matters: the directory goes last, so a crash in between leaves it (and the record of what to
    delete) for the next pass. A cache entry (and the Meta file it points at) that a project which is not being
    purged also references is left alone: another job may be reading it or about to, and deleting it would break
    that job or make it pay again. Each key is handled under its cache lock (the one `Gateway.upload_file` holds
    while it writes its ledger row), and re-checked inside it, so a job that registered the key after an earlier
    look is seen. Lock files are never unlinked here: a job may be waiting on one, and two inodes for one key would
    let two uploads run at once (the retention sweep removes them, only while no job is busy).
    `other_job_ids`: jobs of other projects the caller knows (a callable is evaluated at each check)."""
    rep = {"project": d.name, "cache_entries": 0, "chunk_dirs": 0, "meta_files_recorded": 0, "meta_files_deleted": 0,
           "shared_keys_kept": 0}
    refs = read_json(d / CHUNK_REFS, {}) or {}
    chunk_root = cfg.chunk_cache_dir
    for c in refs.get("chunk_dirs", []):
        p = Path(c)
        # only a direct child of the app's own chunk dir (one dir per source video), never the dir itself or a path
        # that merely resolves into it
        if p.is_dir() and p.parent.resolve() == chunk_root.resolve() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
            rep["chunk_dirs"] += 1
    root = cfg.gateway_cache_root
    if root is not None and root.is_dir():
        keys: dict[str, bool] = {}          # request_key -> is a Files API upload
        for jid in job_ids:
            for r in ledger.job_rows(jid):
                k = r.get("request_key")
                if isinstance(k, str) and KEY_RE.match(k):
                    keys[k] = keys.get(k, False) or r.get("endpoint") == "/files"
        own = set(job_ids)
        kept: set[str] = set()

        def is_live(k: str) -> bool:
            live = key_in_use_by_other_projects(
                cfg, ledger, d, lambda: set(other_job_ids() if callable(other_job_ids) else other_job_ids) - own, k)
            if live:
                kept.add(k)
            return live

        if gw is not None:     # ask Meta to delete whatever the early pass did not; list only the failures
            who = caller_for(d.name, "etho.app.retention", cfg.caller_prefix)
            r = delete_job_files(cfg, ledger, gw, job_ids, caller=who,
                                 project=d.name, record_failures=True, is_live=is_live)
            rep["meta_files_deleted"], rep["meta_files_recorded"] = r["deleted"], r["failed"]
        cache = gw.cache if gw is not None else DiskCache(root)
        for k, is_file in keys.items():
            with cache.lock(k):
                if k in kept or is_live(k):   # kept by the Meta pass: never forget an id whose file was not deleted
                    continue
                if gw is None and is_file:      # no gateway to ask: keep the id so it can be deleted by hand
                    fid = _meta_file_id(root, k)
                    if fid:
                        record_undeleted(cfg, fid, d.name)
                        rep["meta_files_recorded"] += 1
                for p in cache_files(root, k)[:2]:
                    if p.exists():
                        p.unlink(missing_ok=True)
                        rep["cache_entries"] += 1
        rep["shared_keys_kept"] = len(kept)
    shutil.rmtree(d, ignore_errors=True)
    return rep


# ---- sweeps ---------------------------------------------------------------------------------------------------
def _sweep_files(root: Path, older_than_s: float, now: float) -> int:
    """Delete files under `root` last modified more than `older_than_s` ago, then empty directories."""
    n = 0
    if not root.is_dir():
        return 0
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_symlink() or p.is_file():
                if now - p.lstat().st_mtime > older_than_s:
                    p.unlink(missing_ok=True)
                    n += 1
            elif p.is_dir() and not any(p.iterdir()) and now - p.stat().st_mtime > older_than_s:
                p.rmdir()
        except OSError:
            pass
    return n


def sweep_tmp(cfg: AppConfig, now: float, max_age_s: float = TMP_MAX_AGE_S) -> int:
    """Frames the planner and the upload spool leave in the temp dir (removed whole, files and directories)."""
    d = cfg.tmp_dir
    n = 0
    if not d.is_dir():
        return 0
    for p in list(d.iterdir()):
        try:
            if p.is_dir() and not p.is_symlink():
                if now - _newest_mtime(p) > max_age_s:
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
            elif now - p.lstat().st_mtime > max_age_s:
                p.unlink(missing_ok=True)
                n += 1
        except OSError:
            pass
    return n


class Retention:
    """Runs the retention pass now (`run_once`) and on a timer (`start`)."""

    def __init__(self, cfg: AppConfig, manager, ledger: AppLedger, clock=time.time):
        self.cfg, self.manager, self.ledger, self.clock = cfg, manager, ledger, clock
        self.last: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def run_once(self, now: float | None = None, *, startup: bool = False) -> dict:
        now = self.clock() if now is None else now
        cfg = self.cfg
        with self._lock:
            removed = self.manager.cleanup(now)
            limit = cfg.retention_hours * 3600
            swept = {"gateway_cache_files": 0, "chunk_files": 0, "tmp_entries": 0, "sweeps_skipped": False}
            if self.manager.busy():       # shared dirs (caches, temp) are only swept while no job could be using them
                swept["sweeps_skipped"] = True
            else:
                if cfg.private_gateway_cache:
                    swept["gateway_cache_files"] = _sweep_files(cfg.gateway_cache_root, limit, now)
                swept["chunk_files"] = _sweep_files(cfg.chunk_cache_dir, limit, now)   # the app's own chunk dir
                swept["tmp_entries"] = sweep_tmp(cfg, now, 0.0 if startup else TMP_MAX_AGE_S)
            kept = int(getattr(self.manager, "shared_keys_kept", 0) or 0)
            self.last = {"at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                         "removed_projects": len(removed), "retention_hours": cfg.retention_hours, **swept}
            if removed or kept or any(v for v in swept.values() if v is not True):
                log.info("retention: removed %d project(s), kept %d shared cache key(s) another project uses, "
                         "swept %s", len(removed), kept, swept)
            return self.last

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop():
            while not self._stop.wait(max(0.05, self.cfg.maintenance_interval_s)):
                try:
                    self.run_once()
                except Exception:       # never let the timer die
                    log.exception("retention pass failed")

        self._thread = threading.Thread(target=loop, name="etho-retention", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
