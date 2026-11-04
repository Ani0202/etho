"""The replay index: one gzipped JSON-lines file, a header line then one line per recorded call.

    {"kind": "header", "version": 1, "label": ..., "clip": {...}, "live_run": {...}}
    {"kind": "call", "key": <sha256>, "occ": 0, "desc": {...scrubbed request...}, "response": {...}}

`desc` is the scrubbed request (see keys.py); it is kept so a miss can show the nearest recordings and what differs.
"""

from __future__ import annotations

import difflib
import gzip
import json
from collections import defaultdict
from pathlib import Path

from . import keys

VERSION = 1


class ReplayMiss(Exception):
    """A call that has no recording. There is no fallback: the demo never reaches a model or the network."""


class ReplayIndex:
    def __init__(self, header: dict, entries: list[dict]):
        self.header = header
        self.entries = entries
        self._by_key: dict[tuple[str, int], dict] = {}
        self._count: dict[str, int] = defaultdict(int)
        for e in entries:
            self._by_key[(e["key"], e["occ"])] = e
            self._count[e["key"]] += 1

    # ---- files ------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> ReplayIndex:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            lines = [json.loads(ln) for ln in f if ln.strip()]
        if not lines or lines[0].get("kind") != "header" or lines[0].get("version") != VERSION:
            raise ValueError(f"{path} is not a version-{VERSION} replay index")
        return cls(lines[0], [ln for ln in lines[1:] if ln.get("kind") == "call"])

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join([json.dumps(self.header, sort_keys=True)]
                         + [json.dumps({"kind": "call", **e}, sort_keys=True) for e in self.entries]) + "\n"
        with open(path, "wb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as raw:
            raw.write(text.encode("utf-8"))      # no name, no time: the same index gives the same bytes

    # ---- lookup -----------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.entries)

    def occurrences(self, key: str) -> int:
        return self._count.get(key, 0)

    def get(self, key: str, occ: int) -> dict | None:
        return self._by_key.get((key, occ))

    def nearest(self, desc: dict, n: int = 3) -> list[dict]:
        """The recordings most like `desc`: the same method, provider, model and caller module first, then by how
        similar the request text is."""
        mine = _lines(desc)

        def score(e: dict) -> tuple[int, float, float]:
            d = e["desc"]
            same = sum(d[k] == desc[k] for k in ("method", "provider", "model", "caller"))
            theirs = _lines(d)
            a, b = set(mine), set(theirs)
            common = len(a & b) / max(len(a | b), 1)       # most lines identical = nearest
            rest = difflib.SequenceMatcher(None, " ".join(sorted(a - b))[:600], " ".join(sorted(b - a))[:600]).ratio()
            return same, common, rest

        ranked = sorted(self.entries, key=score, reverse=True)
        seen, out = set(), []
        for e in ranked:
            if e["key"] in seen:
                continue
            seen.add(e["key"])
            out.append(e)
            if len(out) >= n:
                break
        return out

    def miss(self, desc: dict, key: str, occ: int, caller: str | None) -> ReplayMiss:
        """The error for a call with no recording: names the key, the caller and the nearest recorded keys, and the
        first text lines that differ from the nearest one (for tier 1 that is the drifted window, for rule synthesis
        the edited definition)."""
        have = self.occurrences(key)
        lines = [f"REPLAY MISS: no recorded response for this call. {keys.summary(desc)}",
                 f"  key {key[:16]} occurrence {occ}"
                 + (f" (this request was recorded {have} time{'s' if have != 1 else ''}; it is being asked for "
                    f"more often than that)" if have else ""),
                 f"  caller {caller}"]
        near = self.nearest(desc)
        if near:
            lines.append("  nearest recorded keys:")
            for e in near:
                lines.append(f"    {e['key'][:16]} (x{self.occurrences(e['key'])})  {keys.summary(e['desc'])}")
            diff = _text_diff(desc, near[0]["desc"])
            if diff:
                lines.append("  difference from the nearest recording (- requested, + recorded):")
                lines += [f"    {ln}" for ln in diff]
        lines.append("  The demo serves only recorded responses and never falls back to a model or the network. "
                     "To score other inputs, add your own Meta Model API key (README, Setup).")
        return ReplayMiss("\n".join(lines))


def _lines(desc: dict) -> list[str]:
    """Text lines of the request, plus its parameters (images, effort, SAM phrase, ...) as lines."""
    params = [f"{k}={json.dumps(v, sort_keys=True)}" for k, v in desc["request"].items()
              if k not in ("messages", "input")]
    return [ln for t in keys.texts(desc) for ln in t.splitlines()] + params


def _text_diff(want: dict, have: dict, limit: int = 8) -> list[str]:
    a, b = _lines(want), _lines(have)
    out = []
    for ln in difflib.unified_diff(a, b, lineterm="", n=0):
        if ln.startswith(("---", "+++", "@@")):
            continue
        out.append(ln if len(ln) <= 200 else ln[:200] + "...")
        if len(out) >= limit:
            break
    return out
