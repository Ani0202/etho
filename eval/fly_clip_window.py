"""Fly-beat demo window selection (docs/DECISIONS.md, "Fly-beat window rule", commit 095af8d, 2026-10-30 20:29 UTC).

Applies the committed rule exactly: the earliest 15 s window in Fly vs. Fly movies 2-5 with the most
expert-labeled lunge or wing-threat bouts; ties go to the earlier movie, then the earlier start.

Fixed terms (from the rule):
- Search space: the span SAM 3.1 tracked for the frozen run in each movie, source frames [22500, 31500) at 30 fps
  (confirmed against data/runs/F1/movie{2..5}/tracking.json "window").
- Windows: 15 s (450 frames), starting on whole seconds of the span (offsets 0..285 s).
- Score: the number of expert-labeled lunge or wing-threat bouts whose onset falls inside the window, both flies
  counted.

Bout definition (the scorer's, not a new one): a bout is a maximal run of True in the loader's per-fly mask
(`flies_extract.masks_from_actions` -> `flies_extract.runs`), one behavior and one fly at a time, so a lunge by
fly 0, a lunge by fly 1 and a wing threat by fly 0 are three bouts. Runs are taken on the full-movie mask, so a bout
that began before the span keeps its true (out-of-span) onset and is not counted. The scorer
(`flies_score._kept_runs`) applies no minimum length; it only drops a bout with no kept frame (every frame
excluded because either fly is `unknown`). The same drop is applied here.

Expert labels only: this module never opens an Etho prediction file. Train movies only (2-5); the loaders' test
seal is left in place. $0, no network, no model calls.

    uv run python -m eval.fly_clip_window
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from eval import common
from eval import flies_extract as fx

RULE = {"commit": "095af8d", "file": "docs/DECISIONS.md", "entry": "Fly-beat window rule",
        "committed_utc": "2026-10-30T20:29:26Z",
        "text": "the earliest 15 s window in Fly vs. Fly movies 2-5 with the most expert-labeled lunge or "
                "wing-threat bouts; ties go to the earlier movie"}
MOVIES = (2, 3, 4, 5)
SPAN = fx.WINDOWS["mid5"]  # (22500, 31500)
WIN_S = 15
BEHAVIORS = ("lunge", "wing_threat")
TRACKING = "data/runs/F1/movie{m}/tracking.json"
OUT = Path("eval/results/fly_clip_window.json")


def tracked_span(movie: int, path_fmt: str = TRACKING) -> tuple[int, int]:
    w = json.loads(Path(path_fmt.format(m=movie)).read_text())["window"]
    if w.get("stride", 1) != 1:
        raise ValueError(f"movie{movie}: tracking stride {w.get('stride')} != 1")
    return int(w["start"]), int(w["stop"])


def bout_onsets(actions: dict, span: tuple[int, int] = SPAN, behaviors=BEHAVIORS,
                dropped: list | None = None) -> list[dict]:
    """Every per-fly bout (maximal run, full movie) of the given behaviors whose onset is inside `span`.
    Drops bouts with no kept frame (either fly `unknown`), as the scorer does. Onsets are source frames."""
    m = fx.masks_from_actions(actions, fx.N_FRAMES)
    unk = m[fx.UNKNOWN].any(axis=0) if fx.UNKNOWN in m else np.zeros(fx.N_FRAMES, bool)
    keep = ~unk
    out = []
    for b in behaviors:
        for f in range(fx.N_FLIES):
            for s, e in fx.runs(m[b][f]):
                if not span[0] <= s < span[1]:
                    continue
                if not keep[s:e].any():
                    if dropped is not None:
                        dropped.append({"behavior": b, "fly": f, "start": int(s), "end": int(e)})
                    continue
                out.append({"behavior": b, "fly": f, "start": int(s), "end": int(e)})
    return out


def window_scores(onsets: list[int], span: tuple[int, int] = SPAN, win_s: int = WIN_S,
                  fps: int = fx.NATIVE_FPS) -> np.ndarray:
    """Score of each window starting at offset k s (k = 0 .. span_s - win_s): onsets in [s0, s0 + win)."""
    n_start = (span[1] - span[0]) // fps - win_s + 1
    o = np.asarray(onsets, np.int64)
    return np.array([int(((o >= span[0] + k * fps) & (o < span[0] + k * fps + win_s * fps)).sum())
                     for k in range(n_start)], np.int64)


def rank(scores_by_movie: dict[int, np.ndarray]) -> list[tuple[int, int, int]]:
    """(movie, start_s, score) sorted by score desc, then earlier movie, then earlier start."""
    rows = [(m, k, int(s)) for m, sc in scores_by_movie.items() for k, s in enumerate(sc)]
    return sorted(rows, key=lambda r: (-r[2], r[0], r[1]))


def _window_record(m: int, k: int, score: int, span=SPAN, win_s=WIN_S, fps=fx.NATIVE_FPS) -> dict:
    s = span[0] + k * fps
    e = s + win_s * fps
    return {"movie": m, "score": score,
            "span_offset_s": [k, k + win_s],
            "span_offset_frames_half_open": [k * fps, k * fps + win_s * fps],
            "source_frames_half_open": [s, e], "source_frames_inclusive": [s, e - 1],
            "source_time_s": [s / fps, e / fps]}


def select(root: Path = fx.DATA_ROOT, tracking_fmt: str = TRACKING) -> dict:
    spans = {m: tracked_span(m, tracking_fmt) for m in MOVIES}
    bad = {m: s for m, s in spans.items() if s != SPAN}
    if bad:
        raise ValueError(f"tracked span differs from the rule's {SPAN}: {bad}")
    bouts, scores, unknown_frames, straddle, dropped, unk_mask = {}, {}, {}, {}, {}, {}
    for m in MOVIES:
        acts = fx.load_actions(m, root)  # train movie; the test seal still applies
        dropped[m] = []
        bouts[m] = bout_onsets(acts, dropped=dropped[m])
        scores[m] = window_scores([b["start"] for b in bouts[m]])
        masks = fx.masks_from_actions(acts)
        unk_mask[m] = masks[fx.UNKNOWN].any(axis=0)
        unknown_frames[m] = int(unk_mask[m][SPAN[0]:SPAN[1]].sum())
        # bouts already running at the span start (onset before 22500): not counted, listed for transparency
        straddle[m] = [{"behavior": b, "fly": f, "start": int(s), "end": int(e)}
                       for b in BEHAVIORS for f in range(fx.N_FLIES) for s, e in fx.runs(masks[b][f])
                       if s < SPAN[0] < e]
    ranked = rank(scores)
    best = ranked[0]
    tied = [r for r in ranked if r[2] == best[2]]
    chosen = _window_record(*best)
    in_win = [b for b in bouts[best[0]]
              if chosen["source_frames_half_open"][0] <= b["start"] < chosen["source_frames_half_open"][1]]
    chosen["bouts"] = [{**b, "onset_span_offset_s": (b["start"] - SPAN[0]) / fx.NATIVE_FPS} for b in in_win]
    cs, ce = chosen["source_frames_half_open"]
    chosen["unknown_frames_in_window"] = {
        "n_frames": int(unk_mask[best[0]][cs:ce].sum()),
        "runs_source_frames_half_open": [[int(a) + cs, int(b) + cs] for a, b in fx.runs(unk_mask[best[0]][cs:ce])],
        "note": "frames where either fly is 'unknown'; the scorer excludes them from counts. No bout in this window "
                "lost its onset to them (bouts are dropped only when every frame is unknown)."}
    chosen["by_behavior"] = {b: sum(1 for x in in_win if x["behavior"] == b) for b in BEHAVIORS}
    return {
        "rule": RULE,
        "terms": {"movies": list(MOVIES), "search_space_source_frames_half_open": list(SPAN),
                  "fps": fx.NATIVE_FPS, "window_s": WIN_S, "window_starts_s": [0, len(scores[MOVIES[0]]) - 1],
                  "n_windows_per_movie": int(len(scores[MOVIES[0]])),
                  "score": "number of expert-labeled lunge or wing_threat bouts whose onset is inside the window "
                           "[start, start + 450) source frames, both flies counted",
                  "ties": "earlier movie, then earlier start"},
        "tracked_span_check": {f"movie{m}": {"tracking_json": tracking_fmt.format(m=m), "window": list(s),
                                             "matches_rule": s == SPAN} for m, s in spans.items()},
        "chosen": chosen,
        "top5": [_window_record(*r) for r in ranked[:5]],
        "tie_break": {"n_windows_at_max_score": len(tied),
                      "used": len(tied) > 1,
                      "rule_applied": "earlier movie, then earlier start" if len(tied) > 1 else None,
                      "tied_windows": [{"movie": r[0], "start_s": r[1]} for r in tied]},
        "per_movie": {f"movie{m}": {"bouts_with_onset_in_span": len(bouts[m]),
                                    "by_behavior": {b: sum(1 for x in bouts[m] if x["behavior"] == b)
                                                    for b in BEHAVIORS},
                                    "max_window_score": int(scores[m].max()),
                                    "first_start_s_at_max": int(np.argmax(scores[m])),
                                    "unknown_frames_in_span": unknown_frames[m],
                                    "bouts_dropped_all_frames_unknown": dropped[m],
                                    "bouts_running_at_span_start_not_counted": straddle[m]} for m in MOVIES},
        "bout_definition": (
            "A bout is a maximal run of True in the loader's per-fly mask (eval/flies_extract.py masks_from_actions "
            "then runs), per behavior and per fly: actions.mat [start, end] (MATLAB 1-based inclusive) becomes "
            "python [start-1, end); overlapping or touching raw rows of the same fly and behavior merge into one "
            "run. Lunge and wing_threat are counted separately, fly 0 and fly 1 separately (both flies counted). "
            "Runs are taken over the full movie, so a bout already running at frame 22500 keeps its true onset "
            "and is not counted. Minimum length: none; the scorer (eval/flies_score.py _kept_runs) uses none and "
            "only drops a bout whose every frame is excluded (either fly 'unknown'); that drop is applied here. "
            "The onset is the run's first python frame."),
        "provenance": {"labels": "expert annotations only (movieN_actions.mat via eval/flies_extract.load_actions)",
                       "predictions_opened": False, "test_movies_opened": False, "model_calls": 0, "cost_usd": 0.0,
                       "labels_previously_used": "train movies 1-5, already used under ADR-004"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    rec = select()
    rec["generated_utc"] = common.now_utc()
    rec.update({k: v for k, v in common.git_info().items() if k != "git_dirty_paths"})
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rec, indent=1) + "\n")
    c = rec["chosen"]
    common.append_run({"run_id": common.new_run_id("fly-clip-window"), "kind": "fly-clip-window",
                       "dataset": "flyvsfly", "split": "flies-train", "purpose": "demo-window-selection",
                       "status": "ok", "time_utc": common.now_utc(), **common.git_info(), "cost": {"usd": 0.0},
                       "labels_loaded": True, "predictions_loaded": False, "movies": list(MOVIES),
                       "rule_commit": RULE["commit"], "output": str(a.out),
                       "output_sha256": common.sha256_file(a.out),
                       "chosen": {"movie": c["movie"], "span_offset_s": c["span_offset_s"],
                                  "source_frames_half_open": c["source_frames_half_open"], "score": c["score"]},
                       "tie_break_used": rec["tie_break"]["used"]})
    print(json.dumps({"chosen": {k: c[k] for k in ("movie", "score", "span_offset_s", "source_frames_half_open")},
                      "top5": [(r["movie"], r["span_offset_s"], r["score"]) for r in rec["top5"]],
                      "tie_break": rec["tie_break"]["n_windows_at_max_score"]}, indent=1))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
