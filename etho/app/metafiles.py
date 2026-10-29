"""Deleting the clips a job uploaded to Meta's Files API.

The tracking stage uploads each SAM chunk with `Gateway.upload_file` (free, cached by content hash; the id sits in the
gateway cache under the `request_key` of the job's `/files` ledger row). Once tracking is done nothing needs those
files, so they are deleted at once through `Gateway.delete_file` (`DELETE /v1/files/{id}`), not at the 24 h
retention pass. The retention pass tries again for anything left, and lists in `meta_files_undeleted.jsonl` only the
ids it could not delete. Deleting forgets the cache entry, so the call is idempotent.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import AppConfig
from .jobgw import AppLedger

log = logging.getLogger("etho.app.metafiles")


def upload_keys(ledger: AppLedger, job_ids: list[str]) -> list[str]:
    """Gateway-cache keys of the Files API uploads made for these jobs (not the delete rows, which have no key)."""
    keys: dict[str, None] = {}
    for jid in job_ids:
        for r in ledger.job_rows(jid):
            k = r.get("request_key")
            if r.get("endpoint") == "/files" and r.get("method") != "DELETE" and isinstance(k, str):
                keys[k] = None
    return list(keys)


def record_undeleted(cfg: AppConfig, file_id: str, project: str) -> None:
    f = cfg.data_dir / "meta_files_undeleted.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as out:
        out.write(json.dumps({"file_id": file_id, "project": project,
                              "recorded": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")


def delete_job_files(cfg: AppConfig, ledger: AppLedger, gw, job_ids: list[str], *, caller: str,
                     project: str | None = None, record_failures: bool = False,
                     is_live=None) -> dict:
    """Delete the Files API copies for these jobs through `gw` (a Gateway). Returns counts. Failures are logged and,
    with `record_failures`, appended to `meta_files_undeleted.jsonl` (the retention pass does this; the early pass
    leaves them for the retention pass to retry).

    The cache entry holds the file id and is content-addressed: another job with identical bytes shares the entry and
    the file. Each key is therefore handled under its cache lock (the lock `Gateway.upload_file` holds while it
    reads the entry and writes its ledger row), and `is_live(key)` is asked INSIDE it: a True answer (a live job
    still uses the file) leaves both the Meta file and the entry alone. A job that hit the cache before we took the
    lock has its ledger row by now; one that comes after finds no entry and uploads afresh. The delete is made
    without `request_key` (the gateway would take the same, non-reentrant lock to forget the entry), and the entry
    is forgotten here once Meta confirms. Returns also `kept`: the keys skipped because they were live."""
    rep = {"deleted": 0, "failed": 0, "kept": 0}
    for key in upload_keys(ledger, job_ids):
        fid, ok = None, False
        try:
            with gw.cache.lock(key):
                if is_live is not None and is_live(key):
                    rep["kept"] += 1
                    continue
                try:
                    rec = gw.cache.get_json(key)
                except Exception:
                    rec = None
                fid = rec.get("id") if isinstance(rec, dict) else None
                if not fid:                           # already deleted (the entry was forgotten) or never stored
                    continue
                ok = bool(gw.delete_file(str(fid), tag=cfg.tag, caller=caller, request_key=None))
                if ok:
                    gw.cache.delete_json(key)
        except Exception as e:                        # a policy refusal (tag locked, no key): not deleted
            log.warning("Meta file delete refused: %s", str(e)[:200])
            ok = False
            if not fid:
                continue
        if ok:
            rep["deleted"] += 1
        else:
            rep["failed"] += 1
            if record_failures:
                record_undeleted(cfg, str(fid), project or "")
    return rep
