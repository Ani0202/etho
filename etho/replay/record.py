"""Build the replay index from a real run that is already in a gateway cache, without spending anything.

    uv run python -m etho.replay.record --cache data/app_rec/gwc --job-dir data/app_rec/jobs/<id> \\
        --ledger ops/spend.jsonl --clip demo/sample/calms21_dev_60s.mp4 --out demo/sample/replay.jsonl.gz

The same job is run again through the web app (`session.run_job`, the code the replay uses), with a recorder between
the app and a `CacheOnlyGateway`: the real `Gateway` pointed at the existing cache, in a mode that refuses any cache
miss before it can reach the network (its `_request` raises, and its HTTP client is a stub that raises too). Each
answered call is stored under its semantic key (keys.py) with the cost it was billed when it was made, recomputed
from the cached usage at the gateway's own prices. Nothing is called, so nothing is billed; the gateway's own ledger
for this run is a scratch directory, and the check at the end reads `--ledger` to confirm it gained no paid row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

from etho.app.backend import Backend, Ctx
from etho.app.config import REPO_ROOT
from etho.gateway import Gateway, GatewayConfig
from etho.gateway import prices as P

from . import LABEL, keys
from .index import VERSION, ReplayIndex
from .session import load_definitions, run_job


class CacheMiss(RuntimeError):
    """The cache has no entry for this request: the recorder never goes to the network."""


@dataclass
class _Cfg(GatewayConfig):
    cache: Path | None = None
    ops: Path | None = None

    @property
    def ops_dir(self) -> Path:
        return self.ops

    @property
    def cache_dir(self) -> Path:
        return self.cache


def _no_network(request: httpx.Request) -> httpx.Response:
    raise CacheMiss(f"the recorder reached for the network: {request.method} {request.url}")


class CacheOnlyGateway(Gateway):
    """The live gateway on an existing cache; a miss raises before anything is sent."""

    def __init__(self, cache_dir: Path, scratch_ops: Path, file_ids: list[str]):
        super().__init__(_Cfg(root=scratch_ops.parent, secrets_path=Path("/nonexistent/etho-record-no-secrets.env"),
                              cache=cache_dir, ops=scratch_ops),
                         client=httpx.Client(transport=httpx.MockTransport(_no_network)))
        self.file_ids = list(file_ids)

    def _request(self, provider, model, method, path, **kw):
        raise CacheMiss(f"cache miss: {method} {provider}{path} (model {model}); the recorder never calls out")

    def sam(self, phrase, *, tag, image=None, video=None, video_file_id=None, **kw):
        """The Files API id an upload got is part of the exact cache key, and the app deletes its uploads (and
        the cache entry that held the id) once tracking is done. Try the ids the original run's delete rows name;
        the right one is the entry whose output has one line per frame (the 5 s probe and the tracking chunk
        differ only in the id). Callbacks fire only for the chosen entry."""
        if video is None or video_file_id is not None or str(video).startswith(("http://", "https://")):
            return super().sam(phrase, tag=tag, image=image, video=video, video_file_id=video_file_id, **kw)
        n = kw.get("n_frames")
        trial = {k: v for k, v in kw.items() if k != "on_line"}
        for fid in self.file_ids:
            try:
                r = super().sam(phrase, tag=tag, video=video, video_file_id=fid, **trial)
            except CacheMiss:
                continue
            if sum(1 for _ in r.lines()) == n:
                return super().sam(phrase, tag=tag, video=video, video_file_id=fid, **kw)
        raise CacheMiss(f"no cached SAM output for {phrase!r} with {n} frames under any of the original run's "
                        f"Files API ids")


class Recorder:
    """Quacks like a gateway for JobGateway; answers from `inner` and stores each answer."""

    def __init__(self, inner: Gateway):
        self.inner = inner
        self.entries: list[dict] = []
        self._seen: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def _store(self, desc: dict, response: dict) -> None:
        key = keys.key_of(desc)
        with self._lock:
            occ = self._seen[key]
            self._seen[key] += 1
            self.entries.append({"key": key, "occ": occ, "desc": desc, "response": response})

    @staticmethod
    def _llm_cost(provider: str, model: str, u: dict) -> float:
        return P.token_cost(provider, model, u["input_tokens"], u["billed_output_tokens"], u["cached_tokens"])

    def chat(self, provider, model, messages, *, effort, tag=None, caller=None, **kw):
        res = self.inner.chat(provider, model, messages, effort=effort, tag=tag, caller=caller, **kw)
        if not res.cache_hit:
            raise CacheMiss("a chat call was not served from the cache")
        res.cost_usd = self._llm_cost(provider, model, res.usage)   # as billed originally (a cache hit says $0)
        self._store(keys.chat_desc(provider, model, messages, effort, caller, kw),
                    {"text": res.text, "usage": res.usage, "reasoning_tokens": res.reasoning_tokens,
                     "cost_usd": res.cost_usd})
        return res

    def responses(self, model, input, *, effort, tag=None, caller=None, **kw):  # noqa: A002
        res = self.inner.responses(model, input, effort=effort, tag=tag, caller=caller, **kw)
        if not res.cache_hit:
            raise CacheMiss("a responses call was not served from the cache")
        res.cost_usd = self._llm_cost("meta", model, res.usage)
        self._store(keys.responses_desc(model, input, effort, caller, kw),
                    {"text": res.text, "usage": res.usage, "reasoning_tokens": res.reasoning_tokens,
                     "cost_usd": res.cost_usd})
        return res

    def sam(self, phrase, *, tag=None, caller=None, **kw):
        res = self.inner.sam(phrase, tag=tag, caller=caller, **kw)
        if not res.cache_hit:
            raise CacheMiss("a SAM call was not served from the cache")
        is_image = kw.get("image") is not None
        cost = res.cost_usd = P.sam_image_cost(1) if is_image else P.sam_video_cost(res.frames_billed)
        self._store(keys.sam_desc(phrase, caller, kw),
                    {"text": res.text(), "usage": res.usage, "frames_billed": res.frames_billed, "cost_usd": cost})
        return res

    def upload_file(self, *a, **k):
        raise CacheMiss("the pipeline uploads through sam(); a direct upload was not expected while recording")

    def delete_file(self, file_id, *, tag=None, caller=None, request_key=None) -> bool:
        return True       # the recorder never uploaded anything


class RecordBackend(Backend):
    """The app's backend, with the original run's stage timings put back for the report stage only.

    The report's methods prompt lists the stage timings of the run that wrote it, so the cache entry made by the
    original run answers only a prompt carrying those exact numbers. (The replay key masks timings; see keys.py.)"""

    def __init__(self, cfg, timings: dict[str, float]):
        super().__init__(cfg)
        self._timings = timings

    def stage_report(self, ctx: Ctx) -> None:
        mine = {s.name: s.elapsed_s for s in ctx.job.stages}
        for s in ctx.job.stages:
            if s.name in self._timings and s.status == "done":
                s.elapsed_s = self._timings[s.name]
        try:
            super().stage_report(ctx)
        finally:
            for s in ctx.job.stages:
                if s.name in mine and s.status == "done":
                    s.elapsed_s = mine[s.name]


def file_ids_of(ledger: Path, job_id: str) -> list[str]:
    """Files API ids the original run deleted (delete rows carry the id), in ledger order."""
    out = []
    for line in ledger.read_text().splitlines():
        if job_id in line and '"file_id"' in line:
            r = json.loads(line)
            if (r.get("caller") or "").split("|", 1)[0].endswith(job_id) and r.get("file_id"):
                out.append(r["file_id"])
    return out


def paid_rows(ledger: Path, since_line: int) -> list[dict]:
    rows = [json.loads(x) for x in ledger.read_text().splitlines()[since_line:] if x.strip()]
    return [r for r in rows if float(r.get("cost_usd") or 0) > 0]


def expected_from(job_dir: Path) -> dict:
    """What the recorded (original) run produced, as the replay must reproduce it."""
    res = json.loads((job_dir / "result.json").read_text())
    v0 = res["versions"][0]
    labels = np.load(job_dir / "labels.npz")["labels"]
    rules = np.load(job_dir / "labels_rules.npz")["labels"]
    return {"names": res["behaviors"]["names"], "n_samples": res["n_samples"], "fps": res["fps"],
            "bouts": v0["bouts"], "rule_bouts": v0["rule_bouts"], "metrics": v0["metrics"],
            "labels_sha256": hashlib.sha256(np.ascontiguousarray(labels).tobytes()).hexdigest(),
            "rule_labels_sha256": hashlib.sha256(np.ascontiguousarray(rules).tobytes()).hexdigest(),
            "plan": {k: (res.get("plan") or {}).get(k) for k in ("phrase", "expected_count", "identity_method")}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, required=True, help="the original run's gateway cache directory")
    ap.add_argument("--job-dir", type=Path, required=True, help="the original run's job directory")
    ap.add_argument("--ledger", type=Path, required=True, help="the real ops/spend.jsonl (read only)")
    ap.add_argument("--clip", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="replay index to write (jsonl.gz)")
    ap.add_argument("--expected", type=Path, help="also write the original run's labels summary (json)")
    ap.add_argument("--work", type=Path, default=REPO_ROOT / "data" / "replay_rec")
    a = ap.parse_args(argv)

    for name in ("cache", "job_dir", "ledger", "clip", "out", "expected", "work"):
        if getattr(a, name) is not None:
            setattr(a, name, getattr(a, name).resolve())       # the job chdirs
    job_id = a.job_dir.name
    ledger_lines = len(a.ledger.read_text().splitlines())
    ids = file_ids_of(a.ledger, job_id)
    print(f"recording from {a.cache}; Files API ids of the original run: {len(ids)}")
    shutil.rmtree(a.work, ignore_errors=True)
    inner = CacheOnlyGateway(a.cache, a.work / "scratch_ops", ids)
    rec = Recorder(inner)
    orig = json.loads((a.job_dir / "result.json").read_text())
    timings = {s["name"]: s["elapsed_s"] for s in orig["stages"]}
    sess = run_job(rec, a.clip, load_definitions(), a.work, replay=False,
                   backend=lambda cfg: RecordBackend(cfg, timings))
    res = sess.result
    sess.close()
    cost = round(sum(e["response"]["cost_usd"] for e in rec.entries), 6)
    live = {"wall_s": orig["timing"]["wall_s"], "video_s": orig["timing"]["video_s"],
            "x_realtime": orig["timing"]["x_realtime"], "cost_usd": orig["cost"]["total_usd"],
            "stage_s": {s["name"]: s["elapsed_s"] for s in orig["stages"]},
            "stage_cost_usd": {s["name"]: s["cost_usd"] for s in orig["stages"]}, "n_calls": len(rec.entries)}
    clip = {"file": a.clip.name, "sha256": hashlib.sha256(a.clip.read_bytes()).hexdigest()}
    header = {"kind": "header", "version": VERSION, "label": LABEL, "clip": clip, "live_run": live,
              "recorded_cost_usd": cost, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ReplayIndex(header, rec.entries).save(a.out)
    if a.expected:
        a.expected.write_text(json.dumps(expected_from(a.job_dir), indent=1, sort_keys=True) + "\n")
    same = res["versions"][0]["bouts"] == orig["versions"][0]["bouts"]
    n_orig = len((a.job_dir / "calls.jsonl").read_text().splitlines())
    complete = len(rec.entries) == n_orig and not res.get("warnings")
    print(f"recorded {len(rec.entries)} of the original run's {n_orig} calls, cost as billed originally "
          f"${cost:.4f} (original job ${orig['cost']['total_usd']:.4f}); bouts equal the original run's: {same}; "
          f"warnings: {res.get('warnings')}")
    gained = paid_rows(a.ledger, ledger_lines)
    print(f"paid rows added to {a.ledger} while recording: {len(gained)}")
    if gained:
        print("FAILURE: a paid row appeared; stop and investigate.")
        return 2
    return 0 if (same and complete) else 1


if __name__ == "__main__":
    raise SystemExit(main())
