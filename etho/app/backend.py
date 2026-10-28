"""Pipeline stages for the app, chained the way scripts/run_pipeline.py chains them.

Each `stage_*` method takes a `Ctx`, does one pipeline stage, writes its artifacts under the version
directory, and returns nothing (progress text goes through `ctx.say`). The modules called here
(planner, tracking, rule synthesis, sandbox, revise, tier-1, report) are used as they are; this file only
sequences them, supplies the job's gateway, and handles files.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from etho.adjudication.tier1 import CONFIDENT, adjudicate, apply, make_windows
from etho.perception.planner import plan as run_planner
from etho.report.report import ReportInput
from etho.report.report import build as build_report
from etho.rules.revise import revise as run_revise
from etho.rules.sandbox import SandboxError, run_detector
from etho.rules.synthesize import Detector, synthesize
from etho.tracking.pipeline import count_frames, prepare_chunks, track_video

from . import behaviors as B
from . import corrections as C
from .config import AppConfig
from .jobgw import JobBudgetExceeded, JobCancelled, JobGateway
from .overlay import render_overlay
from .store import SEQ, Project, read_json, write_json
from .validate import ValidationError, remux_clean

FPS = 10.0
CHUNK_DIR = Path("cache/chunks")  # relative to the repo root: tier-1 hard-codes the same path (bin/app chdirs)


class StageSkipped(Exception):
    """A non-essential stage failed; the job continues with what it has (the message is shown)."""


def meta_model(public_data: bool) -> str:
    return "muse-spark-1.3-contributor" if public_data else "muse-spark-1.3"


@dataclass
class Ctx:
    cfg: AppConfig
    project: Project
    job: Any                       # etho.app.jobs.Job
    gw: JobGateway
    outdir: Path                   # where this version's files go
    version: int
    public_data: bool
    say: Any                       # callable(str)
    state: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_detectors(path: Path, dets: list[Detector], meta: dict) -> None:
    write_json(path, {**meta, "detectors": [vars(d) for d in dets]})


def _load_detectors(path: Path) -> tuple[dict, list[Detector]]:
    spec = read_json(path)
    return spec, [Detector(**d) for d in spec["detectors"]]


class Backend:
    """The real pipeline. Tests substitute fakes by subclassing or by patching this module's names."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    # ---- analysis job -----------------------------------------------------------------------
    def prepare(self, ctx: Ctx) -> None:
        """Load what a stage needs from disk (works for the first run and after a restart)."""
        p = ctx.project
        if ctx.job.kind == "analyze":
            inp = read_json(p.base / "input.json")
            ctx.state.update(input=inp, defs=inp["definitions"], names=list(inp["definitions"]),
                             video=inp["video"])
        else:
            res = p.result()
            ctx.state.update(input=read_json(p.base / "input.json", {}), defs=res["behaviors"]["definitions"],
                             names=res["behaviors"]["names"], video=res["video"])
            ctx.state["table"] = dict(np.load(p.seq_dir / "features.npz"))
            # The version this revision builds on: the latest finished one when it was queued (a failed or
            # interrupted earlier revision leaves a gap in the numbering, so rev - 1 is not it).
            prev = int(ctx.job.params.get("base_version", ctx.version - 1))
            ctx.state["base_version"] = prev
            spec, dets = _load_detectors(p.version_dir(prev) / "detectors.json")
            ctx.state.update(dets=dets, detector_meta=spec,
                             prev_labels=np.load(p.version_dir(prev) / "labels.npz")["labels"],
                             prev_rules=np.load(p.version_dir(prev) / "labels_rules.npz")["labels"],
                             prev_record=res["versions"][0] if prev == 0 else p.revision(prev))
        ctx.state["codes"] = B.make_codes(ctx.state["names"])

    def stage_validate(self, ctx: Ctx) -> None:
        v = ctx.state["video"]
        p = ctx.project
        clean = p.base / "clean.mp4"
        if not clean.exists():
            src = p.video_path()
            ctx.say("re-encoding the upload to a clean 10 fps mp4 (the pipeline never sees the original container)")
            remux_clean(src, clean, self.cfg, fps=int(FPS))
            src.unlink(missing_ok=True)                  # keep only what the pipeline needs
        frames = count_frames(clean)
        if frames > (self.cfg.max_duration_s + 2) * FPS:
            raise ValidationError(f"video has {frames} samples at 10 fps; the limit is "
                                  f"{(self.cfg.max_duration_s + 2) * FPS:.0f}", 413, "too_long")
        ctx.say(f"{v['duration_s']:.1f} s, {v['width']}x{v['height']}, {v['codec']}; {frames} samples at 10 fps; "
                f"estimated cost ${ctx.job.est_cost_usd:.2f} (cap check passed)")

    def stage_plan(self, ctx: Ctx) -> None:
        video = ctx.project.video_path()
        p = run_planner(video, gw=ctx.gw, public_data=ctx.public_data, tag=self.cfg.tag)
        write_json(ctx.project.base / "plan.json", asdict(p))
        ctx.state["plan"] = asdict(p)
        ctx.say(f'SAM phrase "{p.phrase}", expecting {p.expected_count} animals ({p.identity_method} identity)')

    def stage_track(self, ctx: Ctx) -> None:
        video = ctx.project.video_path()
        ph = ctx.state["plan"]["phrase"]
        n_in = count_frames(video)             # bound the chunk count (and so SAM cost) before anything is sent
        if -(-n_in // 600) > -(-int((self.cfg.max_duration_s + 2) * FPS) // 600):
            raise ValidationError(f"{n_in} samples would need too many SAM chunks", 413, "too_long")
        ctx.say(f'tracking "{ph}" with SAM 3.1 (10 fps, 60 s chunks in parallel)')
        r = track_video(video, ctx.project.seq_dir, gw=ctx.gw, phrase=ph, public_data=ctx.public_data,
                        stride=None, fps=int(FPS), tag=self.cfg.tag, chunk_dir=CHUNK_DIR)
        ctx.state["table"] = r.table
        both = float(np.mean((r.table["r_present"] > 0) & (r.table["i_present"] > 0)))
        ctx.state["both_present"] = both
        ctx.say(f"{r.n_samples} samples; both animals found in {both:.0%} of them; "
                f"{r.timings['wall_s']:.0f} s ({r.timings['x_realtime']:.2f}x real time)")
        if both < 0.3:
            ctx.warnings.append(f"Both animals were tracked in only {both:.0%} of samples; scores may be unreliable "
                                "(the tracker assumes two animals of different brightness, resident darker).")

    def stage_synthesize(self, ctx: Ctx) -> None:
        names, defs, v = ctx.state["names"], ctx.state["defs"], ctx.state["video"]
        inp = ctx.state["input"]
        table = ctx.state["table"]
        model = meta_model(ctx.public_data)
        ctx.say(f"{model} (effort high) writes one detector per behavior: {', '.join(names)}")

        def one(name: str):
            try:
                return synthesize(name, defs[name], subject=inp["subject"], partner=inp["partner"], tables=[table],
                                  fps=FPS, frame_wh=(v["width"], v["height"]), gw=ctx.gw, model=model,
                                  public_data=ctx.public_data, tag=self.cfg.tag, effort="high"), None
            except (JobCancelled, JobBudgetExceeded):
                raise
            except Exception as e:  # SandboxError after repairs, provider error, ...
                return None, f"{name}: {type(e).__name__}: {e}"[:400]

        with ThreadPoolExecutor(min(4, len(names))) as ex:
            res = list(ex.map(one, names))
        dets = [d for d, _ in res if d is not None]
        for _, err in res:
            if err:
                ctx.warnings.append(f"No detector for {err}")
        if not dets:
            raise RuntimeError("could not write a working detector for any behavior: " + "; ".join(ctx.warnings))
        ctx.state["dets"] = dets
        ctx.state["detector_meta"] = {"definitions": defs, "model": model, "effort": "high",
                                      "stats_from": "this video only"}
        _save_detectors(ctx.outdir / "detectors.json", dets, ctx.state["detector_meta"])
        ctx.say(f"{len(dets)}/{len(names)} detectors passed the sandbox")

    def stage_revise(self, ctx: Ctx) -> None:
        defs = ctx.state["defs"]
        table = ctx.state["table"]
        corr = ctx.job.params["corrections"]            # cumulative, revise format
        ctx.state["corrections"] = corr
        affected = sorted({c["behavior"] for c in corr})
        model = meta_model(ctx.public_data)
        ctx.say(f"{model} (effort high) revises: {', '.join(affected)} from {len(corr)} corrections")

        def one(det: Detector):
            if det.behavior not in affected:
                return det
            return run_revise(det, defs[det.behavior], corr, {SEQ: table}, fps=FPS, gw=ctx.gw, model=model,
                              public_data=ctx.public_data, tag=self.cfg.tag, effort="high")

        old = ctx.state["dets"]
        with ThreadPoolExecutor(max(1, min(4, len(old)))) as ex:
            new = list(ex.map(one, old))
        notes = []
        for o, n in zip(old, new, strict=True):
            if o.behavior not in affected:
                continue
            rejected = next((c for c in n.calls if c.get("revision_rejected")), None)
            chk = (rejected or {}).get("last_check") if rejected else next(
                (c["check"] for c in reversed(n.calls) if c.get("check")), None)
            status = "rejected" if rejected else ("revised" if n.code != o.code else "unchanged")
            notes.append({"behavior": o.behavior, "status": status, "check": chk,
                          "cost_usd": round(sum(c.get("cost_usd", 0.0) for c in n.calls), 6)})
        ctx.state["dets"] = new
        ctx.state["revise_notes"] = notes
        _save_detectors(ctx.outdir / "detectors.json", new, {**ctx.state["detector_meta"],
                                                              "revised_from": ctx.state["base_version"],
                                                              "n_corrections": len(corr)})
        ctx.say("; ".join(f"{n['behavior']}: {n['status']}" for n in notes))

    def stage_predict(self, ctx: Ctx) -> None:
        names, table = ctx.state["names"], ctx.state["table"]
        n = len(next(iter(table.values())))
        masks = {}
        for d in ctx.state["dets"]:
            try:
                masks[d.behavior] = run_detector(d.code, table, FPS)
            except SandboxError as e:
                ctx.warnings.append(f"detector for {d.behavior} failed on this video: {str(e)[:200]}")
        lab = B.labels_from_masks(masks, names, n)
        ctx.state["rules"] = lab
        np.savez_compressed(ctx.outdir / "labels_rules.npz", labels=lab, fps=np.float64(FPS))
        counts = {b: len(bs) for b, bs in B.bouts_by_behavior(lab, names).items()}
        ctx.say("rule bouts: " + ", ".join(f"{b} {c}" for b, c in counts.items()))

    def stage_tier1(self, ctx: Ctx) -> None:
        names, table, rules = ctx.state["names"], ctx.state["table"], ctx.state["rules"]
        codes = ctx.state["codes"]
        final = rules.copy()
        ctx.state["final"] = final
        ctx.state["verdicts"] = []
        try:
            ws = make_windows(SEQ, rules, table, codes, FPS)
            ctx.say(f"{len(ws)} candidate windows; Muse Spark Open (Fireworks, low effort) judges tiled frame strips")
            v = adjudicate(ctx.project.base / "run", SEQ, ws, {"behaviors": ctx.state["defs"]}, gw=ctx.gw,
                           effort="low", tag=self.cfg.tag, out_dir=ctx.project.strips_dir)
        except (JobCancelled, JobBudgetExceeded):
            raise
        except Exception as e:
            ctx.warnings.append(f"Tier-1 adjudication failed ({type(e).__name__}); rule labels are used as they are.")
            raise StageSkipped(f"{type(e).__name__}: {str(e)[:200]}; using rule labels") from e
        final = apply(rules, v, codes, CONFIDENT, allow=B.tier1_allow(names))
        ctx.state["final"], ctx.state["verdicts"] = final, v
        (ctx.outdir / "verdicts.jsonl").write_text("".join(json.dumps(x) + "\n" for x in v))
        np.savez_compressed(ctx.outdir / "labels.npz", labels=final, fps=np.float64(FPS))
        ch = int((final != rules).sum())
        ctx.say(f"{len(v)} windows judged; {ch} samples ({ch / FPS:.1f} s) relabeled")

    def _ensure_final(self, ctx: Ctx) -> None:
        """If tier 1 was skipped the final labels are the rule labels."""
        if not (ctx.outdir / "labels.npz").exists():
            np.savez_compressed(ctx.outdir / "labels.npz", labels=ctx.state["final"], fps=np.float64(FPS))
            (ctx.outdir / "verdicts.jsonl").write_text("")

    def stage_report(self, ctx: Ctx) -> None:
        self._ensure_final(ctx)
        names, final = ctx.state["names"], ctx.state["final"]
        v, job = ctx.state["video"], ctx.job
        meta = {"video": v.get("name", "upload"), "phrase": (ctx.state.get("plan") or {}).get("phrase"),
                "pipeline": ["Muse Spark plans the SAM concept phrase", "SAM 3.1 tracks both animals",
                             f"{meta_model(ctx.public_data)} writes one Python detector per behavior from the "
                             "English definition (sandboxed)", "Muse Spark Open (Fireworks) judges borderline "
                             "bouts from frame strips"] + (["Detectors revised from researcher corrections"]
                                                          if ctx.version else []),
                "behavior_definitions": ctx.state["defs"], "version": ctx.version,
                "costs_usd": {k: round(x, 4) for k, x in ctx.gw.stage_cost.items()},
                "timings_s": {s.name: round(s.elapsed_s, 1) for s in job.stages if s.status == "done"},
                "detectors": [{"behavior": d.behavior, "model": d.model, "prompt_hash": d.prompt_hash,
                               "code": d.code} for d in ctx.state["dets"]],
                "warnings": ctx.warnings}
        inp = ReportInput(f"Etho report: {v.get('name', 'upload')}", final, FPS, meta)
        out = ctx.outdir / "report.html"
        with B.report_behaviors(names):
            try:
                build_report(inp, out, codes=ctx.state["codes"], gw=ctx.gw, public_data=ctx.public_data)
            except Exception as e:  # methods paragraph is the only model call; keep the rest of the report
                ctx.warnings.append(f"Methods paragraph not generated ({type(e).__name__}).")
                build_report(inp, out, codes=ctx.state["codes"], gw=None, public_data=ctx.public_data)
        ctx.say("ethogram, metrics, methods paragraph and audit trail written")

    def stage_overlay(self, ctx: Ctx) -> None:
        info = read_json(ctx.project.seq_dir / "tracking.json")
        chunks = prepare_chunks(Path(info["src"]), CHUNK_DIR, stride=info.get("src_stride"),
                                fps=int(info.get("sample_fps", FPS)))
        r = render_overlay([c.path for c in chunks], ctx.project.seq_dir, ctx.state["final"], ctx.state["names"],
                           FPS, ctx.outdir / "overlay.mp4",
                           progress=lambda i, n: ctx.say(f"rendering overlay video: {i}/{n} frames"))
        ctx.say(f"{r['frames']} frames, H.264")

    # ---- results ----------------------------------------------------------------------------
    def _version_record(self, ctx: Ctx) -> dict:
        names = ctx.state["names"]
        final, rules = ctx.state["final"], ctx.state["rules"]
        return {"v": ctx.version, "bouts": B.bouts_by_behavior(final, names),
                "rule_bouts": B.bouts_by_behavior(rules, names), "metrics": B.metrics(final, names, FPS),
                "has_overlay": (ctx.outdir / "overlay.mp4").exists(),
                "has_report": (ctx.outdir / "report.html").exists(), "created": _now()}

    def _stage_rows(self, ctx: Ctx) -> list[dict]:
        return [{"name": s.name, "status": s.status, "elapsed_s": round(s.elapsed_s, 2),
                 "cost_usd": round(ctx.gw.stage_cost.get(s.name, 0.0), 6), "message": s.message}
                for s in ctx.job.stages]

    def _timing(self, ctx: Ctx) -> dict:
        wall = sum(s.elapsed_s for s in ctx.job.stages)
        video_s = ctx.state["video"]["duration_s"]
        return {"wall_s": round(wall, 2), "video_s": round(video_s, 2),
                "x_realtime": round(wall / video_s, 3) if video_s else None, "complete": True}

    def finalize(self, ctx: Ctx, ledger_cost: float | None = None) -> None:
        """Write result.json (analysis) or revision.json (revision)."""
        names = ctx.state["names"]
        common = {"stages": self._stage_rows(ctx), "timing": self._timing(ctx),
                  "cost": {"total_usd": round(ctx.gw.total_cost, 6), "ledger_usd": ledger_cost,
                           "by_stage": {k: round(x, 6) for k, x in ctx.gw.stage_cost.items()}},
                  "warnings": ctx.warnings}
        rec = self._version_record(ctx)
        if ctx.job.kind == "analyze":
            inp = ctx.state["input"]
            write_json(ctx.project.base / "result.json", {
                "id": ctx.project.id, "title": inp.get("filename") or "upload", "kind": "job",
                "created": inp.get("created"), "public_data": ctx.public_data, "video": ctx.state["video"],
                "fps": FPS, "n_samples": int(len(ctx.state["final"])), "subject": inp["subject"],
                "partner": inp["partner"],
                "behaviors": {"names": names, "definitions": ctx.state["defs"], "codes": ctx.state["codes"],
                              "color_index": B.color_index(names)},
                "plan": ctx.state.get("plan"), "versions": [{**rec, "label": "original"}], **common})
        else:
            prev = ctx.state["prev_record"]
            diff = self._diff(ctx, prev, rec)
            write_json(ctx.outdir / "revision.json", {
                **rec, "label": f"revision {ctx.version}", "corrections": ctx.state["corrections"],
                "revise": ctx.state["revise_notes"], "diff": diff, **common})

    def _diff(self, ctx: Ctx, prev: dict, new: dict) -> dict:
        names, corr = ctx.state["names"], ctx.state["corrections"]
        per = {b: {"bouts_before": prev["metrics"][b]["bouts"], "bouts_after": new["metrics"][b]["bouts"],
                   "total_s_before": prev["metrics"][b]["total_s"], "total_s_after": new["metrics"][b]["total_s"]}
               for b in names}
        notes = ctx.state["revise_notes"]
        rule = {"before": sum((n["check"] or {}).get("fixed_old", 0) for n in notes),
                "after": sum((n["check"] or {}).get("fixed_old" if n["status"] != "revised" else "fixed_new", 0)
                             for n in notes),
                "n": sum((n["check"] or {}).get("n", 0) for n in notes)}
        before = C.satisfied(ctx.state["prev_labels"], corr, names)
        after = C.satisfied(ctx.state["final"], corr, names)
        return {"per_behavior": per, "relabeled_s": round(float((ctx.state["prev_labels"] != ctx.state["final"]).sum())
                                                          / FPS, 1),
                "corrections": {"n": len(corr), "final_before": before["satisfied"], "final_after": after["satisfied"],
                                "rule_before": rule["before"], "rule_after": rule["after"], "rule_n": rule["n"]}}


__all__ = ["Backend", "Ctx", "StageSkipped"]
