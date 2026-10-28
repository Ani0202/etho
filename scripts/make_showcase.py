"""Package an existing pipeline output (data/runs/<run>/<seq>/ ...) as a showcase for the web app.

  uv run python scripts/make_showcase.py --name calms21-mouse039 --run-dir data/runs/R1 \
      --seq mouse039_task1_annotator1 --final-pred pred_dev_t1 --rules-pred pred_dev \
      --detectors data/runs/R1/detectors.json --verdicts data/runs/R1/verdicts_dev_t1.jsonl \
      --strips data/runs/R1/strips/dev --report data/runs/R1/report_mouse039_task1_annotator1.html

Writes data/showcase/<name>/ in the same layout the app uses for a finished job, so the app shows it with
zero spend (the overlay video is rendered here from the stored RLE masks; no model is called). The output stays
gitignored: it contains dataset video. Product code reads no labels; this script reads one only when it is handed
--expert-labels (a display-only comparison row).
To package a finished run of the web app itself (real timings, prompts, planner steps, per-call costs), use
scripts/package_job_showcase.py instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etho.app import behaviors as B  # noqa: E402
from etho.app.overlay import render_overlay  # noqa: E402
from etho.app.packaging import expert_record  # noqa: E402
from etho.app.store import write_json  # noqa: E402
from etho.gateway import prices  # noqa: E402
from etho.tracking.pipeline import prepare_chunks  # noqa: E402

FPS = 10.0
FW_MODEL = "accounts/fireworks/models/muse-spark-open"


def call_cost(provider: str, model: str, u: dict) -> float:
    try:
        return prices.token_cost(provider, model, int(u.get("input_tokens", 0)),
                                 int(u.get("billed_output_tokens", u.get("output_tokens", 0))),
                                 int(u.get("cached_tokens", 0)))
    except ValueError:
        return 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--seq", required=True)
    ap.add_argument("--final-pred", required=True, help="dir (under run-dir) with <seq>.npz final labels")
    ap.add_argument("--rules-pred", required=True, help="dir (under run-dir) with <seq>.npz rule-level labels")
    ap.add_argument("--detectors", required=True, type=Path)
    ap.add_argument("--verdicts", type=Path, help="tier-1 verdicts jsonl (rows for --seq are used)")
    ap.add_argument("--strips", type=Path, help="dir with tier-1 strip jpgs named <seq>_<s0>_<s1>.jpg")
    ap.add_argument("--report", type=Path, help="existing report html to reuse (default: build without a methods "
                                                "paragraph; no model call)")
    ap.add_argument("--definitions", type=Path, default=Path("configs/calms21_definitions.json"))
    ap.add_argument("--title")
    ap.add_argument("--description", default="")
    ap.add_argument("--out", type=Path, default=Path("data/showcase"))
    ap.add_argument("--expert-labels", type=Path,
                    help="optional labels .npz (key `labels`, 10 fps codes in behavior order) drawn as an "
                         "'Expert (CalMS21)' row under Etho's; display only. Use only for dev-split sequences")
    ap.add_argument("--allow-test-split", action="store_true",
                    help="allow an expert row for a TEST-split sequence (display only); dev needs no flag")
    ap.add_argument("--expert-start-s", type=float, default=0.0, help="offset of the video inside the labels")
    ap.add_argument("--no-public-data", action="store_true",
                    help="mark the run as non-public data (revisions then use Standard models)")
    a = ap.parse_args()

    expert = None
    if a.expert_labels:      # computed first: a bad labels file must not leave a half-built showcase
        n_exp = len(np.load(a.run_dir / a.final_pred / f"{a.seq}.npz")["labels"])
        expert = expert_record(a.expert_labels, list(json.loads(a.definitions.read_text())["behaviors"]), n_exp,
                               FPS, a.expert_start_s, seq=a.seq, allow_test=a.allow_test_split)
    out = a.out / a.name
    if out.exists():
        shutil.rmtree(out)
    seq_in = a.run_dir / a.seq
    seq_out = out / "run" / "video"
    seq_out.mkdir(parents=True)
    info = json.loads((seq_in / "tracking.json").read_text())
    src = Path(info["src"])
    if not src.exists():
        sys.exit(f"source video not found: {src}")
    shutil.copy2(src, out / "source.mp4")
    for p in [seq_in / "features.npz", *sorted(seq_in.glob("raw_c*.jsonl.gz"))]:
        shutil.copy2(p, seq_out / p.name)
    info["src"] = str(out / "source.mp4")          # relative to the repo root, like the app's own jobs
    (seq_out / "tracking.json").write_text(json.dumps(info, indent=1))

    final = np.load(a.run_dir / a.final_pred / f"{a.seq}.npz")["labels"]
    rules = np.load(a.run_dir / a.rules_pred / f"{a.seq}.npz")["labels"]
    defs_file = json.loads(a.definitions.read_text())
    defs = defs_file["behaviors"]
    names = list(defs)
    assert names == ["attack", "investigation", "mount"], names   # label codes in these runs follow this order
    n = len(final)
    np.savez_compressed(out / "labels.npz", labels=final, fps=np.float64(FPS))
    np.savez_compressed(out / "labels_rules.npz", labels=rules, fps=np.float64(FPS))

    spec = json.loads(a.detectors.read_text())
    spec["definitions"] = defs
    write_json(out / "detectors.json", spec)

    verdicts, t1_cost = [], 0.0
    if a.verdicts and a.verdicts.exists():
        for line in a.verdicts.read_text().splitlines():
            v = json.loads(line)
            if v["seq"] != a.seq:
                continue
            verdicts.append({**v, "seq": "video"})
            t1_cost += call_cost("fireworks", FW_MODEL, {"input_tokens": v.get("in_tok") or 0,
                                                         "output_tokens": v.get("out_tok") or 0})
            if a.strips and (a.strips / f"{a.seq}_{v['s0']}_{v['s1']}.jpg").exists():
                dst = out / "strips" / f"video_{v['s0']}_{v['s1']}.jpg"
                dst.parent.mkdir(exist_ok=True)
                shutil.copy2(a.strips / f"{a.seq}_{v['s0']}_{v['s1']}.jpg", dst)
    (out / "verdicts.jsonl").write_text("".join(json.dumps(v) + "\n" for v in verdicts))

    # Costs are list-price estimates from recorded token and frame counts: the original run's calls were
    # cache hits on re-runs, so the ledger rows for them say $0.
    model = spec.get("model", "muse-spark-1.3-contributor")
    synth = sum(call_cost("meta", model, c.get("usage", {})) for d in spec["detectors"] for c in d["calls"])
    n_chunks, rem = divmod(n, 600)
    sam = sum(prices.sam_video_cost(k) for k in [600] * n_chunks + ([rem] if rem else []))
    rec_costs = {"track": round(sam, 4), "synthesize": round(synth, 4), "tier1": round(t1_cost, 4)}

    chunks = prepare_chunks(Path(info["src"]), Path("cache/chunks"), stride=info.get("src_stride"),
                            fps=int(info.get("sample_fps", FPS)))
    ov = render_overlay([c.path for c in chunks], seq_out, final, names, FPS, out / "overlay.mp4",
                        progress=lambda i, m: print(f"overlay {i}/{m}", flush=True))
    print(f"overlay: {ov['frames']} frames")

    if a.report and a.report.exists():
        shutil.copy2(a.report, out / "report.html")
    else:
        from etho.report.report import ReportInput, build
        meta = {"video": f"{a.seq}.mp4", "pipeline": "precomputed showcase (no methods paragraph: no model call)"}
        with B.report_behaviors(names):
            build(ReportInput(f"Etho report: {a.seq}", final, FPS, meta), out / "report.html",
                  codes=B.make_codes(names), gw=None, public_data=True)

    dur = n / FPS
    stages = [{"name": s, "status": st, "elapsed_s": None, "cost_usd": rec_costs.get(s, 0.0), "message": m}
              for s, st, m in [
                  ("validate", "done", "dataset video"),
                  ("plan", "skipped", 'fixed SAM phrase "mouse" (the perception planner was not run for this video)'),
                  ("track", "done", f"SAM 3.1, {n} samples at 10 fps"),
                  ("synthesize", "done", f"{model}, effort high, one detector per behavior"),
                  ("predict", "done", "detectors run in the sandbox"),
                  ("tier1", "done", f"{len(verdicts)} windows judged by Muse Spark Open (Fireworks, low effort)"),
                  ("report", "done", "report built from the stored run"),
                  ("overlay", "done", "rendered from the stored RLE masks")]]
    version = {"v": 0, "label": "original", "bouts": B.bouts_by_behavior(final, names),
               "rule_bouts": B.bouts_by_behavior(rules, names), "metrics": B.metrics(final, names, FPS),
               "has_overlay": True, "has_report": True,
               "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    write_json(out / "result.json", {
        "id": f"sc-{a.name}", "kind": "showcase", "title": a.title or f"CalMS21 {a.seq}", "description": a.description,
        "created": version["created"], "public_data": not a.no_public_data,
        "dataset": {"name": "CalMS21 (task 1)", "license": "CC BY 4.0",
                    "url": "https://data.caltech.edu/records/s0vdx-0k302"},
        "video": {"name": f"{a.seq}.mp4", "duration_s": round(dur, 2), "width": int(info.get("width", 1024)),
                  "height": int(info.get("height", 570)), "fps": 30.0, "codec": "h264"},
        "fps": FPS, "n_samples": n, "subject": defs_file["subject"], "partner": defs_file["partner"],
        "behaviors": {"names": names, "definitions": defs, "codes": B.make_codes(names),
                      "color_index": B.color_index(names)},
        "plan": {"phrase": info.get("phrase", "mouse"), "steps": []}, "versions": [version], "stages": stages,
        "timing": {"wall_s": None, "video_s": round(dur, 2), "x_realtime": None, "complete": False,
                   "note": "precomputed run: wall-clock was not recorded for this video; run an upload for the "
                           "measured end-to-end time"},
        "cost": {"total_usd": round(sum(rec_costs.values()), 4), "ledger_usd": None, "by_stage": rec_costs,
                 "estimated": True},
        "warnings": [],
        **({"expert": expert} if expert else {}),
        "audit_note": "Precomputed run: prompts and responses were not captured, so the audit shows each model "
                      "call's recorded token usage and the detector code. Costs are list-price estimates from "
                      "recorded usage."})
    print(f"showcase written to {out}")


if __name__ == "__main__":
    main()
