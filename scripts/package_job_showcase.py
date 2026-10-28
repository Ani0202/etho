"""Package a finished web-app job (data/app/jobs/<id>) as a showcase, keeping what the run really measured.

  uv run python scripts/package_job_showcase.py --job data/app/jobs/<id> --name calms21-live-run \
      --title "CalMS21 dev clip, scored live" --description "..." [--calms21] [--public-data] \
      [--expert-labels labels.npz --expert-start-s 0]

Writes data/showcase/<name>/ (gitignored: it holds dataset video) with the job's own files: the cleaned video, the
tracks, labels, detectors, tier-1 verdicts and strips, overlay, report, plan.json (the perception planner's steps)
and calls.jsonl (every model call's prompt, answer, tokens, latency and cost). result.json keeps the job's real
per-stage timings, wall-clock time, per-stage costs and the gateway-ledger total; only the identity fields change
(id, kind, title, description, dataset). No model is called and nothing is spent. Revisions of the job are not
packaged: the showcase is the original run (visitors make their own revisions in a private copy).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etho.app.packaging import CALMS21, expert_record  # noqa: E402
from etho.app.store import ID_RE, read_json, write_json  # noqa: E402

COPY_FILES = ("labels.npz", "labels_rules.npz", "detectors.json", "verdicts.jsonl", "report.html", "overlay.mp4",
              "plan.json", "calls.jsonl")
NOT_COPIED = "input.json (the upload's name and settings), job.json, revisions/, cache_refs.json"


def package(job: Path, name: str, out_root: Path, *, title: str | None = None, description: str = "",
            public_data: bool | None = None, calms21: bool = False, expert_labels: Path | None = None,
            expert_start_s: float = 0.0) -> Path:
    """Build out_root/<name> from the job dir `job`. Raises SystemExit with a plain message when the job is not a
    finished analysis."""
    if not ID_RE.match(name):
        sys.exit(f"--name must match {ID_RE.pattern}")
    res = read_json(job / "result.json")
    if not res or res.get("kind") not in ("job", None) or not res.get("versions"):
        sys.exit(f"{job} has no finished analysis (result.json missing); run the job to completion first")
    for f in ("labels.npz", "labels_rules.npz", "detectors.json", "overlay.mp4", "report.html"):
        if not (job / f).is_file():
            sys.exit(f"{job}/{f} is missing; the job did not finish all stages")
    video = next((job / n for n in ("clean.mp4",) if (job / n).is_file()), None)
    track = read_json(job / "run" / "video" / "tracking.json")
    if video is None or track is None:
        sys.exit(f"{job} has no clean.mp4 or run/video/tracking.json (was it already purged by retention?)")

    out = out_root / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(job / "run", out / "run")
    for f in COPY_FILES:
        if (job / f).is_file():
            shutil.copy2(job / f, out / f)
    shutil.copy2(video, out / "clean.mp4")
    if (job / "strips").is_dir():
        shutil.copytree(job / "strips", out / "strips")
    track["src"] = str(out / "clean.mp4")          # relative to the repo root, like make_showcase.py and the app
    write_json(out / "run" / "video" / "tracking.json", track)

    pub = bool(res.get("public_data")) if public_data is None else public_data
    n = int(res["n_samples"])
    res.update(id=f"sc-{name}", kind="showcase", title=title or res.get("title") or name, description=description,
               public_data=pub, created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               packaged_from=job.name)
    res.pop("forked_from", None)
    if calms21:
        res["dataset"] = CALMS21
    if expert_labels:
        res["expert"] = expert_record(expert_labels, res["behaviors"]["names"], n, float(res["fps"]), expert_start_s)
    res["audit_note"] = (f"Packaged from a live run of the web app (job {job.name}). Timings, per-call costs, prompts "
                         "and answers are the ones recorded during that run; the ledger reconciliation below counts "
                         "only rows booked under this showcase's own id, so it shows 0 here.")
    write_json(out / "result.json", res)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True, type=Path, help="a finished job dir, data/app/jobs/<id>")
    ap.add_argument("--name", required=True, help="showcase name (the page is /p/sc-<name>)")
    ap.add_argument("--title")
    ap.add_argument("--description", default="")
    ap.add_argument("--out", type=Path, default=Path("data/showcase"))
    ap.add_argument("--calms21", action="store_true", help="credit CalMS21 (CC BY 4.0) as the data source")
    ap.add_argument("--public-data", dest="public_data", action="store_true", default=None,
                    help="mark as public data (forks may use -contributor models; default: as the job was run)")
    ap.add_argument("--expert-labels", type=Path, help="optional labels .npz for an 'Expert (CalMS21)' row")
    ap.add_argument("--expert-start-s", type=float, default=0.0)
    a = ap.parse_args(argv)
    out = package(a.job, a.name, a.out, title=a.title, description=a.description, public_data=a.public_data,
                  calms21=a.calms21, expert_labels=a.expert_labels, expert_start_s=a.expert_start_s)
    res = json.loads((out / "result.json").read_text())
    print(f"showcase written to {out} ({res['timing']['wall_s']} s wall, ${res['cost']['total_usd']} model cost; "
          f"not copied: {NOT_COPIED})")


if __name__ == "__main__":
    main()
