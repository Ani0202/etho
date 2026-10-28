"""Read individual members of a huge remote zip with HTTP Range requests (no full download).

CalMS21's task1_videos_mp4.zip is 28 GB; we need a handful of sequences. data.caltech.edu
redirects to short-lived presigned URLs, so the redirect is re-resolved whenever it expires.
"""

from __future__ import annotations

import io
import shutil
import time
import zipfile
from pathlib import Path

import httpx


class HttpRangeFile(io.RawIOBase):
    def __init__(self, url: str, client: httpx.Client | None = None, ttl_s: float = 45.0):
        self.url = url
        self.client = client or httpx.Client(timeout=120.0)
        self.ttl_s = ttl_s
        self._resolved: str | None = None
        self._resolved_at = 0.0
        self.pos = 0
        self.size = self._fetch_size()

    def _target(self, force: bool = False) -> str:
        if force or self._resolved is None or time.monotonic() - self._resolved_at > self.ttl_s:
            r = self.client.get(self.url, headers={"Range": "bytes=0-0"}, follow_redirects=False)
            self._resolved = r.headers["location"] if r.is_redirect else self.url
            self._resolved_at = time.monotonic()
        return self._resolved

    def _get(self, start: int, end: int) -> bytes:
        for attempt in range(5):
            r = self.client.get(self._target(force=attempt > 0), headers={"Range": f"bytes={start}-{end}"})
            if r.status_code == 206:
                return r.content
            if r.status_code in (403, 429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            raise OSError(f"server ignored Range (HTTP {r.status_code})")
        raise OSError(f"range request failed after retries: {r.status_code}")

    def _fetch_size(self) -> int:
        r = self.client.get(self._target(), headers={"Range": "bytes=0-0"})
        if r.status_code != 206:
            raise OSError(f"Range not supported (HTTP {r.status_code})")
        return int(r.headers["content-range"].split("/")[-1])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def readinto(self, b) -> int:
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        data = self._get(self.pos, self.pos + n - 1)
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def open_remote_zip(url: str) -> zipfile.ZipFile:
    raw = HttpRangeFile(url)
    return zipfile.ZipFile(io.BufferedReader(raw, buffer_size=8 << 20))


def extract(zf: zipfile.ZipFile, member: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zf.open(member) as src, open(tmp, "wb") as out:
        shutil.copyfileobj(src, out, length=8 << 20)
    tmp.replace(dest)
    return dest
