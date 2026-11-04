"""Run one analysis job through the web app, the way a browser upload does, with a gateway of our choosing.

Used twice with the same code path: by the recorder (a gateway that answers from an existing cache and records what
it saw) and by the replay (`ReplayGateway`). It builds the real app (`create_app`: validation, spend guard, job
queue, the pipeline stages), posts the clip to `/api/jobs` in-process (no socket), and waits for the job to end.
Only the gateway and, for the replay, the label-adding backend differ from a live run.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from etho.app.backend import Backend
from etho.app.config import REPO_ROOT, AppConfig
from etho.app.jobgw import AppLedger
from etho.app.server import create_app

from .backend import ReplayBackend

DEFINITIONS = REPO_ROOT / "configs" / "calms21_definitions.json"


@dataclass
class Session:
    app: object
    client: object
    cfg: AppConfig
    job_id: str
    job: object
    out_dir: Path            # the project directory

    @property
    def result(self) -> dict:
        return json.loads((self.out_dir / "result.json").read_text())

    def close(self) -> None:
        self.client.close()
        self.app.state.manager.stop()


def load_definitions(path: Path = DEFINITIONS) -> dict:
    d = json.loads(Path(path).read_text())
    return {"definitions": d["behaviors"], "subject": d["subject"], "partner": d["partner"]}


def make_config(work_dir: Path) -> AppConfig:
    """Localhost, non-public, every knob explicit so the environment cannot change what a replay does."""
    return AppConfig(root=REPO_ROOT, data_root=Path(work_dir) / "app", work_root=Path(work_dir) / "app" / "work",
                     public=False, read_only=False, live_until=None, gateway_cache_root=None, caller_prefix="job:",
                     tag="dev", total_cap_usd=None, daily_cap_usd=5.0, max_upload_mb=200.0, max_duration_s=300.0,
                     kill_file=None, readonly_file=None, access_code_file=None)


def run_job(gateway, clip: Path, definitions: dict, work_dir: Path, *, replay: bool, say=print,
            timeout_s: float = 900.0, backend=None) -> Session:
    """Submit `clip` with `definitions` ({"definitions": {...}, "subject": ..., "partner": ...}) and wait.
    Raises RuntimeError with the job's error when it does not finish `done`."""
    from fastapi.testclient import TestClient

    work_dir, clip = Path(work_dir).resolve(), Path(clip).resolve()      # the job chdirs below
    cfg = make_config(work_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(cfg.work_dir)          # tier 1 reads cache/chunks relative to the working directory (as bin/app does)
    ledger = AppLedger(work_dir / "ops" / "spend.jsonl", cfg.caller_prefix)
    backend = backend(cfg) if backend else (ReplayBackend if replay else Backend)(cfg)
    app = create_app(cfg, backend=backend, gateway_factory=lambda: gateway, ledger=ledger)
    client = TestClient(app, base_url="http://testserver")
    with open(clip, "rb") as f:
        r = client.post("/api/jobs", data={"definitions": json.dumps(definitions["definitions"]),
                                           "subject": definitions["subject"], "partner": definitions["partner"]},
                        files={"video": (Path(clip).name, f, "video/mp4")})
    if r.status_code != 200:
        raise RuntimeError(f"the app refused the clip: HTTP {r.status_code} {r.text[:400]}")
    jid = r.json()["id"]
    manager = app.state.manager
    job, last, t0, stopped = manager.jobs[jid], None, time.monotonic(), False
    while job.status not in ("done", "failed", "cancelled"):
        if getattr(gateway, "misses", None) and not stopped:
            stopped = True                      # a call had no recording: stop the job, the run is void
            manager.cancel(jid)
            if say:
                say("  a model call has no recording; stopping")
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"job {jid} did not finish in {timeout_s:.0f} s")
        msg = job.message
        if msg != last and say:
            say(f"  ... {msg}")
            last = msg
        time.sleep(0.05)
    sess = Session(app, client, cfg, jid, job, cfg.jobs_dir / jid)
    misses = getattr(gateway, "misses", None)
    if misses:                       # whatever the job did with the failure (a stage may carry on), the run is void
        sess.close()
        raise misses[0]
    if job.status != "done":
        err = job.error or job.message
        sess.close()
        raise RuntimeError(err or f"job ended {job.status}")
    return sess
