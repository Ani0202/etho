"""Paths and secrets. Keys are read at runtime from ~/.config/etho/secrets.env and never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECRETS = Path.home() / ".config" / "etho" / "secrets.env"

ENV_KEYS = {
    "meta": "META_MODEL_API_KEY",
    "baseten": "BASETEN_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "together": "TOGETHER_API_KEY",
}

BASE_URLS = {
    "meta": "https://api.meta.ai/v1",
    "baseten": "https://inference.baseten.co/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "together": "https://api.together.xyz/v1",
}


def load_secrets(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. Environment variables override the file."""
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                out[k.strip().removeprefix("export ").strip()] = v
    for k in ENV_KEYS.values():
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


@dataclass
class GatewayConfig:
    root: Path = field(default_factory=lambda: Path(os.environ.get("ETHO_ROOT", REPO_ROOT)))
    secrets_path: Path = field(
        default_factory=lambda: Path(os.environ.get("ETHO_SECRETS", DEFAULT_SECRETS)))
    base_urls: dict[str, str] = field(default_factory=lambda: dict(BASE_URLS))
    timeout_s: float = 600.0
    max_retries: int = 5

    @property
    def ops_dir(self) -> Path:
        return self.root / "ops"

    @property
    def ledger_path(self) -> Path:
        return self.ops_dir / "spend.jsonl"

    @property
    def alerts_path(self) -> Path:
        return self.ops_dir / "alerts.jsonl"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache" / "gateway"

    def api_key(self, provider: str) -> str | None:
        return load_secrets(self.secrets_path).get(ENV_KEYS[provider])
