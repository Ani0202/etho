"""Dev tool: run the real app against synthetic pipeline stages and a fake gateway (no network, no spend).

  uv run python -m tests.mock_server --port 8766

Uploads go through the real validation, queue, SSE, overlay and report code; only planner / SAM / synthesis /
tier-1 / revise are stand-ins. Used for screenshots and manual UI checks.
"""

import argparse
import tempfile
from pathlib import Path

import pytest
import uvicorn

from etho.app.config import AppConfig
from etho.app.server import create_app
from tests.app_helpers import FPS, FakeGateway, make_video, patch_pipeline


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--delay", type=float, default=0.8, help="seconds per fake model call")
    a = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="etho_mock_"))
    (root / "configs").mkdir()
    src = Path(__file__).resolve().parents[1] / "configs" / "calms21_definitions.json"
    (root / "configs" / "calms21_definitions.json").write_text(src.read_text())
    import os
    os.chdir(root)
    cfg = AppConfig(root=root, rate_limit_jobs=100)
    video = make_video(root / "in.mp4", a.seconds)
    patch_pipeline(pytest.MonkeyPatch(), video, int(a.seconds * FPS))
    print("sample video to upload:", video, "root:", root, flush=True)
    uvicorn.run(create_app(cfg, gateway_factory=lambda: FakeGateway(cfg.ledger_path, cost=0.012, delay=a.delay)),
                host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
