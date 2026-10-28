"""App configuration. Everything that guards spend or abuse is a value here, overridable by env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_EXT = (".mp4", ".mov", ".avi")


def _env(name: str, default, cast=str):
    v = os.environ.get(name)
    return default if v is None or v == "" else cast(v)


@dataclass
class AppConfig:
    root: Path = REPO_ROOT
    # Gateway tag for every model call the app makes. The live demo will switch to "demo-reserve"
    # (the gateway refuses it before Nov 3).
    tag: str = field(default_factory=lambda: _env("ETHO_APP_TAG", "dev"))
    # Upload limits (checked before anything is spent).
    max_upload_mb: float = field(default_factory=lambda: _env("ETHO_APP_MAX_UPLOAD_MB", 200.0, float))
    max_duration_s: float = field(default_factory=lambda: _env("ETHO_APP_MAX_DURATION_S", 300.0, float))
    max_behaviors: int = 8
    # Per-day app spend cap (UTC day) and the pre-estimate used to refuse a job that would cross it.
    daily_cap_usd: float = field(default_factory=lambda: _env("ETHO_APP_DAILY_CAP_USD", 5.0, float))
    est_sam_usd_per_s: float = 0.02      # SAM at 10 fps (spec rate; measured is about 10x lower)
    est_tier1_usd_per_s: float = 0.02    # tier-1 strips (spec rate; measured is about 17x lower)
    est_fixed_usd: float = 0.05
    est_per_behavior_usd: float = 0.06   # one high-effort synthesis call per behavior
    est_revise_per_behavior_usd: float = 0.15
    # Per-client rate limit (analysis and revision jobs both count).
    rate_limit_jobs: int = field(default_factory=lambda: _env("ETHO_APP_RATE_JOBS", 3, int))
    rate_window_s: float = 3600.0
    trust_proxy: bool = field(default_factory=lambda: _env("ETHO_APP_TRUST_PROXY", "0") == "1")
    max_corrections: int = 40
    max_queue: int = 5
    # Showcase runs are CalMS21 (public data); revisions on them may use -contributor models.
    showcase_public_data: bool = True

    @property
    def data_dir(self) -> Path:
        return self.root / "data" / "app"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def showcase_dir(self) -> Path:
        return self.root / "data" / "showcase"

    @property
    def showcase_state_dir(self) -> Path:
        return self.data_dir / "showcase_state"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ops" / "spend.jsonl"

    def estimate_job_usd(self, duration_s: float, n_behaviors: int) -> float:
        return round(duration_s * (self.est_sam_usd_per_s + self.est_tier1_usd_per_s) + self.est_fixed_usd
                     + n_behaviors * self.est_per_behavior_usd, 4)

    def estimate_revise_usd(self, duration_s: float, n_affected: int) -> float:
        return round(self.est_fixed_usd + n_affected * self.est_revise_per_behavior_usd
                     + duration_s * self.est_tier1_usd_per_s, 4)
