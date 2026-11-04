"""`make demo`: run Etho's web-app job pipeline on the 60 s CalMS21 sample clip from recorded responses.

No API key, no network, no model call: every call the pipeline makes is answered from demo/sample/replay.jsonl.gz
(see etho/replay/keys.py for how a call is matched). A call with no recording stops the run with a ReplayMiss.

    uv run python -m etho.replay.demo                  # run, print timings, bouts and the report path
    uv run python -m etho.replay.demo --serve          # ... then serve the result read-only on 127.0.0.1:8000
    uv run python -m etho.replay.demo --definitions my_defs.json     # edited definitions: expect a ReplayMiss
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import warnings
from pathlib import Path

import numpy as np

from etho.app.config import REPO_ROOT

from . import LABEL

SAMPLE = REPO_ROOT / "demo" / "sample"
RULE = "=" * 78
EXIT_OK, EXIT_FAILED, EXIT_MISS, EXIT_DIFFERS = 0, 1, 3, 4


def banner(stream=None) -> None:
    stream = stream or sys.stdout
    print(RULE, file=stream)
    print(LABEL, file=stream)
    print(RULE, file=stream)


def fmt_bouts(bouts: list, fps: float, limit: int = 6) -> str:
    spans = [f"{s / fps:.1f}-{e / fps:.1f}s" for s, e in bouts[:limit]]
    return ", ".join(spans) + (f", ... (+{len(bouts) - limit})" if len(bouts) > limit else "")


def stage_table(res: dict, live: dict) -> list[str]:
    lines = [f"  {'stage':<11}{'replay':>9}{'recorded live run':>20}   {'recorded cost':>13}"]
    live_s, live_c = live.get("stage_s", {}), live.get("stage_cost_usd", {})
    for s in res["stages"]:
        n = s["name"]
        lines.append(f"  {n:<11}{s['elapsed_s']:>8.1f}s{live_s.get(n, float('nan')):>19.1f}s   "
                     f"{'$' + format(live_c.get(n, 0.0), '.4f'):>13}")
    t = res["timing"]
    lines.append(f"  {'whole job':<11}{t['wall_s']:>8.1f}s{live.get('wall_s', float('nan')):>19.1f}s   "
                 f"{'$' + format(live.get('cost_usd', 0.0), '.4f'):>13}")
    return lines


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def labels_sha(path: Path) -> str:
    return hashlib.sha256(np.ascontiguousarray(np.load(path)["labels"]).tobytes()).hexdigest()


def compare_with_recorded(res: dict, job_dir: Path, expected: dict | None) -> tuple[bool | None, str]:
    """(None when there is nothing to compare with, else whether the labels equal the recorded run's; a sentence)."""
    if not expected:
        return None, "no recorded labels to compare with"
    same_bouts = res["versions"][0]["bouts"] == expected["bouts"]
    same_labels = labels_sha(job_dir / "labels.npz") == expected["labels_sha256"]
    if same_bouts and same_labels:
        return True, "yes: the scored labels equal the recorded live run's, sample for sample"
    n = int((np.load(job_dir / "labels.npz")["labels"] != 0).sum())
    return False, f"NO: bouts differ from the recorded run (labelled samples: {n}); this machine's numerics differ"


def run(index_path: Path, clip: Path, definitions_path: Path, out: Path, *, serve: bool, host: str,
        port: int) -> int:
    from .gateway import ReplayGateway
    from .index import ReplayIndex, ReplayMiss
    from .lockdown import lockdown
    from .session import load_definitions, run_job

    banner()
    index = ReplayIndex.load(index_path)
    recorded_clip = index.header.get("clip", {})
    if file_sha256(clip) != recorded_clip.get("sha256"):
        print(f"\nthis recording belongs to {recorded_clip.get('file', 'its own clip')}; for other clips add your own "
              "Meta Model API key (README, Setup)\n", file=sys.stderr)
        banner(sys.stderr)
        return EXIT_MISS
    with lockdown():
        expected_path = index_path.with_name("expected.json")
        expected = json.loads(expected_path.read_text()) if expected_path.exists() else None
        shutil.rmtree(out, ignore_errors=True)
        gw = ReplayGateway(index, out / "replay_scratch")
        print(f"clip        {clip.name}  (CalMS21, Sun et al. 2021, CC BY 4.0)")
        print(f"recording   {len(index)} recorded model calls in {index_path.name}")
        print(f"output      {out}")
        print("running the web app's job pipeline (validate, plan, track, rules, tier 1, report) ...")
        t0 = time.monotonic()
        try:
            sess = run_job(gw, clip, load_definitions(definitions_path), out, replay=True,
                           say=lambda m: print(f"  {m.lstrip('. ')}"), live=index.header.get("live_run"))
        except ReplayMiss as e:
            print(f"\n{e}\n", file=sys.stderr)
            banner(sys.stderr)
            return EXIT_MISS
        except Exception as e:
            print(f"\nthe demo run failed: {type(e).__name__}: {e}\n", file=sys.stderr)
            banner(sys.stderr)
            return EXIT_FAILED
        wall = time.monotonic() - t0
        res, job_dir = sess.result, sess.out_dir
        fps = res["fps"]
        live = index.header.get("live_run", {})

        print("\nPer-stage timings (replay time is local compute only: no model call waited on a network;\n"
              "the live run's times and costs are what was recorded):")
        print("\n".join(stage_table(res, live)))
        print(f"\nScored bouts ({res['n_samples']} samples at {fps:g} fps, "
              f"{res['video']['duration_s']:.0f} s of video):")
        m = res["versions"][0]["metrics"]
        for name, bouts in res["versions"][0]["bouts"].items():
            print(f"  {name:<14}{len(bouts):>3} bouts, {m[name]['total_s']:>5.1f} s   {fmt_bouts(bouts, fps)}")
        same, verdict = compare_with_recorded(res, job_dir, expected)
        print(f"\nMatches the recorded live run: {verdict}")
        print(f"{len(gw.served)} of {len(index)} recorded calls served; the replay made no model call and spent "
              f"$0.00 (the live run cost ${live.get('cost_usd', 0.0):.4f}).")
        if len(gw.served) != len(index):
            print(f"WARNING: {len(index) - len(gw.served)} recorded calls were not used, so this run differs from "
                  "the recorded one.")
        print("\nReport (open in a browser)   " + str(job_dir / "report.html"))
        print("Overlay video                " + str(job_dir / "overlay.mp4"))
        print("Audit trail (every call)     " + str(job_dir / "calls.jsonl"))
        print(f"Total wall time              {wall:.1f} s")
        if same is False:
            sess.close()
            print("\nFAILED: the scored labels differ from the recorded run's (exit code 4).", file=sys.stderr)
            banner(sys.stderr)
            return EXIT_DIFFERS
        if serve:
            return serve_app(sess, host, port, live)
        sess.close()
        print("\nTo browse the result in the app: make demo-serve")
        banner()
        return EXIT_OK


def serve_app(sess, host: str, port: int, live: dict | None = None) -> int:
    """Serve the finished project through the app, read-only, with the label on every page."""
    import uvicorn

    from .serve import LabelMiddleware

    sess.cfg.readonly_path.write_text("replay demo: uploads and revisions are off\n")    # the app checks it per request
    url = f"http://{host}:{port}/p/{sess.job_id}"
    print(f"\nServing the result, read-only: {url}\n(Ctrl-C to stop)\n")
    banner()
    uvicorn.run(LabelMiddleware(sess.app, live), host=host, port=port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore")
    sys.stdout.reconfigure(line_buffering=True)     # type: ignore[attr-defined]  # keep stdout and stderr in order
    ap = argparse.ArgumentParser(
        description="Etho offline replay demo (no keys, no network)",
        epilog="exit codes: 0 ok; 1 the run failed; 3 REPLAY MISS (a call, or the clip, has no recording); "
               "4 the scored labels differ from the recorded run's")
    ap.add_argument("--index", type=Path, default=SAMPLE / "replay.jsonl.gz")
    ap.add_argument("--clip", type=Path, default=SAMPLE / "calms21_dev_60s.mp4")
    ap.add_argument("--definitions", type=Path, default=REPO_ROOT / "configs" / "calms21_definitions.json",
                    help="behavior definitions (the format of configs/calms21_definitions.json)")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "replay_demo",
                    help="scratch output directory (emptied at the start)")
    ap.add_argument("--serve", action="store_true", help="after the run, serve the result read-only")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    return run(a.index.resolve(), a.clip.resolve(), a.definitions.resolve(), a.out.resolve(), serve=a.serve,
               host=a.host, port=a.port)


if __name__ == "__main__":
    raise SystemExit(main())
