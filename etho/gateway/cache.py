"""Disk cache keyed by a hash of the exact request. A hit costs $0 and is still logged."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def request_key(provider: str, endpoint: str, body: Any) -> str:
    blob = json.dumps({"provider": provider, "endpoint": endpoint, "body": body},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class DiskCache:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def get_json(self, key: str) -> dict | None:
        p = self._path(key, ".json.gz")
        if not p.exists():
            return None
        with gzip.open(p, "rt") as f:
            return json.load(f)

    def put_json(self, key: str, value: dict) -> None:
        p = self._path(key, ".json.gz")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}")
        with gzip.open(tmp, "wt") as f:
            json.dump(value, f)
        tmp.replace(p)

    def text_path(self, key: str) -> Path:
        """Location for streamed text payloads (SAM output), stored gzipped."""
        return self._path(key, ".txt.gz")
