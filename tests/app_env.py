"""Test app factory: an app rooted in tmp_path with the fake gateway and synthetic pipeline stages."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from tests.app_helpers import FPS, FakeGateway, make_video, patch_pipeline

DEFS = {"investigation": "The resident is close to the partner and sniffing it.",
        "attack": "The resident bites or tussles with the partner at high intensity."}


def make_app(tmp_path, monkeypatch, *, seconds: float = 6.0, cfg_kw: dict | None = None,
             backend_kw: dict | None = None, now=None, clock=None, gw_kw: dict | None = None):
    """Returns (TestClient, namespace with cfg, video, n, gw, app, manager)."""
    from fastapi.testclient import TestClient

    from etho.app.config import AppConfig
    from etho.app.server import create_app

    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir(exist_ok=True)
    src = Path(__file__).resolve().parents[1] / "configs" / "calms21_definitions.json"
    (tmp_path / "configs" / "calms21_definitions.json").write_text(src.read_text())
    cfg = AppConfig(root=tmp_path, **(cfg_kw or {}))
    video = make_video(tmp_path / "in.mp4", seconds)
    n = int(seconds * FPS)
    gw = FakeGateway(cfg.ledger_path, **(gw_kw or {}))
    patch_pipeline(monkeypatch, video, n, **(backend_kw or {}))
    app = create_app(cfg, gateway_factory=lambda: gw, now=now, clock=clock)
    return TestClient(app), SimpleNamespace(cfg=cfg, video=video, n=n, gw=gw, app=app, manager=app.state.manager)


def post_job(client, video: Path, defs: dict | None = None, filename: str | None = None, headers: dict | None = None,
             **form):
    body = {"definitions": json.dumps(DEFS if defs is None else defs), "subject": "the resident",
            "partner": "the intruder", **form}
    with open(video, "rb") as f:
        return client.post("/api/jobs", data=body, headers=headers,
                           files={"video": (filename or video.name, f, "video/mp4")})


def wait_terminal(manager, jid: str, timeout: float = 90.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = manager.jobs.get(jid)
        if j and j.status in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {jid} did not finish: {manager.jobs.get(jid)}")


def no_jobs(c) -> bool:
    return not c.cfg.jobs_dir.exists() or not any(c.cfg.jobs_dir.iterdir())


def wait_for(cond, timeout: float = 20.0, what: str = "condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    raise TimeoutError(f"timed out waiting for {what}")
