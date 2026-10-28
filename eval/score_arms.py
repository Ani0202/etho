"""Score several prediction arms in ONE logged execution (docs/EVAL_PLAN.md section 7, test-run protocol).

Test runs must use this entry point (`eval.score` refuses the test split): every arm of a test run is hashed
and written to the run log in a single `started` record before any label file is opened, then all arms are
scored against the same labels, with paired sequence-bootstrap differences between arms.

    uv run python -m eval.score_arms --split test --purpose test-1 --confirm-test-run \
        --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
        --arm zs_t1=<dir> --arm zs_rules=<dir> --arm fs_t1=<dir> --arm fs_rules=<dir> \
        --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1

Each `--arm name=dir` is a prediction directory in the `eval.score` format. `--compare a:b` reports b minus a.

On the test split:
- `--frozen-config` is required; every {"path", "sha256"} pair in it is re-hashed and must match, or the run
  stops before the `started` record (nothing is logged as a test run).
- Per-sequence metrics are **sealed**: they are written only to `<run_id>.per_sequence.sealed.json` (its
  sha256 is in the log), not to the run log, the result JSON, or the console. EVAL_PLAN section 7 says when
  it may be opened.
- Targets (`--targets`) are evaluated mechanically for the primary arm and printed as MET / MET ROBUSTLY /
  NOT MET (direction-aware: for lower-is-better targets MET ROBUSTLY needs the CI *upper* bound at or below
  the threshold).

Pre-label checks (EVAL_PLAN amendment A1), all label-free and all run before the `started` record. Any
unexplained problem refuses the run: a `kind: "preflight"` record with `status: "refused"` is logged (it is not
a scoring execution, so it does not use up a test run) and the scorer exits.
- Prediction validation (`validate_predictions`): a file for every sequence in every arm; npz/jsonl syntax;
  sample_fps / sample_stride declarations; label codes; per-sequence lengths against the decoded source frame
  counts in the pipeline manifest (`frames.<seq>.src_frames`, `n_samples == ceil(src_frames / stride)`), and
  against the expected annotation length (`frames` field of the CalMS21 index file; no per-frame label is
  read); failed-sequence placeholders must be declared in `failed_sequences` and be all "other". Strict on test.
  A length mismatch can be accepted only by naming it: `--accept-length-mismatch SEQ=REASON` (logged).
- Execution check (`verify_execution`, whenever `--frozen-config` is given; strict on test): the four named
  arms and the primary arm; each manifest's `config` (frozen sha256, frozen_at_commit, run_commit, dirty
  paths at run time); `git diff <frozen_at_commit> <run_commit> -- etho/` empty; the tier-1 prompt at
  run_commit hashes to `tier1.prompt_sha256`; the frozen config and every frozen asset are identical at
  run_commit.
- Prediction payload hashes (`payload_sha256`, the per-sequence files only) are logged separately from the
  manifest hash, because manifests legitimately carry timing and provenance that change between executions.

`--preflight-only` runs the checks above, logs the preflight record, and stops; it never opens a label file.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import math
import subprocess
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from eval import common, score
from eval.common import BEHAVIORS, NATIVE_FPS

SEALED_SUFFIX = ".per_sequence.sealed.json"
TEST_ARMS = ("zs_t1", "zs_rules", "fs_t1", "fs_rules")  # EVAL_PLAN 7.1
TEST_PRIMARY = "zs_t1"
TEST_COMPARES = (("zs_rules", "zs_t1"), ("zs_t1", "fs_t1"), ("fs_rules", "fs_t1"), ("zs_rules", "fs_rules"))
PROMPT_FILE = "etho/adjudication/tier1.py"  # tier-1 prompt template, module constant PROMPT
FROZEN_TREES = ("etho/",)  # must be identical between frozen_at_commit and run_commit
NO_DIRTY_PREFIXES = ("etho/", "configs/", "scripts/test_run.py")  # must be clean when the runner ran
OTHER_CODE = common.VOCAB["other"]
BOOTSTRAP_SCOPE = ("Sequence-level bootstrap: resamples the scored sequences with replacement, conditional on "
                   "this fitted pipeline and these realized predictions. It does not cover the development "
                   "choices (prompts, thresholds, cascade rules selected on labeled dev videos), provider "
                   "sampling noise (one execution), other laboratories, setups, or species. It treats sequences "
                   "as independent units, which is not verified (consecutive test sequences may share animals "
                   "or sessions).")


def _git(*args: str) -> tuple[int, bytes]:
    try:
        r = subprocess.run(["git", *args], capture_output=True)
    except FileNotFoundError:
        return 127, b""
    return r.returncode, r.stdout


def _commit_ok(c) -> bool:
    return isinstance(c, str) and bool(c) and _git("rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")[0] == 0


def prompt_sha_at(commit: str, path: str = PROMPT_FILE, name: str = "PROMPT") -> str | None:
    """sha256 of the module-level string constant `name` in `path` at `commit` (read with ast, not imported)."""
    rc, out = _git("show", f"{commit}:{path}")
    if rc != 0:
        return None
    try:
        tree = ast.parse(out.decode())
    except SyntaxError:
        return None
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else \
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            try:
                v = ast.literal_eval(node.value)
            except ValueError:
                return None
            return hashlib.sha256(v.encode()).hexdigest() if isinstance(v, str) else None
    return None


def blob_sha_at(commit: str, path: str) -> str | None:
    rc, out = _git("show", f"{commit}:{path}")
    return hashlib.sha256(out).hexdigest() if rc == 0 else None


def parse_arm(spec: str) -> tuple[str, Path]:
    name, sep, d = spec.partition("=")
    if not sep or not name or not d:
        raise SystemExit(f"--arm must be name=dir, got {spec!r}")
    return name, Path(d)


def verify_frozen_config(path: Path) -> tuple[dict, dict]:
    """Re-hash every {"path": ..., "sha256": ...} entry in the frozen config (working tree).

    Returns (info, cfg); info["mismatches"] lists every difference (the caller refuses the run on any)."""
    cfg = json.loads(Path(path).read_text())
    checked, bad = {}, []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("path"), str) and isinstance(o.get("sha256"), str):
                p = Path(o["path"])
                actual = common.sha256_file(p) if p.exists() else None
                checked[o["path"]] = actual
                if actual != o["sha256"]:
                    bad.append(f"{o['path']}: frozen {o['sha256'][:12]}, now {actual and actual[:12]}")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(cfg)
    return {"path": str(path), "sha256": common.sha256_file(Path(path)), "files_checked": checked,
            "name": cfg.get("name"), "frozen_at_commit": cfg.get("frozen_at_commit"),
            "mismatches": [f"frozen config mismatch: {b}" for b in bad]}, cfg


# --------------------------------------------------------------------------- label-free pre-label checks

def expected_frames(split: str, seqs: list[str], labels_dir: Path) -> tuple[dict | None, dict]:
    """Expected annotation length per sequence from the CalMS21 index file (`frames` field only).

    The index also holds per-sequence behavior counts (aggregates disclosed in EVAL_PLAN section 1); they are
    not used here, and no per-frame label file is opened."""
    p = Path(labels_dir) / f"index_calms21_task1_{common.source_dir(split)}.json"
    if not p.exists():
        return None, {"path": str(p), "present": False}
    idx = json.loads(p.read_text())
    out = {s: idx[s]["frames"] for s in seqs if s in idx and isinstance(idx[s].get("frames"), int)}
    return out, {"path": str(p), "present": True, "sha256": common.sha256_file(p),
                 "missing": [s for s in seqs if s not in out]}


def _inspect_file(path: Path, manifest: dict) -> tuple[dict, list[str]]:
    """Syntax check of one prediction file, without labels. Returns (info, problems)."""
    name, probs = path.name, []
    info: dict = {"file": name}
    man_fps = manifest.get("sample_fps")
    try:
        if path.suffix == ".npz":
            info["format"] = "npz"
            with np.load(path, allow_pickle=False) as d:
                keys = set(d.files)
                if "labels" not in keys:
                    return info, [f"{name}: npz has no 'labels' array"]
                lab = np.asarray(d["labels"])
                fps_arr = d["sample_fps"] if "sample_fps" in keys else None
                stride_arr = d["sample_stride"] if "sample_stride" in keys else None
                vocab = str(d["vocab"]) if "vocab" in keys else None
            if lab.ndim != 1:
                probs.append(f"{name}: labels must be 1-D, got shape {lab.shape}")
            if lab.size and lab.dtype.kind not in "iu":
                probs.append(f"{name}: labels must be integers, got dtype {lab.dtype}")
            else:
                bad = sorted(set(np.unique(lab).tolist()) - set(common.VOCAB.values()))
                if bad:
                    probs.append(f"{name}: label codes {bad} not in {common.VOCAB}")
            if vocab is not None and json.loads(vocab) != common.VOCAB:
                probs.append(f"{name}: npz vocab must equal {common.VOCAB}")
            npz_fps = None
            if fps_arr is not None:
                if np.asarray(fps_arr).size != 1:
                    probs.append(f"{name}: sample_fps must be a scalar")
                else:
                    npz_fps = float(np.asarray(fps_arr).reshape(-1)[0])
            if npz_fps is not None and man_fps is not None and float(man_fps) != npz_fps:
                probs.append(f"{name}: sample_fps {npz_fps} in file != {man_fps} in manifest")
            fps = npz_fps if npz_fps is not None else man_fps
            try:
                step = score.step_from_fps(fps)
            except ValueError as e:
                probs.append(f"{name}: {e}")
                step = None
            if stride_arr is not None and step is not None:
                st = np.asarray(stride_arr).reshape(-1)
                if st.size != 1 or int(st[0]) != step:
                    probs.append(f"{name}: sample_stride {st.tolist()} != 30 / sample_fps = {step}")
            info |= {"sample_fps": fps, "step": step, "n": int(lab.size),
                     "all_other": bool(lab.size == 0 or np.all(lab == OTHER_CODE))}
        elif path.suffix == ".jsonl":
            info["format"] = "jsonl"
            bouts, meta = score.read_bouts_jsonl(path)
            fps = meta.get("sample_fps", man_fps)
            try:
                step = score.step_from_fps(fps)
            except ValueError as e:
                probs.append(f"{name}: {e}")
                step = None
            max_end, n_beh = 0, 0
            for i, bt in enumerate(bouts):
                b = bt["behavior"]
                if b not in (*BEHAVIORS, "other"):
                    probs.append(f"{name}: bout {i}: unknown behavior {b!r}")
                    continue
                s0, e0 = bt["start_frame"], bt["end_frame"]
                if not (isinstance(s0, int) and isinstance(e0, int)) or s0 < 0 or e0 <= s0:
                    probs.append(f"{name}: bout {i}: invalid [{s0}, {e0}) (need ints, 0 <= start < end)")
                    continue
                max_end = max(max_end, e0)
                n_beh += b != "other"
            info |= {"sample_fps": fps, "step": step, "n_bouts": len(bouts), "max_end": max_end,
                     "all_other": n_beh == 0}
        else:
            probs.append(f"{name}: unsupported prediction file type")
    except Exception as e:  # unreadable / malformed file
        probs.append(f"{name}: unreadable: {e!r}")
    return info, probs


def validate_predictions(seqs: list[str], arms: dict, manifests: dict, files_by_arm: dict,
                         expected: dict | None, strict: bool, accept: dict | None = None) -> tuple[dict, list]:
    """Label-free validation of every arm (see module docstring). Returns (report, unexplained problems).

    Length problems for a sequence named in `accept` ({seq: reason}) are recorded as accepted, not raised.
    Syntax, missing-file, and declaration problems cannot be accepted."""
    accept = accept or {}
    problems: list[str] = []
    accepted: list[str] = []
    report: dict = {"strict": strict, "expected_frames_checked": expected is not None, "arms": {},
                    "accepted_length_mismatches": accepted, "accept_reasons": accept}

    def length_problem(seq, msg):
        (accepted if seq in accept else problems).append(msg)

    unknown_accept = sorted(set(accept) - set(seqs))
    if unknown_accept:
        problems.append(f"--accept-length-mismatch names sequences not in the split: {unknown_accept}")
    if strict and expected is None:
        problems.append("expected annotation lengths (CalMS21 index `frames`) not available")
    frames_by_arm, failed_by_arm = {}, {}
    for arm in arms:
        man = manifests[arm]
        files = files_by_arm[arm]
        frames = man.get("frames") if isinstance(man.get("frames"), dict) else None
        failed_decl = man.get("failed_sequences")
        if strict and frames is None:
            problems.append(f"{arm}: manifest has no `frames` (decoded src_frames / n_samples per sequence)")
        if strict and not isinstance(failed_decl, list):
            problems.append(f"{arm}: manifest has no `failed_sequences` list (use [] when none failed)")
        failed = set(failed_decl) if isinstance(failed_decl, list) else set()
        if failed - set(seqs):
            problems.append(f"{arm}: failed_sequences not in the split: {sorted(failed - set(seqs))}")
        frames_by_arm[arm], failed_by_arm[arm] = frames, sorted(failed)
        arm_rep = {"missing": [s for s in seqs if files[s] is None], "failed_declared": sorted(failed),
                   "lengths_checked": 0, "formats": {}}
        for s in arm_rep["missing"]:
            problems.append(f"{arm}: no prediction file for {s}")
        for s in seqs:
            f = files[s]
            if f is None:
                continue
            info, probs = _inspect_file(Path(f), man)
            problems += [f"{arm}: {x}" for x in probs]
            arm_rep["formats"][info.get("format")] = arm_rep["formats"].get(info.get("format"), 0) + 1
            fr = (frames or {}).get(s)
            if frames is not None and not isinstance(fr, dict):
                problems.append(f"{arm}: {s}: no entry in manifest `frames`")
                fr = None
            src = fr.get("src_frames") if fr else None
            ns = fr.get("n_samples") if fr else None
            if fr is not None and not isinstance(src, int):
                problems.append(f"{arm}: {s}: manifest src_frames is {src!r}, need the decoded frame count")
                src = None
            exp = expected.get(s) if expected else None
            if expected is not None and exp is None:
                problems.append(f"{arm}: {s}: no expected annotation length in the index")
            if src is not None and exp is not None and src != exp:
                length_problem(s, f"{arm}: {s}: decoded src_frames {src} != expected annotation length {exp}")
            T = src if src is not None else exp
            step = info.get("step")
            is_failed = s in failed
            if fr is not None and ns is None and not is_failed:
                problems.append(f"{arm}: {s}: n_samples missing but the sequence is not in failed_sequences")
            if ns is not None and (not isinstance(ns, int) or isinstance(ns, bool)):
                problems.append(f"{arm}: {s}: n_samples {ns!r} is not an int")
                ns = None
            if ns is not None and src is not None and step and ns != math.ceil(src / step):
                length_problem(s, f"{arm}: {s}: n_samples {ns} != ceil(src_frames {src} / {step}) = "
                                  f"{math.ceil(src / step)}")
            if is_failed and not info.get("all_other", False):
                problems.append(f"{arm}: {s}: declared failed but its placeholder is not all 'other'")
            if probs or not step:
                continue
            if exp is not None:  # never waivable: the scorer could only handle this by an undeclared truncation
                limit = exp if step == 1 else math.ceil(exp / step)
                n_have = info["n"] if info["format"] == "npz" else None
                if n_have is not None and n_have > limit:
                    problems.append(f"{arm}: {s}: {n_have} labels > {limit} possible for the {exp}-frame "
                                    f"annotation at stride {step} (unscorable, cannot be accepted)")
                if info["format"] == "jsonl" and info["max_end"] > exp:
                    problems.append(f"{arm}: {s}: bout ends at {info['max_end']} > {exp}-frame annotation "
                                    f"(would be clipped; cannot be accepted)")
            if T is None:
                continue
            arm_rep["lengths_checked"] += 1
            if info["format"] == "npz":
                want = T if step == 1 else math.ceil(T / step)
                if is_failed and info["n"] == 0:
                    continue  # empty placeholder for a declared failure: scored as all "other"
                if info["n"] != want:
                    length_problem(s, f"{arm}: {s}: {info['n']} labels, expected {want} "
                                      f"({T} frames at stride {step})")
                if ns is not None and step > 1 and info["n"] != ns:
                    length_problem(s, f"{arm}: {s}: {info['n']} labels != manifest n_samples {ns}")
            elif info["max_end"] > T:
                length_problem(s, f"{arm}: {s}: bout ends at frame {info['max_end']} > {T} frames")
        report["arms"][arm] = arm_rep
    # one pipeline execution: every arm must describe the same frames and the same failures
    ref = next(iter(arms))
    for arm in arms:
        if frames_by_arm[arm] != frames_by_arm[ref] and (strict or (frames_by_arm[arm] and frames_by_arm[ref])):
            problems.append(f"{arm}: manifest `frames` differs from arm {ref}'s (not one pipeline execution?)")
        if failed_by_arm[arm] != failed_by_arm[ref]:
            problems.append(f"{arm}: failed_sequences {failed_by_arm[arm]} != {ref}'s {failed_by_arm[ref]}")
    report["failed_sequences"] = failed_by_arm[ref]
    report["n_problems"] = len(problems)
    return report, problems


def verify_execution(frozen_info: dict, cfg: dict, arms: dict, manifests: dict, primary: str,
                     targets: dict | None, strict: bool) -> tuple[dict, list[str]]:
    """Does the frozen config cover the execution that produced these predictions? (EVAL_PLAN A1, item 4)."""
    problems: list[str] = []
    rep: dict = {"strict": strict}
    if strict:
        if set(arms) != set(TEST_ARMS):
            problems.append(f"arms must be exactly {list(TEST_ARMS)}, got {sorted(arms)}")
        if primary != TEST_PRIMARY:
            problems.append(f"primary arm must be {TEST_PRIMARY!r}, got {primary!r}")
    if targets is not None and targets.get("primary_arm") != primary:
        problems.append(f"targets primary_arm {targets.get('primary_arm')!r} != --primary {primary!r}")
    frozen_at = cfg.get("frozen_at_commit")
    run_commits = set()
    for name in arms:
        man = manifests[name]
        c = man.get("config") if isinstance(man.get("config"), dict) else {}
        if man.get("arm") != name and (strict or "arm" in man):
            problems.append(f"{name}: manifest arm {man.get('arm')!r} != {name!r}")
        if c.get("frozen_sha256") != frozen_info["sha256"]:
            problems.append(f"{name}: manifest config.frozen_sha256 {str(c.get('frozen_sha256'))[:12]} != "
                            f"frozen config {frozen_info['sha256'][:12]}")
        if c.get("frozen_at_commit") != frozen_at:
            problems.append(f"{name}: manifest config.frozen_at_commit {c.get('frozen_at_commit')!r} != "
                            f"{frozen_at!r}")
        if not c.get("run_commit"):
            problems.append(f"{name}: manifest config.run_commit missing")
        else:
            run_commits.add(c["run_commit"])
        dirty = c.get("run_git_dirty_paths")
        if dirty is None:
            if strict:
                problems.append(f"{name}: manifest config.run_git_dirty_paths missing (the runner must record "
                                f"`git status --porcelain` paths at start)")
        elif not isinstance(dirty, list):
            problems.append(f"{name}: config.run_git_dirty_paths must be a list")
        else:
            bad = [p for p in dirty if str(p).startswith(NO_DIRTY_PREFIXES)]
            if bad:
                problems.append(f"{name}: runner ran with uncommitted changes in frozen paths: {bad}")
    if len(run_commits) > 1:
        problems.append(f"arms come from different run commits: {sorted(run_commits)}")
    rep["run_commit"] = run_commit = next(iter(run_commits)) if len(run_commits) == 1 else None
    rep["frozen_at_commit"] = frozen_at
    if not _commit_ok(frozen_at):
        problems.append(f"frozen_at_commit {frozen_at!r} is not a commit in this repository")
    if run_commit and not _commit_ok(run_commit):
        problems.append(f"run_commit {run_commit!r} is not a commit in this repository")
    if run_commit and _commit_ok(run_commit) and _commit_ok(frozen_at):
        rc, out = _git("diff", "--stat", frozen_at, run_commit, "--", *FROZEN_TREES)
        rep["product_diff_empty"] = rc == 0 and not out.strip()
        if not rep["product_diff_empty"]:
            problems.append(f"git diff {frozen_at[:10]} {run_commit[:10]} -- {' '.join(FROZEN_TREES)} is not "
                            f"empty:\n{out.decode(errors='replace').strip()}")
        want = (cfg.get("zero_shot_arm") or {}).get("tier1", {}).get("prompt_sha256")
        got = prompt_sha_at(run_commit)
        rep["tier1_prompt_sha256_at_run_commit"] = got
        if not want or got != want:
            problems.append(f"tier-1 prompt ({PROMPT_FILE} PROMPT) at run_commit hashes to {got and got[:12]}, "
                            f"frozen config says {want and want[:12]}")
        fc = blob_sha_at(run_commit, frozen_info["path"])
        if fc != frozen_info["sha256"]:
            problems.append(f"frozen config {frozen_info['path']} at run_commit hashes to {fc and fc[:12]}, "
                            f"working tree {frozen_info['sha256'][:12]}")
        for path, sha in frozen_info["files_checked"].items():
            at = blob_sha_at(run_commit, path)
            if at != sha:
                problems.append(f"frozen asset {path} at run_commit hashes to {at and at[:12]}, "
                                f"working tree {sha and sha[:12]}")
        runner = blob_sha_at(run_commit, "scripts/test_run.py")
        rep["runner_sha256_at_run_commit"] = runner  # recorded for audit; the runner is not frozen
    rep["n_problems"] = len(problems)
    return rep, problems


def speed_from_manifest(manifest: dict, seqs: list[str], arm: str) -> dict:
    """Summed per-video processing time vs video duration (EVAL_PLAN 4.4, amendment A1). Label-free.

    Per video: track_s (chunking + SAM + decode + features + identity) + rules_s, + tier1_s for a tier-1 arm,
    from manifest `wall_clock_s.per_video.<seq>`. Duration = src_frames / 30 from manifest `frames`, so failed
    videos stay in the denominator. Missing data gives computed: false (never a partial number)."""
    stages = ("track_s", "rules_s") + (("tier1_s",) if arm.endswith("_t1") else ())
    wc = manifest.get("wall_clock_s") if isinstance(manifest.get("wall_clock_s"), dict) else {}
    pv = wc.get("per_video")
    frames = manifest.get("frames") if isinstance(manifest.get("frames"), dict) else {}
    out = {"arm": arm, "stages_summed": list(stages),
           "definition": "per video: sum of the wall-clock of each stage run on that video; ratio = that sum / "
                         "video duration (src_frames / 30). All videos in the denominator, failures included. "
                         "Summed stage time, not one measured file-to-output interval; download excluded."}
    if not isinstance(pv, dict):
        return {**out, "computed": False, "reason": "manifest wall_clock_s.per_video missing"}
    rows, missing = {}, []
    for s in seqs:
        v = pv.get(s) if isinstance(pv.get(s), dict) else None
        src = (frames.get(s) or {}).get("src_frames") if isinstance(frames.get(s), dict) else None
        if v is None or not isinstance(src, int) or src <= 0 or any(
                not isinstance(v.get(k), (int, float)) or isinstance(v.get(k), bool) for k in stages):
            missing.append(s)
            continue
        proc = float(sum(v[k] for k in stages))
        rows[s] = {"processing_s": proc, "video_s": src / NATIVE_FPS, "x_realtime": proc / (src / NATIVE_FPS),
                   **{k: float(v[k]) for k in stages}}
        for k in ("summed_s", "total_s"):  # the runner's own sum, if it reports one, must agree
            if isinstance(v.get(k), (int, float)) and abs(v[k] - proc) > 0.01:
                rows[s][f"manifest_{k}_differs"] = float(v[k])
    if missing:
        return {**out, "computed": False, "reason": f"per-video stage timings or src_frames missing for {missing}"}
    ratios = np.array([r["x_realtime"] for r in rows.values()])
    tot_p, tot_v = sum(r["processing_s"] for r in rows.values()), sum(r["video_s"] for r in rows.values())
    return {**out, "computed": True, "n_videos": len(rows), "total_processing_s": tot_p, "total_video_s": tot_v,
            "pooled_x_realtime": tot_p / tot_v, "median_x_realtime": float(np.median(ratios)),
            "max_x_realtime": float(ratios.max()), "concurrency": wc.get("concurrency"), "per_video": rows}


def per_seq_counts(metrics: dict, seqs: list[str]) -> np.ndarray:
    """[S, 3 behaviors, (tp, fp, fn)] back from score_split's per_sequence blocks."""
    ps = metrics["per_sequence"]
    return np.array([[[ps[s][b]["tp"], ps[s][b]["fp"], ps[s][b]["fn"]] for b in BEHAVIORS] for s in seqs],
                    dtype=np.int64)


def paired_diff(base: np.ndarray, arm: np.ndarray, reps: int = 10_000, seed: int = common.SEED) -> dict:
    """arm minus base, per behavior F1 and macro F1, pooled; CI from the SAME sequence resamples for both."""
    def f1(c):
        return score._vec_prf(c)[2]

    point_b, point_a = f1(base.sum(axis=0)), f1(arm.sum(axis=0))
    w = common.bootstrap_weights(base.shape[0], reps, seed)
    fb = f1(np.einsum("rs,sbk->rbk", w, base))
    fa = f1(np.einsum("rs,sbk->rbk", w, arm))
    out = {"statistic": "arm minus base, pooled frame F1", "reps": reps, "seed": seed, "unit": "sequence",
           "behaviors": {}}
    for i, b in enumerate(BEHAVIORS):
        d = fa[:, i] - fb[:, i]
        ok = ~np.isnan(d)
        ci = [float(np.percentile(d[ok], 2.5)), float(np.percentile(d[ok], 97.5))] if ok.any() else None
        out["behaviors"][b] = {
            "diff": float(point_a[i] - point_b[i]), "ci95": ci, "ci_reading": ci_reading(ci),
            "frac_resamples_positive": float((d[ok] > 0).mean()) if ok.any() else None,
            "undefined_frac": float(1 - ok.mean())}
    d = fa.mean(axis=1) - fb.mean(axis=1)
    ok = ~np.isnan(d)
    ci = [float(np.percentile(d[ok], 2.5)), float(np.percentile(d[ok], 97.5))] if ok.any() else None
    out["macro_f1"] = {
        "diff": float(np.mean(point_a) - np.mean(point_b)), "ci95": ci, "ci_reading": ci_reading(ci),
        "frac_resamples_positive": float((d[ok] > 0).mean()) if ok.any() else None,
        "undefined_frac": float(1 - ok.mean())}
    return out


def ci_reading(ci: list | None) -> str:
    """Wording rule (EVAL_PLAN 5 and 7.3, amendment A1): a CI containing 0 is inconclusive, not 'no change'."""
    if ci is None:
        return "undefined"
    if ci[0] > 0:
        return "above zero"
    if ci[1] < 0:
        return "below zero"
    return "inconclusive (CI contains 0)"


def duration_agreement(metrics: dict, seqs: list[str]) -> dict:
    """Per behavior, total scored duration per video (s) vs ground truth: MAE, mean signed error, Pearson r."""
    out = {}
    for b in BEHAVIORS:
        g = np.array([metrics["per_sequence"][s][b]["gt_frames"] for s in seqs], float) / NATIVE_FPS
        p = np.array([metrics["per_sequence"][s][b]["pred_frames"] for s in seqs], float) / NATIVE_FPS
        r = float(np.corrcoef(g, p)[0, 1]) if len(seqs) > 1 and g.std() > 0 and p.std() > 0 else None
        out[b] = {"n_videos": len(seqs), "mae_s": float(np.mean(np.abs(p - g))),
                  "mean_signed_error_s": float(np.mean(p - g)), "pearson_r": r,
                  "gt_total_s": float(g.sum()), "pred_total_s": float(p.sum())}
    return out


def _verdict(point: float | None, ci: list | None, thr: float, direction: str = "higher") -> str:
    """MET: point reaches the threshold. MET ROBUSTLY: the CI bound on the far side reaches it too, i.e. the
    lower bound for higher-is-better and the UPPER bound for lower-is-better (amendment A1). Thresholds are
    inclusive (>= / <=), full precision, no rounding."""
    if point is None:
        return "NOT COMPUTED"
    if direction == "higher":
        if point < thr:
            return "NOT MET"
        return "MET ROBUSTLY" if ci is not None and ci[0] >= thr else "MET"
    if direction == "lower":
        if point > thr:
            return "NOT MET"
        return "MET ROBUSTLY" if ci is not None and ci[1] <= thr else "MET"
    raise ValueError(f"direction must be higher or lower, got {direction!r}")


def _level(components: dict) -> dict:
    """A target level is reached only if EVERY component reaches it (amendment A1)."""
    vs = [c["verdict"] for c in components.values()]
    if all(v == "MET ROBUSTLY" for v in vs):
        overall = "MET ROBUSTLY"
    elif all(v in ("MET", "MET ROBUSTLY") for v in vs):
        overall = "MET"
    else:
        overall = "NOT MET"
    return {"verdict": overall, "not_met": [k for k, c in components.items() if c["verdict"] not in
                                            ("MET", "MET ROBUSTLY")]}


def check_targets(targets: dict, arms_metrics: dict, diffs: dict, speed: dict | None = None) -> dict:
    out = {"rule": targets.get("rule"), "level_rule": targets.get("level_rule"), "f1": {}, "f1_level": {},
           "tier1_lift_macro": None, "speed_x_realtime": None}
    m = arms_metrics.get(targets["primary_arm"])
    if m is None:
        out["error"] = f"primary arm {targets['primary_arm']!r} not scored"
        return out
    for level, thr in targets["f1"].items():
        res = {}
        for k, t in thr.items():
            if k == "macro":
                pt, ci = m["macro_f1"], m["macro_f1_ci95"]
            else:
                pt, ci = m["behaviors"][k]["f1"], m["behaviors"][k]["ci95"]["f1"]
            res[k] = {"threshold": t, "direction": "higher", "point": pt, "ci95": ci,
                      "verdict": _verdict(pt, ci, t, "higher")}
        out["f1"][level] = res
        out["f1_level"][level] = _level(res)
    lift = targets.get("tier1_lift_macro")
    if lift:
        key = f"{lift['baseline_arm']}:{lift['arm']}"
        d = diffs.get(key)
        if d is None:
            out["tier1_lift_macro"] = {"error": f"comparison {key} not requested"}
        else:
            md = d["macro_f1"]
            out["tier1_lift_macro"] = {
                level: {"threshold": lift[level], "direction": "higher", "point": md["diff"], "ci95": md["ci95"],
                        "verdict": _verdict(md["diff"], md["ci95"], lift[level], "higher")}
                for level in ("minimum_credible", "good")}
    sp = targets.get("speed_x_realtime")
    if sp:
        if not speed or not speed.get("computed"):
            out["speed_x_realtime"] = {"verdict": "NOT COMPUTED",
                                       "reason": (speed or {}).get("reason", "no speed data")}
        else:
            res = {}
            for level in ("minimum_credible", "good"):
                comp = {k: {"threshold": t, "direction": "lower", "point": speed[f"{k}_x_realtime"], "ci95": None,
                            "verdict": _verdict(speed[f"{k}_x_realtime"], None, t, "lower")}
                        for k, t in sp[level].items()}
                res[level] = {"components": comp, **_level(comp),
                              "note": "no CI for speed: MET ROBUSTLY is not applicable"}
            out["speed_x_realtime"] = res
    return out


def _refuse(base: dict, problems: list[str], log_path: Path, what: str) -> None:
    """Log a label-free preflight refusal (not a scoring execution, so no test run is used) and exit."""
    common.append_run({**base, "kind": "preflight", "time_utc": common.now_utc(), "status": "refused",
                       "labels_loaded": False, "problems": problems}, log_path)
    raise SystemExit(f"{what}: {len(problems)} problem(s), refusing before any label is read "
                     f"(logged as preflight {base['run_id']}):\n  " + "\n  ".join(problems))


def run_arms(split: str, arms: dict, primary: str, purpose: str | None = None,
             compares: list[tuple[str, str]] | None = None, frozen_config: Path | None = None,
             targets_path: Path | None = None, priority=score.DEFAULT_PRIORITY,
             labels_dir: Path = common.LABELS_DIR, split_file: Path = common.SPLIT_FILE,
             log_path: Path = common.RUNS_LOG, out_dir: Path | None = None, reps: int = 10_000,
             confirm_test_run: bool = False, notes: str = "", seal: bool | None = None,
             strict: bool | None = None, accept_length_mismatch: dict | None = None,
             preflight_only: bool = False) -> dict:
    split = common.canonical_split(split)
    purpose = purpose or {"dev": "tune", "dev_holdout": "holdout-check"}.get(split)
    if not arms or primary not in arms:
        raise SystemExit(f"--primary {primary!r} must be one of the arms {sorted(arms)}")
    compares = list(compares if compares is not None else [(primary, a) for a in arms if a != primary])
    for a, b in compares:
        if a not in arms or b not in arms:
            raise SystemExit(f"--compare {a}:{b}: unknown arm")
    is_test = split == "test"
    strict = is_test or bool(strict)
    seal = is_test if seal is None else (seal or is_test)
    if is_test and frozen_config is None:
        raise SystemExit("test split: --frozen-config is required")
    if not preflight_only:  # a preflight-only execution never opens labels, so it is not a test run
        common.guard_test_run(split, confirm_test_run, purpose or "", "score", log_path)
    targets = json.loads(Path(targets_path).read_text()) if targets_path else None
    seqs, _ = common.load_split(split, split_file)
    problems: list[str] = []
    frozen, cfg = verify_frozen_config(frozen_config) if frozen_config else (None, None)
    if frozen:
        problems += frozen["mismatches"]
    if strict:
        missing_cmp = [c for c in TEST_COMPARES if c not in compares]
        if missing_cmp:
            problems.append(f"required paired comparisons missing: {[f'{a}:{b}' for a, b in missing_cmp]}")

    arm_info, files_by_arm, manifests = {}, {}, {}
    for name, d in arms.items():
        d = Path(d)
        manifests[name] = score.read_manifest(d)
        files = {s: score.find_pred_file(d, s) for s in seqs}
        files_by_arm[name] = files
        h, per_file = score._hash_predictions(d, files)
        payload = {s: v for s, v in per_file.items() if s != "manifest.json"}
        arm_info[name] = {"pred_dir": str(d), "predictions_sha256": h, "predictions_files_sha256": per_file,
                          "payload_sha256": common.sha256_json(payload),
                          "manifest_sha256": per_file.get("manifest.json"),
                          "missing_predictions": [s for s, p in files.items() if p is None],
                          "manifest": manifests[name],
                          "config_sha256": common.sha256_json(manifests[name].get("config", {}))}
    expected, expected_info = expected_frames(split, seqs, labels_dir)
    validation, vprobs = validate_predictions(seqs, arms, manifests, files_by_arm, expected, strict,
                                              accept_length_mismatch)
    validation["expected_frames_source"] = expected_info
    problems += vprobs
    execution = None
    if frozen:
        execution, eprobs = verify_execution(frozen, cfg, arms, manifests, primary, targets, strict)
        problems += eprobs
    speed = speed_from_manifest(manifests[primary], seqs, primary)
    t0 = time.time()
    run_id = common.new_run_id(f"score-{split}")
    base = {
        "run_id": run_id, "kind": "score", "split": split, "purpose": purpose, "multi_arm": True,
        **common.git_info(),
        "split_file": str(split_file), "split_file_sha256": common.sha256_file(Path(split_file)),
        "sequences": seqs, "partial": False, "primary_arm": primary, "compares": [f"{a}:{b}" for a, b in compares],
        "predictions_sha256": common.sha256_json({n: a["predictions_sha256"] for n, a in arm_info.items()}),
        "payload_sha256": common.sha256_json({n: a["payload_sha256"] for n, a in arm_info.items()}),
        "arms": arm_info, "frozen_config": frozen,
        "targets": {"path": str(targets_path), "sha256": common.sha256_file(Path(targets_path))}
        if targets_path else None,
        "scorer": {"priority": list(priority), "reps": reps, "seed": common.SEED,
                   "bout_iou_thresholds": list(score.BOUT_IOU_THRESHOLDS), "allow_missing": False,
                   "per_sequence_sealed": seal, "strict": strict},
        "preflight": {"validation": validation, "execution": execution},
        "speed": speed,
        "notes": notes,
    }
    if problems:
        _refuse(base, problems, log_path, "preflight")
    if preflight_only:
        rec = {**base, "kind": "preflight", "time_utc": common.now_utc(), "status": "ok", "labels_loaded": False}
        common.append_run(rec, log_path)
        return rec
    common.append_run({**base, "time_utc": common.now_utc(), "status": "started"}, log_path)  # before labels
    warnings: list[str] = []
    labels_loaded = False
    try:
        gt_by_seq = {}
        for s in seqs:
            labels_loaded = True
            gt_by_seq[s] = common.load_sequence(split, s, labels_dir)["labels"]
        arms_metrics, counts_by_arm, sample_fps = {}, {}, {}
        for name in arms:
            pred_by_seq, fps = {}, set()
            for s in seqs:
                w: list[str] = []
                excl, ovr, meta = score.load_prediction(files_by_arm[name][s], len(gt_by_seq[s]), manifests[name],
                                                        priority, w)
                warnings += [f"{name}: {x}" for x in w]
                pred_by_seq[s] = (excl, ovr)
                fps.add(str(meta.get("sample_fps")))
            m = score.score_split(gt_by_seq, pred_by_seq, reps=reps)
            m["duration_agreement"] = duration_agreement(m, seqs)
            counts_by_arm[name] = per_seq_counts(m, seqs)
            arms_metrics[name], sample_fps[name] = m, sorted(fps)
        diffs = {f"{a}:{b}": paired_diff(counts_by_arm[a], counts_by_arm[b], reps) for a, b in compares}
        target_check = check_targets(targets, arms_metrics, diffs, speed) if targets else None
        sealed_info = None
        if seal:
            sealed = {n: {"per_sequence": m.pop("per_sequence")} for n, m in arms_metrics.items()}
            if out_dir is None:
                raise ValueError("sealing per-sequence metrics needs out_dir")
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            sp = Path(out_dir) / f"{run_id}{SEALED_SUFFIX}"
            sp.write_text(json.dumps({"run_id": run_id, "arms": sealed}, indent=1, default=str))
            sealed_info = {"path": str(sp), "sha256": common.sha256_file(sp)}
        rec = {**base, "time_utc": common.now_utc(), "status": "ok", "labels_loaded": True,
               "metrics": {"arms": arms_metrics, "paired_differences": diffs,
                           "random_baseline": score.random_baseline(gt_by_seq), "bootstrap_scope": BOOTSTRAP_SCOPE},
               "targets_check": target_check, "per_sequence_sealed": sealed_info, "sample_fps": sample_fps,
               "warnings": warnings, "scorer_wall_clock_s": round(time.time() - t0, 3)}
    except Exception as e:  # logged, then re-raised
        rec = {**base, "time_utc": common.now_utc(), "status": "failed", "error": repr(e),
               "labels_loaded": labels_loaded, "traceback": traceback.format_exc(limit=5), "warnings": warnings}
        common.append_run(rec, log_path)
        raise
    common.append_run(rec, log_path)
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"{run_id}.json").write_text(json.dumps(rec, indent=1, default=str))
    return rec


def format_arms_report(rec: dict) -> str:
    lines = [f"run {rec['run_id']} split={rec['split']} purpose={rec['purpose']} primary={rec['primary_arm']}"]
    for name, m in rec["metrics"]["arms"].items():
        mm = copy.copy(m)
        mm.setdefault("per_sequence", {})  # sealed on test: nothing printed per sequence
        lines += ["", f"=== arm {name} ({rec['arms'][name]['pred_dir']})", score.format_report(mm, "")]
    lines += ["", "paired differences (b minus a; CI from the same sequence resamples):"]
    for key, d in rec["metrics"]["paired_differences"].items():
        md = d["macro_f1"]
        ci = md["ci95"]
        per = ", ".join(f"{b} {d['behaviors'][b]['diff']:+.4f}" for b in BEHAVIORS)
        lines.append(f"  {key}: macro {md['diff']:+.4f} "
                     f"[{ci[0]:+.4f}, {ci[1]:+.4f}] {md['ci_reading']} ({per})" if ci
                     else f"  {key}: macro {md['diff']:+.4f}")
    tc = rec.get("targets_check")
    if tc:
        lines += ["", f"targets ({tc.get('rule')}):"]
        for level, res in tc.get("f1", {}).items():
            lv = (tc.get("f1_level") or {}).get(level, {})
            lines.append(f"  {level}: level {lv.get('verdict')} (needs every component); " + "; ".join(
                f"{k} {v['point']:.4f} vs {v['threshold']} {v['verdict']}" for k, v in res.items()))
        lift = tc.get("tier1_lift_macro") or {}
        for level, v in lift.items():
            if isinstance(v, dict):
                lines.append(f"  tier-1 lift {level}: {v['point']:+.4f} vs +{v['threshold']} {v['verdict']}")
        sp = tc.get("speed_x_realtime")
        if sp and sp.get("verdict") == "NOT COMPUTED":
            lines.append(f"  speed: NOT COMPUTED ({sp.get('reason')})")
        elif sp:
            for level, v in sp.items():
                lines.append(f"  speed {level}: {v['verdict']}; " + "; ".join(
                    f"{k} {c['point']:.4f}x vs <= {c['threshold']} {c['verdict']}" for k, c in v["components"].items()))
    if rec.get("per_sequence_sealed"):
        lines += ["", f"per-sequence metrics sealed in {rec['per_sequence_sealed']['path']} "
                      "(do not open before the test-2 decision is committed; EVAL_PLAN section 7)"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, help="dev | dev-holdout | test")
    ap.add_argument("--arm", action="append", required=True, help="name=pred_dir (repeat)")
    ap.add_argument("--primary", required=True)
    ap.add_argument("--compare", action="append", help="a:b, reports b minus a (repeat); default primary:each")
    ap.add_argument("--purpose")
    ap.add_argument("--frozen-config", type=Path)
    ap.add_argument("--targets", type=Path)
    ap.add_argument("--priority", default=",".join(score.DEFAULT_PRIORITY))
    ap.add_argument("--labels-dir", type=Path, default=common.LABELS_DIR)
    ap.add_argument("--split-file", type=Path, default=common.SPLIT_FILE)
    ap.add_argument("--log", type=Path, default=common.RUNS_LOG)
    ap.add_argument("--out-dir", type=Path, default=Path("eval/results"))
    ap.add_argument("--reps", type=int, default=10_000)
    ap.add_argument("--seal", action="store_true", help="seal per-sequence metrics (always on for test)")
    ap.add_argument("--confirm-test-run", action="store_true")
    ap.add_argument("--strict", action="store_true", help="test-split pre-label checks on any split")
    ap.add_argument("--accept-length-mismatch", action="append", default=[], metavar="SEQ=REASON",
                    help="accept a named length mismatch for SEQ (logged with the reason)")
    ap.add_argument("--preflight-only", action="store_true",
                    help="run the label-free checks, log a preflight record, open no label file")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    accept = {}
    for spec in a.accept_length_mismatch:
        seq, sep, reason = spec.partition("=")
        if not sep or not seq or not reason.strip():
            raise SystemExit(f"--accept-length-mismatch must be SEQ=REASON, got {spec!r}")
        accept[seq] = reason.strip()
    arms = dict(parse_arm(s) for s in a.arm)
    if len(arms) != len(a.arm):
        raise SystemExit("duplicate arm name")
    compares = [tuple(c.split(":", 1)) for c in a.compare] if a.compare else None
    rec = run_arms(a.split, arms, a.primary, a.purpose, compares, a.frozen_config, a.targets,
                   tuple(a.priority.split(",")), a.labels_dir, a.split_file, a.log, a.out_dir, a.reps,
                   a.confirm_test_run, a.notes, a.seal or None, a.strict or None, accept, a.preflight_only)
    if a.preflight_only:
        v = rec["preflight"]["validation"]
        print(f"preflight ok ({rec['run_id']}): {len(rec['sequences'])} sequences x {len(arms)} arms; "
              f"failed_sequences {v['failed_sequences']}; accepted {v['accepted_length_mismatches']}; "
              f"speed computed: {rec['speed'].get('computed')} ({rec['speed'].get('reason', 'ok')})")
        print(f"logged to {a.log}; no label file was opened")
        return 0
    print(format_arms_report(rec))
    for w in rec["warnings"]:
        print("warning:", w, file=sys.stderr)
    print(f"\nlogged to {a.log}; full result {a.out_dir}/{rec['run_id']}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
