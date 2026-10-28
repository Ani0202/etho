"""Detector cache: an English definition that was already turned into a detector is not written again.

Key: sha256 of (behavior name, definition text, subject, partner, `FEATURE_DOC`). `FEATURE_DOC` is part of the
key because a detector is code over those feature names: change the features and every old detector is stale.

Lookup order for one behavior:
  1. seed: the definition text and the subject/partner match one in `configs/calms21_definitions.json`
     exactly, so the detector scored on the benchmark (`configs/detectors/R1_zero_shot.json`) is used. It was
     written from CalMS21 dev feature statistics and is not adapted to the uploaded video; the app also tracks
     with shorter chunks than the benchmark, so app results are not guaranteed to match the reported numbers.
     The seed is matched on text, not on the name the user
     gave the behavior (a detector is a function of the features and does not know its name).
  2. cache: a detector the app wrote for the same key on an earlier upload (also tried when the seed does not
     run on the video at hand: the replacement written for that case is cached).
  3. miss: the caller synthesizes one and `put`s it here.
Detector code contains no user video and no user data, so reuse across uploads is fine. (The prompt that produced
it did include label-free feature statistics of the first video it was written for; the audit trail says so.)

Every result carries a `Provenance`, which the audit trail and the results page show per behavior.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from etho.rules.synthesize import Detector
from etho.tracking.features import FEATURE_DOC

from .store import read_json, write_json

SEED_DEFINITIONS = Path("configs/calms21_definitions.json")
SEED_DETECTORS = Path("configs/detectors/R1_zero_shot.json")


def cache_key(name: str, definition: str, subject: str, partner: str) -> str:
    blob = json.dumps([name, definition, subject, partner, FEATURE_DOC], sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Provenance:
    behavior: str
    source: str                 # "seed" | "cache" | "fresh" | "revised"
    origin: str                 # human-readable: where the code came from
    key: str = ""               # first 16 hex chars of the cache key
    model: str = ""
    note: str = ""              # e.g. "reused detector failed on this video; rewritten"

    def as_dict(self) -> dict:
        return asdict(self)


class DetectorCache:
    def __init__(self, root: Path, cache_dir: Path):
        self.root, self.dir = Path(root), Path(cache_dir)
        self._lock = threading.Lock()
        self._seed: dict | None = None

    # ---- seed -------------------------------------------------------------------------------
    def _load_seed(self) -> dict:
        """{definition text: (subject, partner, behavior in the benchmark, detector dict)}."""
        if self._seed is None:
            seed: dict = {}
            defs = read_json(self.root / SEED_DEFINITIONS, {})
            dets = read_json(self.root / SEED_DETECTORS, {})
            by_name = {d["behavior"]: d for d in dets.get("detectors", []) if d.get("code")}
            for name, text in (defs.get("behaviors") or {}).items():
                if name in by_name:
                    seed[text] = (defs.get("subject", ""), defs.get("partner", ""), name, by_name[name])
            self._seed = seed
        return self._seed

    def seed_for(self, name: str, definition: str, subject: str, partner: str) -> tuple[Detector, Provenance] | None:
        hit = self._load_seed().get(definition)
        if not hit or (hit[0], hit[1]) != (subject, partner):
            return None
        _, _, bench_name, d = hit
        det = Detector(name, d["code"], d.get("model", ""), [], d.get("prompt_hash", ""))
        origin = (f"benchmark detector for '{bench_name}' ({SEED_DETECTORS.as_posix()}), the one scored on CalMS21; "
                  "your definition matches the dataset authors' wording exactly. It was written from CalMS21 "
                  "feature statistics and is not adapted to this video")
        return det, Provenance(name, "seed", origin, cache_key(name, definition, subject, partner)[:16],
                               det.model)

    # ---- cache ------------------------------------------------------------------------------
    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def candidates(self, name: str, definition: str, subject: str,
                   partner: str) -> list[tuple[Detector, Provenance]]:
        """Detectors that may be reused for this behavior, best first: the benchmark seed, then the cache."""
        out = []
        seeded = self.seed_for(name, definition, subject, partner)
        if seeded:
            out.append(seeded)
        key = cache_key(name, definition, subject, partner)
        with self._lock:
            rec = read_json(self._path(key), None)
        if isinstance(rec, dict) and rec.get("code"):
            det = Detector(name, rec["code"], rec.get("model", ""), [], rec.get("prompt_hash", ""))
            origin = (f"detector cache: written {rec.get('created', '?')} by {rec.get('model', '?')} "
                      f"for an earlier upload ({rec.get('job', '?')}); no model call made for it now")
            out.append((det, Provenance(name, "cache", origin, key[:16], det.model)))
        return out

    def get(self, name: str, definition: str, subject: str, partner: str) -> tuple[Detector, Provenance] | None:
        c = self.candidates(name, definition, subject, partner)
        return c[0] if c else None

    def put(self, det: Detector, definition: str, subject: str, partner: str, *, job: str,
            cacheable: bool = True) -> Provenance:
        """First writer wins: an existing entry is never overwritten (a degenerate upload can't poison it), and
        callers pass cacheable=False when the detector was written from a poorly tracked video."""
        key = cache_key(det.behavior, definition, subject, partner)
        fresh = Provenance(det.behavior, "fresh", f"written for this upload by {det.model} (effort high)",
                           key[:16], det.model)
        if not cacheable:
            fresh.note = "not cached: written from a poorly tracked video"
            return fresh
        rec = {"behavior": det.behavior, "code": det.code, "model": det.model, "prompt_hash": det.prompt_hash,
               "job": job, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "cost_usd": round(sum(c.get("cost_usd", 0.0) for c in det.calls), 6)}
        with self._lock:
            if self._path(key).exists():
                fresh.note = "not cached: an earlier entry for this definition is kept"
                return fresh
            write_json(self._path(key), rec)
        return fresh
