"""What the progress and results pages need on top of a raw job snapshot: five phases, friendly errors, the
planner card's data, and the per-bout trace. Pure functions of data already on disk; no model call, no spend."""

from __future__ import annotations

import json

from etho.adjudication.tier1 import CONFIDENT

from . import behaviors as B

# The five phases the page shows. Each is a run of the job's stages (a revision has only revise, predict, ...).
PHASES = (
    ("plan", "Plan", ("validate", "plan"),
     "Muse Spark proposes SAM phrases; SAM 3.1 checks each one on sample frames and a 5 s probe"),
    ("track", "Tracking", ("track",), "SAM 3.1 tracks both animals through the video"),
    ("rules", "Rules", ("synthesize", "revise", "predict"),
     "Muse Spark writes a Python detector per behavior; the detectors run in a sandbox"),
    ("strips", "Frame-strip check", ("tier1",),
     "Muse Spark Open judges borderline bouts from tiled frame strips"),
    ("report", "Report", ("report", "overlay"), "ethogram, metrics, methods paragraph and the overlay video"),
)
STILL_OPEN = "The precomputed showcase runs stay open."


def phases(snap: dict) -> list[dict]:
    """The snapshot's stages grouped into phases with a status, a wall-clock time and a status message."""
    stages = {s["name"]: s for s in snap["stages"]}
    job_elapsed = snap.get("elapsed_s") or 0.0
    out = []
    for key, label, names, blurb in PHASES:
        mine = [stages[n] for n in names if n in stages]
        if not mine:
            continue
        sts = [s["status"] for s in mine]
        if "failed" in sts:
            status = "failed"
        elif "running" in sts:
            status = "running"
        elif all(x in ("done", "skipped") for x in sts):
            status = "done"
        elif any(x in ("done", "skipped") for x in sts) and snap["status"] == "running":
            status = "running"                 # between two stages of the same phase
        else:
            status = "pending"
        started = [s for s in mine if s.get("start_s") is not None]
        if started:                            # a window, not a sum: the stages of a group may overlap
            t0 = min(s["start_s"] for s in started)
            ends = [s["end_s"] if s.get("end_s") is not None else job_elapsed for s in started]
            elapsed = max(0.0, max(ends) - t0)
        else:
            elapsed = sum(s["elapsed_s"] for s in mine)
        with_msg = [s for s in mine if s["message"]]
        running_msg = [s for s in with_msg if s["status"] == "running"]
        msg = (running_msg or with_msg or [{"message": ""}])[-1]["message"]
        if status == "pending" or not msg:
            msg = blurb if status == "pending" else (msg or "working")
        out.append({"key": key, "label": label, "status": status, "elapsed_s": round(elapsed, 1), "message": msg,
                    "blurb": blurb, "cost_usd": round(sum(s["cost_usd"] for s in mine), 4)})
    return out


def friendly_failure(snap: dict, friendly: dict[str, str]) -> str | None:
    """A plain sentence for a job that did not finish; never the raw exception text."""
    if snap["status"] not in ("failed", "cancelled"):
        return None
    text = f"{snap.get('error') or ''} {snap.get('message') or ''}".lower()
    if "switched off" in text or "kill" in text:
        return friendly["killed"]
    if "total cap" in text:
        return friendly["total_cap"]
    if "cap" in text or "budget" in text:
        return friendly["daily_cap"]
    if snap["status"] == "cancelled":
        return "This job was cancelled. " + STILL_OPEN
    return "Scoring this video did not finish, so there is no result for it. " + STILL_OPEN


def view(snap: dict, friendly: dict[str, str]) -> dict:
    """The snapshot as the browser gets it."""
    snap = dict(snap)
    snap["phases"] = phases(snap)
    snap["friendly"] = friendly_failure(snap, friendly)
    return snap


# ---------------------------------------------------------------------------------------------- planner card
def planner_view(plan: dict | None) -> dict:
    """The perception plan, shaped for the planner card, from plan.steps as etho.perception.planner writes them:
    one `propose` step (Muse Spark's answer, in its order), an `image_check` per phrase (SAM object counts on two
    frames), `video_probe` steps (fraction of probe frames with the expected animal count), maybe a `fallback`."""
    steps = (plan or {}).get("steps") or []
    if not steps:
        return {"ran": False, "note": "planner not run for this precomputed showcase"}
    prop = next((s for s in steps if s.get("step") == "propose"), {})
    answer = prop.get("answer") if isinstance(prop.get("answer"), dict) else {}
    expected = (plan or {}).get("expected_count")
    chosen = (plan or {}).get("phrase")
    checks = {s["phrase"]: s.get("counts", []) for s in steps if s.get("step") == "image_check" and "phrase" in s}
    probes = {s["phrase"]: s for s in steps if s.get("step") == "video_probe" and "phrase" in s}
    order = [p for p in (answer.get("phrases") or []) if isinstance(p, str) and p.strip()] or list(checks)
    for ph in list(checks) + list(probes):
        if ph not in order:
            order.append(ph)
    rows = []
    for i, ph in enumerate(order):
        counts = checks.get(ph)
        pr = probes.get(ph)
        ok = (sum(c >= expected for c in counts) if counts is not None and isinstance(expected, int) else None)
        rows.append({"rank": i + 1, "phrase": ph, "image_counts": counts, "image_frames_ok": ok,
                     "probe_frames": pr.get("frames") if pr else None,
                     "probe_frac": pr.get("frac_with_expected") if pr else None, "chosen": ph == chosen})
    cost = (plan or {}).get("cost_usd")
    if cost is None:
        cost = sum(float(s.get("cost_usd") or 0) for s in steps)
    return {"ran": True, "model": prop.get("model"), "phrases": rows, "chosen": chosen, "expected_count": expected,
            "animals": answer.get("animals"), "identity_cue": (plan or {}).get("identity_cue"),
            "identity_method": (plan or {}).get("identity_method"),
            "fallback": any(s.get("step") == "fallback" for s in steps), "cost_usd": round(float(cost), 6),
            "probe_pass_frac": 0.9}


# ---------------------------------------------------------------------------------------------- bout trace
def decide(v: dict, allow: frozenset[str]) -> tuple[str, str]:
    """How tier-1 `apply` treats one verdict: (decision, plain sentence). Mirrors etho.adjudication.tier1.apply."""
    lab, conf = v.get("label"), v.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else 0.0
    if lab == "unsure":
        return "set_aside", "Tier 1 answered unsure, so the rule result stands."
    if conf < CONFIDENT:
        return "set_aside", (f"Tier 1 confidence {conf:.2f} is below the {CONFIDENT:g} threshold, so the rule "
                             "result stands.")
    if lab not in allow:
        return "restriction", (f"Tier 1 said \"{lab}\", but this app lets tier 1 change a window only to "
                               f"{' or '.join(sorted(allow))}. The verdict was set aside by that restriction and "
                               "the rule result stands.")
    return "applied", f"Tier 1 said \"{lab}\" with confidence {conf:.2f}, and the final label follows it."


def read_verdicts(path, allow: frozenset[str], limit: int = 5000) -> list[dict]:
    rows = []
    try:
        lines = path.read_text().splitlines()[:limit]
    except OSError:
        return rows
    for line in lines:
        try:
            x = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(x, dict) or "s0" not in x or "s1" not in x:
            continue
        dec, why = decide(x, allow)
        rows.append({"s0": x["s0"], "s1": x["s1"], "rule_label": x.get("rule_label"), "source": x.get("source"),
                     "label": x.get("label"), "confidence": x.get("confidence"), "reason": x.get("reason"),
                     "decision": dec, "decision_text": why,
                     "strip": f"{x.get('seq', 'video')}_{x['s0']}_{x['s1']}.jpg"})
    return rows


def trace_index(verdicts: list[dict]) -> dict:
    """Compact list of every tier-1 window and how it was treated, so the ethogram can mark the interesting ones."""
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v["decision"]] = counts.get(v["decision"], 0) + 1
    return {"windows": [[v["s0"], v["s1"], v["rule_label"], v["label"], v["confidence"], v["decision"]]
                        for v in verdicts], "counts": counts}


def bout_trace(record: dict, verdicts: list[dict], behavior: str, s0: int, s1: int, fps: float) -> dict:
    """Which steps produced the final bout [s0, s1) of `behavior`: the rule result, the tier-1 verdicts for
    windows that touch it (each with how it was treated), and the step that decided the final label."""
    rule = [b for b in (record.get("rule_bouts") or {}).get(behavior, []) if b[0] < s1 and s0 < b[1]]
    touching = [v for v in verdicts if v["s0"] < s1 and s0 < v["s1"]]
    exact_rule = [s0, s1] in [list(b) for b in rule]
    applied = [v for v in touching if v["decision"] == "applied"]
    if any(v["label"] != behavior for v in applied):
        decided = "tier1"
        why = "A confident tier-1 verdict changed this window from the rule result"
    elif applied:
        decided = "rules"
        why = "The rules decided, and a confident tier-1 verdict confirmed it"
    elif any(v["decision"] == "restriction" for v in touching):
        decided = "rules"
        why = "The rules decided: a tier-1 verdict was set aside by the restriction"
    elif touching:
        decided = "rules"
        why = "The rules decided: the tier-1 verdicts were set aside (unsure or low confidence)"
    else:
        decided = "rules"
        why = "The rules decided: no tier-1 window was needed for this bout"
    return {"behavior": behavior, "s0": s0, "s1": s1, "start_s": round(s0 / fps, 2), "end_s": round(s1 / fps, 2),
            "rule_result": {"flagged": bool(rule), "bouts": [[b[0], b[1]] for b in rule], "exact": exact_rule},
            "tier1": touching, "decided_by": decided, "summary": why}


def allow_for(names: list[str]) -> frozenset[str]:
    return B.tier1_allow(names)
