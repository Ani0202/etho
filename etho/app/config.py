"""App configuration. Everything that guards spend or abuse is a value here, overridable by env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_EXT = (".mp4", ".mov", ".avi")
ET = ZoneInfo("America/New_York")
# Hosted live mode switches itself off after this moment (Aniket, 2026-10-28); read-only browsing stays up.
DEFAULT_LIVE_UNTIL = datetime(2026, 11, 10, 23, 59, 59, tzinfo=ET)
# Judging window (Aniket, 2026-10-28): the public daily cap is raised from Nov 4 00:00 ET through Nov 9 ET.
JUDGING_FROM = datetime(2026, 11, 4, 0, 0, 0, tzinfo=ET)
JUDGING_UNTIL = datetime(2026, 11, 10, 0, 0, 0, tzinfo=ET)
MIN_ACCESS_CODE_LEN = 16
_UNSET = "unset"      # "not given": __post_init__ picks the default for the deployment kind


def _env(name: str, default, cast=str):
    v = os.environ.get(name)
    return default if v is None or v == "" else cast(v)


def _flag(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _path(v: str) -> Path:
    return Path(v).expanduser()


def parse_live_until(v: str) -> datetime | None:
    """ISO timestamp; a naive one is read as US Eastern. "none" / "off" disables the auto-off."""
    if v.strip().lower() in ("none", "off", "never"):
        return None
    d = datetime.fromisoformat(v.strip())
    return d if d.tzinfo else d.replace(tzinfo=ET)


@dataclass
class AppConfig:
    root: Path = field(default_factory=lambda: _env("ETHO_ROOT", REPO_ROOT, _path))
    # ---- hosting (ADR-003). `public` marks a deployment that faces the internet: it needs an access code for live
    # use, runs only under bwrap, keeps its own gateway cache (purged with the job) and takes the defaults marked
    # "(public)" below unless the env says otherwise. Plain localhost runs keep the old behaviour.
    public: bool = field(default_factory=lambda: _env("ETHO_APP_PUBLIC", False, _flag))
    read_only: bool = field(default_factory=lambda: _env("ETHO_APP_READ_ONLY", False, _flag))
    # "Open" public mode (ETHO_APP_OPEN=1): uploads and revisions need no access code and no login. It is a switch the
    # operator must set on purpose: a public deployment without it and without a readable code file stays read-only.
    # The kill switch, the read-only file/flag and the live-until date still apply, the showcase stays open, and an
    # upload must carry the consent field (server.py). The code file, if one exists, is then only an operator key.
    open: bool = field(default_factory=lambda: _env("ETHO_APP_OPEN", False, _flag))
    # Where the friendly cap messages send a visitor who cannot use live scoring (repo README: `make demo`).
    readme_url: str = field(default_factory=lambda: _env("ETHO_APP_README_URL", "https://github.com/Ani0202/etho#readme"))
    # Operator switches checked on every request, so they work without a restart. KILL stops live work at once
    # (running job cancelled, queue cleared); READ_ONLY only refuses new work. The showcase stays up in both.
    kill_file: Path | None = field(default_factory=lambda: _env("ETHO_APP_KILL_FILE", None, _path))
    readonly_file: Path | None = field(default_factory=lambda: _env("ETHO_APP_READONLY_FILE", None, _path))
    # Live use needs this code. It is read from a file (never from the page, the env or the log) and re-read when
    # the file changes, so it can be rotated without a restart; a rotated code invalidates every cookie.
    access_code_file: Path | None = field(default_factory=lambda: _env("ETHO_APP_ACCESS_CODE_FILE", None, _path))
    cookie_ttl_s: float = field(default_factory=lambda: _env("ETHO_APP_COOKIE_TTL_S", 7200.0, float))
    access_max_failures: int = field(default_factory=lambda: _env("ETHO_APP_ACCESS_FAILURES", 5, int))
    access_window_s: float = field(default_factory=lambda: _env("ETHO_APP_ACCESS_WINDOW_S", 900.0, float))
    access_global_failures: int = field(default_factory=lambda: _env("ETHO_APP_ACCESS_GLOBAL_FAILURES", 600, int))
    access_throttle_delay_s: float = field(default_factory=lambda: _env("ETHO_APP_ACCESS_DELAY_S", 1.0, float))
    access_global_window_s: float = 3600.0
    # Live mode ends at this moment (public: Nov 10 2026 23:59:59 ET). None = no auto-off.
    live_until: datetime | str | None = field(default_factory=lambda: _env("ETHO_APP_LIVE_UNTIL", _UNSET,
                                                                          lambda v: parse_live_until(v)))
    # Prefix of the `caller` string on every ledger row of an app job. The caps count only rows with the app's own
    # prefix, so a second instance on the same tag (the lead's localhost demo recording) does not eat the public
    # instance's budget. (public: "pub:job:", localhost: "job:")
    caller_prefix: str | None = field(default_factory=lambda: _env("ETHO_APP_CALLER_PREFIX", None))
    # Gateway tag for every model call the app makes (public: "demo-reserve"; the gateway refuses it before Nov 3).
    tag: str | None = field(default_factory=lambda: _env("ETHO_APP_TAG", None))
    # Cumulative cap over the app's own ledger rows (None = no cumulative cap; public: $20).
    total_cap_usd: float | None = field(default_factory=lambda: _env("ETHO_APP_TOTAL_CAP_USD", None, float))
    # Paths. Defaults live under the repo; a deployment under another Unix user points these elsewhere.
    data_root: Path | None = field(default_factory=lambda: _env("ETHO_APP_DATA_DIR", None, _path))
    showcase_root: Path | None = field(default_factory=lambda: _env("ETHO_APP_SHOWCASE_DIR", None, _path))
    # The app's own working directory (default: `<data dir>/work`). Its `cache/chunks` is the only chunk cache the
    # app uses, in every mode, so it is never mixed with the repo's `cache/chunks` (the benchmark's) and it can be
    # swept; tier 1's own hard-coded `cache/chunks` is redirected to it (tuning.py).
    work_root: Path | None = field(default_factory=lambda: _env("ETHO_APP_WORK_DIR", None, _path))
    # Gateway response cache. None = the gateway's own (shared) cache. A private dir (public default) holds only this
    # app's entries, so retention can delete them with the job.
    gateway_cache_root: Path | None = field(default_factory=lambda: _env("ETHO_APP_GATEWAY_CACHE", None, _path))
    # How often the retention timer runs (also at startup).
    maintenance_interval_s: float = field(default_factory=lambda: _env("ETHO_APP_MAINTENANCE_S", 300.0, float))
    # Upload limits (checked before anything is spent). (public: 50 MB and 120 s)
    max_upload_mb: float | None = field(default_factory=lambda: _env("ETHO_APP_MAX_UPLOAD_MB", None, float))
    max_duration_s: float | None = field(default_factory=lambda: _env("ETHO_APP_MAX_DURATION_S", None, float))
    max_behaviors: int = 8
    # Speed. SAM bills at least 50 frames per request, so at 10 fps a chunk is never shorter than 10 s; the target is
    # about `chunk_target` chunks per video so that they track in parallel (chunk_s = clamp(duration / 8, 10, 60)).
    chunk_target: int = 8
    chunk_min_s: int = 60   # = benchmark; 10 s and 30 s chunks lost coverage (DECISIONS 2026-10-28)
    chunk_max_s: int = 60
    # Fireworks calls in flight for tier 1 (the benchmark default is 6; 40 at once returned HTTP 429).
    tier1_concurrency: int = field(default_factory=lambda: _env("ETHO_APP_TIER1_CONCURRENCY", 16, int))
    synth_workers: int = 8                     # detector-writing calls in flight (at most one per behavior)
    # Per-day app spend cap (UTC day) and the pre-estimate used to refuse a job that would cross it. Rates are
    # measured costs with margin: SAM $0.002/s of video at 10 fps, tier-1 $0.0012/s (docs/STATE.md).
    # The "day" is a rolling 24 hours, not a calendar day, so spend cannot be doubled around midnight.
    # (public: $3; $8 in the judging window)
    daily_cap_usd: float | None = field(default_factory=lambda: _env("ETHO_APP_DAILY_CAP_USD", None, float))
    # Daily cap during the judging window [JUDGING_FROM, JUDGING_UNTIL) (public: $8; None = no raise).
    judging_daily_cap_usd: float | None = field(default_factory=lambda: _env("ETHO_APP_JUDGING_DAILY_CAP_USD", None,
                                                                             float))
    # Per-job ceiling = min(max(mult x estimate, estimate + extra), headroom); reserved when the job is admitted.
    # (public: 1.5 and $0.25, so two 120 s jobs fit under $3 a day; measured cost is 0.4 to 0.6 of the estimate)
    job_ceiling_mult: float | None = field(default_factory=lambda: _env("ETHO_APP_CEILING_MULT", None, float))
    job_ceiling_extra_usd: float | None = field(default_factory=lambda: _env("ETHO_APP_CEILING_EXTRA_USD", None, float))
    est_sam_usd_per_s: float = 0.003
    est_tier1_usd_per_s: float = 0.003
    est_fixed_usd: float = 0.05
    est_per_behavior_usd: float = 0.06   # one high-effort synthesis call per behavior
    est_revise_per_behavior_usd: float = 0.15
    # Per-client rate limit (analysis and revision jobs both count). The client is request.client.host; behind a
    # reverse proxy run uvicorn with --forwarded-allow-ips so it rewrites the address (bin/app passes it through).
    # (default 3 per hour; open mode: 10 per rolling 24 hours, Aniket 2026-11-03)
    rate_limit_jobs: int | None = field(default_factory=lambda: _env("ETHO_APP_RATE_JOBS", None, int))
    rate_window_s: float | None = field(default_factory=lambda: _env("ETHO_APP_RATE_WINDOW_S", None, float))
    max_corrections: int = 40
    max_queue: int = 5
    # Most jobs queued or running at once (analyses and revisions). None = only `max_queue` limits it; open mode: 2 (one
    # runs, one waits, since the worker runs one job at a time). ETHO_APP_MAX_IN_FLIGHT overrides; 0 = no limit.
    max_in_flight: int | None = field(default_factory=lambda: _env("ETHO_APP_MAX_IN_FLIGHT", None, int))
    max_json_body_bytes: int = 256_000        # every POST except the upload
    max_concurrent_uploads: int = 2
    max_concurrent_probes: int = 2
    max_sse_per_client: int = 4
    # Uploads, results and the cache entries made for them are deleted this long after the upload.
    retention_hours: float = field(default_factory=lambda: _env("ETHO_APP_RETENTION_H", 24.0, float))
    # Showcase runs are CalMS21 (public data) unless their result.json says otherwise.
    # Wording shown in the UI (Aniket is deciding the final text).
    tagline: str = field(default_factory=lambda: _env(
        "ETHO_APP_TAGLINE", "No labeled examples, no model training: behaviors scored from plain-English "
        "definitions with Meta models"))
    tile_title: str = field(default_factory=lambda: _env("ETHO_APP_TILE_TITLE", "Labeled training frames"))
    tile_value: str = field(default_factory=lambda: _env("ETHO_APP_TILE_VALUE", "0"))
    tile_sub: str = field(default_factory=lambda: _env(
        "ETHO_APP_TILE_SUB", "Definitions in English only; corrections below are optional"))

    def __post_init__(self) -> None:
        pub = self.public
        if self.job_ceiling_mult is None:
            self.job_ceiling_mult = 1.5 if pub else 2.0
        if self.job_ceiling_extra_usd is None:
            self.job_ceiling_extra_usd = 0.25 if pub else 0.5
        if self.rate_limit_jobs is None:
            self.rate_limit_jobs = 10 if self.open else 3
        if self.rate_window_s is None:
            self.rate_window_s = 86400.0 if self.open else 3600.0
        if self.max_in_flight is None:
            self.max_in_flight = 2 if self.open else 0
        if self.caller_prefix is None:
            self.caller_prefix = "pub:job:" if pub else "job:"
        if self.tag is None:
            self.tag = "demo-reserve" if pub else "dev"
        # An explicitly set daily cap (ETHO_APP_DAILY_CAP_USD or a constructor argument) is never raised in the
        # judging window unless ETHO_APP_JUDGING_DAILY_CAP_USD is set as well.
        if self.judging_daily_cap_usd is None and pub and self.daily_cap_usd is None:
            self.judging_daily_cap_usd = 8.0
        if self.daily_cap_usd is None:
            self.daily_cap_usd = 3.0 if pub else 5.0
        if self.total_cap_usd is None and pub:
            self.total_cap_usd = 30.0
        if self.max_upload_mb is None:
            self.max_upload_mb = 50.0 if pub else 200.0
        if self.max_duration_s is None:
            self.max_duration_s = 120.0 if pub else 300.0
        if self.live_until == _UNSET:
            self.live_until = DEFAULT_LIVE_UNTIL if pub else None
        if self.gateway_cache_root is None and pub:
            self.gateway_cache_root = self.data_dir / "gateway_cache"

    @property
    def consent_required(self) -> bool:
        """An upload must say the visitor may upload it and that it shows no people (open mode only)."""
        return self.open

    def daily_cap_at(self, now: datetime) -> float:
        """The rolling-24 h cap in force at `now`: the judging-window cap inside the window, else `daily_cap_usd`."""
        if self.judging_daily_cap_usd is not None and JUDGING_FROM <= now < JUDGING_UNTIL:
            return self.judging_daily_cap_usd
        return self.daily_cap_usd

    @property
    def detector_cache_dir(self) -> Path:
        return self.data_dir / "detector_cache"

    @property
    def data_dir(self) -> Path:
        return self.data_root or self.root / "data" / "app"

    @property
    def sample_dir(self) -> Path:
        """The sample clip for judges (scripts/make_sample_clip.py), served read-only. It lives under the showcase
        dir (no result.json, so it is never listed as a showcase) because the public unit bind-mounts that dir
        read-only and cannot read the operator's data/app."""
        return self.showcase_dir / "_sample"

    @property
    def work_dir(self) -> Path:
        return self.work_root or self.data_dir / "work"

    @property
    def tmp_dir(self) -> Path:
        """Where the process keeps temp files (the planner's sampled frames, upload spools); swept by retention."""
        return self.data_dir / "tmp"

    @property
    def chunk_cache_dir(self) -> Path:
        return self.work_dir / "cache" / "chunks"

    @property
    def private_gateway_cache(self) -> bool:
        return self.gateway_cache_root is not None

    @property
    def kill_path(self) -> Path:
        return self.kill_file or self.data_dir / "KILL"

    @property
    def readonly_path(self) -> Path:
        return self.readonly_file or self.data_dir / "READ_ONLY"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def showcase_dir(self) -> Path:
        return self.showcase_root or self.root / "data" / "showcase"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ops" / "spend.jsonl"

    def estimate_job_usd(self, duration_s: float, n_behaviors: int) -> float:
        return round(duration_s * (self.est_sam_usd_per_s + self.est_tier1_usd_per_s) + self.est_fixed_usd
                     + n_behaviors * self.est_per_behavior_usd, 4)

    def estimate_revise_usd(self, duration_s: float, n_affected: int) -> float:
        return round(self.est_fixed_usd + n_affected * self.est_revise_per_behavior_usd
                     + duration_s * self.est_tier1_usd_per_s, 4)
