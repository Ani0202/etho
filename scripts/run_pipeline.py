"""Run Etho on a CalMS21 split: track -> synthesize detectors -> predict -> (score with eval/).

  uv run python scripts/run_pipeline.py track   --split dev --run R1
  uv run python scripts/run_pipeline.py synth   --split dev --run R1 [--model muse-spark-1.3-contributor]
  uv run python scripts/run_pipeline.py predict --split dev --run R1 --detectors data/runs/R1/detectors.json
  uv run python -m eval.score --split dev --pred-dir data/runs/R1/pred

Product code never reads labels: this script only touches videos and features. Scoring is eval/'s job.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from etho.gateway import Gateway
from etho.rules.sandbox import run_detector
from etho.rules.synthesize import Detector, synthesize
from etho.tracking.pipeline import track_video

SPLITS = json.loads(Path("eval/splits/calms21_v1.json").read_text())
PRIORITY = ("mount", "attack", "investigation")  # overlap resolution, same default as eval/score.py
CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}


def seqs_for(split: str) -> list[str]:
    return SPLITS[split]


def video_for(seq: str, split: str) -> Path:
    sub = "test" if split == "test" else "train"
    return Path(f"data/calms21/videos/{sub}/{seq}.mp4")


def cmd_track(a) -> None:
    gw = Gateway()
    out = Path("data/runs") / a.run
    rows = []
    for seq in seqs_for(a.split):
        t0 = time.monotonic()
        r = track_video(video_for(seq, a.split), out / seq, gw=gw, public_data=True, workers=a.workers,
                        tag=a.tag)
        rows.append({"seq": seq, **r.timings, "cost_usd": r.cost_usd, "outer_wall_s": time.monotonic() - t0})
        print(json.dumps(rows[-1], default=float), flush=True)
    tot_v = sum(r["video_s"] for r in rows)
    tot_w = sum(r["wall_s"] for r in rows)
    summary = {"split": a.split, "videos": len(rows), "video_s": tot_v, "wall_s": tot_w,
               "x_realtime": tot_w / tot_v, "cost_usd": sum(r["cost_usd"] for r in rows), "per_video": rows}
    (out / f"track_{a.split}.json").write_text(json.dumps(summary, indent=1, default=float))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_video"}, default=float))


def load_tables(run: str, split: str) -> dict[str, dict[str, np.ndarray]]:
    return {s: dict(np.load(Path("data/runs") / run / s / "features.npz")) for s in seqs_for(split)}


def cmd_synth(a) -> None:
    defs = json.loads(Path(a.definitions).read_text())
    tables = list(load_tables(a.run, a.split).values())
    gw = Gateway()
    dets = []
    for name, definition in defs["behaviors"].items():
        d = synthesize(name, definition, subject=defs["subject"], partner=defs["partner"], tables=tables, fps=10.0,
                       frame_wh=(1024, 570), gw=gw, model=a.model, public_data=True, tag="eval-llm",
                       effort=a.effort)
        dets.append(d)
        print(name, "ok", [c["cost_usd"] for c in d.calls], flush=True)
    path = Path("data/runs") / a.run / "detectors.json"
    path.write_text(json.dumps({"definitions": a.definitions, "model": a.model, "effort": a.effort,
                                "stats_from_split": a.split, "detectors": [vars(d) for d in dets]},
                               indent=1, default=float))
    print(path)


def cmd_predict(a) -> None:
    spec = json.loads(Path(a.detectors).read_text())
    dets = [Detector(**d) for d in spec["detectors"]]
    out = Path("data/runs") / a.run / (a.pred_name or f"pred_{a.split}")
    out.mkdir(parents=True, exist_ok=True)
    t_all = 0.0
    for seq, t in load_tables(a.run, a.split).items():
        t0 = time.monotonic()
        masks = {d.behavior: run_detector(d.code, t, 10.0) for d in dets}
        n = len(next(iter(t.values())))
        lab = np.full(n, CODES["other"], np.int8)
        for b in reversed(PRIORITY):  # lowest priority first, so higher ones overwrite
            if b in masks:
                lab[masks[b]] = CODES[b]
        np.savez_compressed(out / f"{seq}.npz", labels=lab, sample_fps=np.float64(10.0), sample_stride=np.int64(3))
        t_all += time.monotonic() - t0
        print(seq, {b: int(m.sum()) for b, m in masks.items()}, flush=True)
    (out / "manifest.json").write_text(json.dumps({"sample_fps": 10, "detectors": a.detectors,
                                                   "predict_wall_s": t_all}, indent=1))
    print(out)


def cmd_adjudicate(a) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from etho.adjudication.tier1 import CONFIDENT, adjudicate, apply, make_windows

    defs = json.loads(Path(a.definitions).read_text())
    run_dir = Path("data/runs") / a.run
    src = run_dir / (a.pred_in or f"pred_{a.split}")
    sfx = "" if a.effort_t1 == "low" else f"_{a.effort_t1}"
    out = run_dir / (a.pred_out or f"pred_{a.split}_t1{sfx}")
    out.mkdir(parents=True, exist_ok=True)
    gw = Gateway()
    tables = load_tables(a.run, a.split)

    def one(seq):
        lab = np.load(src / f"{seq}.npz")["labels"]
        ws = make_windows(seq, lab, tables[seq], CODES, 10.0)
        t0 = time.monotonic()
        v = adjudicate(run_dir, seq, ws, defs, gw=gw, effort=a.effort_t1,
                       out_dir=run_dir / "strips" / a.split)  # same strips for every effort
        return seq, lab, v, time.monotonic() - t0

    t0 = time.monotonic()
    with ThreadPoolExecutor(len(tables)) as ex:
        res = list(ex.map(one, tables))
    allv = []
    for seq, lab, v, dt in res:
        new = apply(lab, v, CODES, a.confident or CONFIDENT)
        np.savez_compressed(out / f"{seq}.npz", labels=new, sample_fps=np.float64(10.0), sample_stride=np.int64(3))
        allv += v
        print(seq, len(v), "windows", f"{dt:.0f}s", f"${sum(x['cost_usd'] for x in v):.3f}", flush=True)
    (run_dir / f"verdicts_{out.name}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in allv))
    from etho.adjudication.tier1 import DEFAULT_ALLOW
    (out / "manifest.json").write_text(json.dumps({"sample_fps": 10, "tier1_model": "fireworks muse-spark-open",
                                                   "input": str(src), "allow": sorted(DEFAULT_ALLOW),
                                                   "effort": a.effort_t1, "confident": a.confident or CONFIDENT,
                                                   "windows": len(allv),
                                                   "cost_usd": sum(x["cost_usd"] for x in allv),
                                                   "wall_s": time.monotonic() - t0}, indent=1))
    print(f"{len(allv)} windows, ${sum(x['cost_usd'] for x in allv):.3f}, {time.monotonic() - t0:.0f}s -> {out}")


def cmd_adjudicate2(a) -> None:
    from etho.adjudication.tier2 import adjudicate_clips, disagreements

    defs = json.loads(Path(a.definitions).read_text())
    run_dir = Path("data/runs") / a.run
    v1 = [json.loads(x) for x in (run_dir / f"verdicts_{a.split}_t1.jsonl").read_text().splitlines()]
    esc = disagreements(v1)
    gw = Gateway()
    t0 = time.monotonic()
    by_seq = {}
    for v in esc:
        by_seq.setdefault(v["seq"], []).append(v)
    allv = []
    for seq, ws in by_seq.items():
        r = adjudicate_clips(run_dir, seq, ws, defs, gw=gw, effort=a.effort_t2)
        allv += r
        print(seq, len(r), f"${sum(x['cost_usd'] for x in r):.3f}", flush=True)
    (run_dir / f"verdicts_{a.split}_t2.jsonl").write_text("".join(json.dumps(x) + "\n" for x in allv))
    print(f"{len(allv)} clips, ${sum(x['cost_usd'] for x in allv):.3f}, {time.monotonic() - t0:.0f}s")


def cmd_revise(a) -> None:
    from etho.rules.revise import revise

    defs = json.loads(Path(a.definitions).read_text())
    spec = json.loads(Path(a.detectors).read_text())
    corr = json.loads(Path(a.corrections).read_text())
    corr = corr["corrections"] if isinstance(corr, dict) else corr
    tables = load_tables(a.run, "dev")
    gw = Gateway()
    out = []
    for d in spec["detectors"]:
        det = Detector(**d)
        new = revise(det, defs["behaviors"][det.behavior], corr, tables, gw=gw, model=a.model, public_data=True,
                     effort=a.effort)
        out.append(new)
        status = "revised" if new is not det else "unchanged"
        print(det.behavior, status, [c["cost_usd"] for c in new.calls], flush=True)
    path = Path("data/runs") / a.run / f"detectors_revised_{Path(a.corrections).stem}.json"
    path.write_text(json.dumps({**{k: v for k, v in spec.items() if k != "detectors"}, "revised_from": a.detectors,
                                "corrections": a.corrections, "n_corrections": len(corr),
                                "detectors": [vars(x) for x in out]}, indent=1, default=float))
    print(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["track", "synth", "predict", "adjudicate", "adjudicate2", "revise"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--run", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="eval-sam")
    ap.add_argument("--definitions", default="configs/calms21_definitions.json")
    ap.add_argument("--model", default="muse-spark-1.3-contributor")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--detectors")
    ap.add_argument("--effort-t1", default="low")
    ap.add_argument("--confident", type=float)
    ap.add_argument("--effort-t2", default="high")
    ap.add_argument("--corrections")
    ap.add_argument("--pred-in")
    ap.add_argument("--pred-out")
    ap.add_argument("--pred-name", help="output dir name for predict (default pred_<split>)")
    a = ap.parse_args()
    {"track": cmd_track, "synth": cmd_synth, "predict": cmd_predict, "adjudicate": cmd_adjudicate,
     "adjudicate2": cmd_adjudicate2, "revise": cmd_revise}[a.cmd](a)


if __name__ == "__main__":
    main()
