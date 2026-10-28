"""On-disk layout of a project (one analyzed video) and the showcase index.

  <base>/                    result.json (version 0), input.json, plan.json, clean.mp4 | source.mp4,
                             run/video/{features.npz, tracking.json, raw_c*.jsonl.gz}, labels.npz,
                             labels_rules.npz, detectors.json, verdicts.jsonl, report.html,
                             overlay.mp4, calls.jsonl, job.json
  <base>/revisions/<n>/      revision.json (version n), the same per-version files, job.json
  <base>/strips/             tier-1 strips (jpg), shared by all versions

A showcase (data/showcase/<name>) is read-only. To try the correction loop a visitor gets a private copy
(`fork_showcase`: a jobs/<id> dir that symlinks the big read-only files), which then behaves like any upload.
The copy's id is an unguessable capability, so nothing is shared between visitors.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SHOWCASE_PREFIX = "sc-"
SEQ = "video"
VERSION_FILES = {"overlay": "overlay.mp4", "report": "report.html", "labels": "labels.npz",
                 "detectors": "detectors.json", "verdicts": "verdicts.jsonl"}


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=float))
    tmp.replace(p)


@dataclass
class Project:
    id: str
    kind: str          # "job" | "showcase"
    base: Path
    work: Path
    public_data: bool

    @property
    def seq_dir(self) -> Path:
        return self.base / "run" / SEQ

    @property
    def strips_dir(self) -> Path:
        return self.work / "strips"

    def result(self) -> dict | None:
        return read_json(self.base / "result.json")

    def version_dir(self, v: int) -> Path:
        return self.base if v == 0 else self.work / "revisions" / str(v)

    def revision_numbers(self) -> list[int]:
        d = self.work / "revisions"
        if not d.exists():
            return []
        return sorted(int(p.name) for p in d.iterdir() if p.name.isdigit() and (p / "revision.json").exists())

    def revision(self, n: int) -> dict | None:
        return read_json(self.work / "revisions" / str(n) / "revision.json")

    def next_revision(self) -> int:
        d = self.work / "revisions"
        nums = [int(p.name) for p in d.iterdir() if p.name.isdigit()] if d.exists() else []
        return max(nums, default=0) + 1

    def latest_version(self) -> int:
        """Latest version that finished (has a revision.json); failed or interrupted revisions don't count."""
        nums = self.revision_numbers()
        return nums[-1] if nums else 0

    def version_file(self, v: int, kind: str) -> Path:
        return self.version_dir(v) / VERSION_FILES[kind]

    def video_path(self) -> Path | None:
        for pat in ("clean.mp4", "upload.*", "source.*"):
            hits = sorted(self.base.glob(pat))
            if hits:
                return hits[0]
        return None


def resolve_project(cfg: AppConfig, pid: str) -> Project | None:
    if not ID_RE.match(pid or ""):
        return None
    if pid.startswith(SHOWCASE_PREFIX):
        name = pid[len(SHOWCASE_PREFIX):]
        base = cfg.showcase_dir / name
        if not ID_RE.match(name) or not (base / "result.json").exists():
            return None
        public = bool(read_json(base / "result.json", {}).get("public_data"))
        return Project(pid, "showcase", base, base, public)
    base = cfg.jobs_dir / pid
    if not base.is_dir():
        return None
    res = read_json(base / "result.json")
    public = bool(res.get("public_data")) if res else bool(read_json(base / "input.json", {}).get("public_data"))
    return Project(pid, "job", base, base, public)


def fork_showcase(cfg: AppConfig, sc: Project, new_id: str) -> Project:
    """A private, revisable copy of a showcase. Big read-only inputs are symlinks; result.json is rewritten for the
    copy; strips are copied because revising writes new strips next to them."""
    base = cfg.jobs_dir / new_id
    base.mkdir(parents=True)
    for item in sc.base.iterdir():
        if item.name == "result.json":
            continue
        if item.name == "strips":
            shutil.copytree(item, base / "strips")
        else:
            os.symlink(item.resolve(), base / item.name)
    res = read_json(sc.base / "result.json")
    res.update(id=new_id, kind="showcase_copy", forked_from=sc.id)
    write_json(base / "result.json", res)
    write_json(base / "input.json", {"public_data": bool(res.get("public_data")), "forked_from": sc.id,
                                     "filename": res.get("title")})
    return Project(new_id, "job", base, base, bool(res.get("public_data")))


def list_showcases(cfg: AppConfig) -> list[dict]:
    out = []
    if not cfg.showcase_dir.exists():
        return out
    for d in sorted(cfg.showcase_dir.iterdir()):
        r = read_json(d / "result.json")
        if not r:
            continue
        out.append({"id": SHOWCASE_PREFIX + d.name, "title": r.get("title", d.name),
                    "description": r.get("description", ""), "video": r.get("video", {}),
                    "behaviors": r.get("behaviors", {}).get("names", []),
                    "metrics": r.get("versions", [{}])[0].get("metrics", {}),
                    "cost_usd": r.get("cost", {}).get("total_usd"),
                    "dataset": r.get("dataset")})
    return out
