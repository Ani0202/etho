"""Runtime speed settings for app jobs, applied from outside the frozen pipeline modules.

* `choose_chunk_s`: SAM chunk length so that short uploads still track in parallel.
* `install`: sets tier 1's Fireworks concurrency for app jobs (module attributes `FW_CONCURRENCY` and `_FW_SLOTS`)
  and makes tier 1's own `prepare_chunks` call find the chunks the app already cut. Tier 1 calls it with the
  default `chunk_s=60`; the app cuts at `chunk_s` from `choose_chunk_s`, and the chunk cache is keyed by `chunk_s`,
  so without this it would cut the video a second time. The app registers each source's `chunk_s` and the shim
  substitutes it. Sources that were never registered behave as before.
`install` is idempotent and process-wide; it is called by `Backend.__init__`.
"""

from __future__ import annotations

import threading
from pathlib import Path

from etho.adjudication import tier1
from etho.tracking import pipeline

from .config import AppConfig

_REGISTRY: dict[str, int] = {}
_REG_LOCK = threading.Lock()


def choose_chunk_s(duration_s: float, cfg: AppConfig | None = None) -> int:
    """clamp(duration / 8, 10, 60) seconds, as an int (ffmpeg's segment time and the cache key use it)."""
    cfg = cfg or AppConfig()
    return int(min(cfg.chunk_max_s, max(cfg.chunk_min_s, round(duration_s / cfg.chunk_target))))


def register_chunk_s(src: Path, chunk_s: int) -> None:
    with _REG_LOCK:
        _REGISTRY[str(Path(src).resolve())] = int(chunk_s)


def forget_under(directory: Path) -> None:
    """Drop registrations for sources inside a deleted project directory."""
    prefix = str(Path(directory).resolve()) + "/"
    with _REG_LOCK:
        for k in [k for k in _REGISTRY if k.startswith(prefix)]:
            del _REGISTRY[k]


def registered_chunk_s(src: Path) -> int | None:
    with _REG_LOCK:
        return _REGISTRY.get(str(Path(src).resolve()))


def _prepare_chunks_for_app(src, out_dir, *, stride=3, fps=10, chunk_s=60):
    cs = registered_chunk_s(Path(src))
    return _original_prepare(src, out_dir, stride=stride, fps=fps, chunk_s=cs if cs else chunk_s)


_original_prepare = pipeline.prepare_chunks
_prepare_chunks_for_app._etho_app_shim = True  # type: ignore[attr-defined]


def install(cfg: AppConfig) -> None:
    n = max(1, int(cfg.tier1_concurrency))
    tier1.FW_CONCURRENCY = n
    tier1._FW_SLOTS = threading.BoundedSemaphore(n)
    if not getattr(tier1.prepare_chunks, "_etho_app_shim", False):
        tier1.prepare_chunks = _prepare_chunks_for_app
