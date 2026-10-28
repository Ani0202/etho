from datetime import datetime, timezone

import httpx
import pytest

from etho.gateway import Gateway, GatewayConfig


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in ("META_MODEL_API_KEY", "BASETEN_API_KEY", "FIREWORKS_API_KEY", "TOGETHER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("META_MODEL_API_KEY=test-meta\nBASETEN_API_KEY=test-bt\nFIREWORKS_API_KEY=test-fw\n")
    return GatewayConfig(root=tmp_path, secrets_path=secrets, max_retries=3)


@pytest.fixture
def make_gw(cfg):
    sleeps = []

    def _make(handler, now=None):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        gw = Gateway(cfg, client=client, sleep=sleeps.append,
                     now=now or (lambda: datetime(2026, 10, 28, tzinfo=timezone.utc)))
        gw.sleeps = sleeps
        return gw
    return _make
