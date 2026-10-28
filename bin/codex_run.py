#!/usr/bin/env python3
"""Codex runner with Etho's ChatGPT-Plus budget policy (CLAUDE.md, "Second opinions from Codex").

Used by bin/codex-review, bin/codex-ask, bin/codex-build, bin/codex-queue, bin/codex-usage.
Standard library only.

Profiles
  review (routine)  gpt-6.1-sol  high
  ask    (routine)  gpt-6.1-sol  xhigh
  --gate NAME       gpt-6-astra  xhigh; on a usage-limit (or model) error, gpt-6.1-sol at max
  build             gpt-6.1-sol  high; disabled unless --force (Aniket approves each use)
Never: max on Astra, Astra outside a gate, effort "ultra".

Budget (routine = every non-gate call)
  - routine reviews only for diffs touching gateway / tracking / scoring / eval code;
  - at most 1 routine review and 4 routine calls per ET calendar day; past that, queue;
  - from Nov 1 ET, no routine calls at all (the weekly allowance is reserved for the gates).

Usage limits
  - A usage-limit error (distinct from a model error) records the parsed reset time per model in
    ops/codex/limit.json. While a model's reset is in the future, calls to it are skipped at once.
  - Skipped or capped requests go to ops/codex/queue/ (one JSON file each). bin/codex-queue --drain
    (cron, every 10 min) runs them after the reset: gates first, then oldest first.
  - Never retry in a loop: each request is attempted at most once per reset window.
  - Never block: routine requests return immediately (exit 75, "Codex second opinion pending").
    Only gates wait. A gate blocked with its reset > 3 h away (or after its deadline) asks for one
    PushNotification to Aniket (printed + ops/codex/notify.jsonl).

Every attempt (with token usage from `codex exec --json` turn.completed) and every skip is logged to
ops/codex/usage.jsonl.

Exit codes: 0 ok, 75 queued/skipped, 76 gate blocked, 77 refused by policy, other = Codex failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SOL, ASTRA = "gpt-6.1-sol", "gpt-6-astra"
PROFILES = {
    "review": [(SOL, "high")],
    "ask": [(SOL, "xhigh")],
    "gate": [(ASTRA, "xhigh"), (SOL, "max")],
    "build": [(SOL, "high")],
}
ROUTINE_DAILY_CAP = 4
ROUTINE_REVIEWS_PER_DAY = 1
NO_ROUTINE_FROM_ET = dt.date(2026, 11, 1)
NOTIFY_IF_RESET_BEYOND = dt.timedelta(hours=3)
UNPARSED_RESET_BACKOFF = dt.timedelta(hours=1)  # unknown reset: probe again (once) after an hour
GATES = {  # latest useful time for each gate (ET)
    "eval-plan": dt.datetime(2026, 10, 31, 23, 59, tzinfo=ET),       # before targets are frozen
    "test-results": dt.datetime(2026, 11, 2, 23, 59, tzinfo=ET),     # before feature freeze
    "pre-submission": dt.datetime(2026, 11, 4, 12, 0, tzinfo=ET),    # internal target
}
REVIEW_PATHS = ("etho/gateway/", "etho/tracking/", "etho/scoring/", "etho/rules/",
                "etho/adjudication/", "eval/")
EXIT_QUEUED, EXIT_GATE_BLOCKED, EXIT_REFUSED = 75, 76, 77

LIMIT_RE = re.compile(r"usage limit|rate limit|hit your (usage )?limit|limit (reached|exceeded)|quota|"
                      r"too many requests|\b429\b", re.I)
MODEL_RE = re.compile(r"model_not_found|unknown model|unsupported model|invalid model|"
                      r"model .*(not (found|supported|available|exist)|does not exist|unavailable|no access|"
                      r"not allowed)|(do not|don't) have access to .*model", re.I)


class Policy(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# ---- small helpers ----------------------------------------------------------------------------
def now_utc() -> dt.datetime:
    override = os.environ.get("ETHO_NOW")  # tests only
    return dt.datetime.fromisoformat(override) if override else dt.datetime.now(dt.timezone.utc)


def ops_dir() -> Path:
    return Path(os.environ.get("CODEX_OPS_DIR", Path(__file__).resolve().parents[1] / "ops" / "codex"))


def read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def append_jsonl(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def fmt_et(t: dt.datetime | None) -> str:
    return t.astimezone(ET).strftime("%a %b %d %H:%M ET") if t else "unknown"


# ---- limits -----------------------------------------------------------------------------------
_DUR = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)


def parse_reset(text: str, now: dt.datetime) -> dt.datetime | None:
    """Best-effort reset time from a Codex limit message ('try again in 2 days 3 hours',
    'try again at Nov 3rd, 2026 4:12 PM', 'resets at 16:00'). Naive times are read as UTC (the VM
    clock). Returns None when nothing parses."""
    m = re.search(r"(?:try again|resets?|available again)\s+in\s+([^.\n]+)", text, re.I)
    if m:
        secs = sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u[0].lower()]
                   for n, u in _DUR.findall(m.group(1)))
        if secs:
            return now + dt.timedelta(seconds=secs)
    m = re.search(r"(?:try again|resets?|available again)\s+(?:at|on|after)\s+([^\n]+?)(?:\.\s|\.$|$)",
                  text, re.I)
    if m:
        raw = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group(1).strip().rstrip("."))
        raw = re.sub(r"\s+(UTC|GMT)$", "", raw)
        try:
            t = dt.datetime.fromisoformat(raw)
            return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
        for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d %Y %I:%M %p", "%Y-%m-%d %H:%M"):
            try:
                return dt.datetime.strptime(raw, fmt).replace(tzinfo=dt.timezone.utc)
            except ValueError:
                pass
        for fmt in ("%I:%M %p", "%H:%M"):
            try:
                t = dt.datetime.strptime(raw, fmt).time()
                cand = dt.datetime.combine(now.date(), t, tzinfo=dt.timezone.utc)
                return cand if cand > now else cand + dt.timedelta(days=1)
            except ValueError:
                pass
    return None


def limits() -> dict:
    p = ops_dir() / "limit.json"
    return json.loads(p.read_text()) if p.exists() else {}


def active_limit(model: str, now: dt.datetime) -> dt.datetime | None:
    d = limits().get(model)
    if not d:
        return None
    reset = dt.datetime.fromisoformat(d["reset"])
    return reset if reset > now else None


def record_limit(model: str, message: str, now: dt.datetime) -> dt.datetime:
    parsed = parse_reset(message, now)
    reset = parsed or now + UNPARSED_RESET_BACKOFF
    lim = limits()
    lim[model] = {"reset": reset.isoformat(), "reset_parsed": parsed is not None,
                  "message": message[:500], "seen": now.isoformat(timespec="seconds")}
    p = ops_dir() / "limit.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lim, indent=1, sort_keys=True) + "\n")
    return reset


# ---- queue ------------------------------------------------------------------------------------
def queue_dir() -> Path:
    return ops_dir() / "queue"


def enqueue(script: str, argv: list[str], gate: str | None, reason: str, now: dt.datetime) -> str:
    for it in queued_items():  # the same request is queued once, however often it is retried
        if it["script"] == script and it.get("argv") == argv and it.get("gate") == gate:
            return it["id"]
    qid = f"{now.strftime('%Y%m%dT%H%M%S')}-{script}-{uuid.uuid4().hex[:6]}"
    queue_dir().mkdir(parents=True, exist_ok=True)
    (queue_dir() / f"{qid}.json").write_text(json.dumps(
        {"id": qid, "created": now.isoformat(timespec="seconds"), "script": script, "gate": gate,
         "argv": argv, "reason": reason}, indent=1, sort_keys=True) + "\n")
    return qid


def queued_items() -> list[dict]:
    if not queue_dir().exists():
        return []
    items = [json.loads(p.read_text()) for p in sorted(queue_dir().glob("*.json"))]
    return sorted(items, key=lambda it: (it.get("gate") is None, it["created"], it["id"]))  # gates first


def resolve(qid: str, status: str, **extra) -> None:
    src = queue_dir() / f"{qid}.json"
    if not src.exists():
        return
    d = {**json.loads(src.read_text()), "status": status, "resolved": now_utc().isoformat(timespec="seconds"),
         **extra}
    dst = queue_dir() / status / f"{qid}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    src.unlink()


# ---- policy checks ----------------------------------------------------------------------------
def routine_check(script: str, usage: list[dict], now: dt.datetime) -> None:
    today = now.astimezone(ET).date()
    if today >= NO_ROUTINE_FROM_ET:
        raise Policy(EXIT_REFUSED, f"no routine Codex calls from {NO_ROUTINE_FROM_ET} ET (allowance reserved "
                                   "for the gates); use the Claude reviewer subagent")
    todays = {u["request"] for u in usage if u.get("mode") == "routine" and u.get("date_et") == str(today)
              and u.get("outcome") in ("ok", "failed", "limit", "model_error")}
    if len(todays) >= ROUTINE_DAILY_CAP:
        raise Policy(EXIT_QUEUED, f"routine cap reached ({ROUTINE_DAILY_CAP}/day ET)")
    reviews = {u["request"] for u in usage if u.get("mode") == "routine" and u.get("script") == "review"
               and u.get("date_et") == str(today) and u.get("outcome") in ("ok", "failed", "limit")}
    if script == "review" and len(reviews) >= ROUTINE_REVIEWS_PER_DAY:
        raise Policy(EXIT_QUEUED, "already one routine Codex review today (ET); batched into the next one")


def review_paths_check(changed: list[str]) -> None:
    if not any(p.startswith(REVIEW_PATHS) for p in changed):
        raise Policy(EXIT_REFUSED, "diff touches no gateway/tracking/scoring/eval code: use the Claude "
                                   "reviewer subagent only")


def model_check(model: str, effort: str, gate: bool) -> None:
    if effort == "ultra":
        raise Policy(EXIT_REFUSED, "reasoning effort 'ultra' is never allowed")
    if model == ASTRA and effort == "max":
        raise Policy(EXIT_REFUSED, "never use max on Astra")
    if model == ASTRA and not gate:
        raise Policy(EXIT_REFUSED, "Astra is reserved for the three gates (--gate)")


# ---- running Codex ----------------------------------------------------------------------------
def codex_exec(model: str, effort: str, sandbox: str, prompt: str, cwd: str | None) -> dict:
    """One `codex exec --json` call. Returns rc, final text, usage, and the terminal error message."""
    timeout = int(os.environ.get("CODEX_TIMEOUT", "2700"))
    try:
        p = subprocess.run(["codex", "exec", "--json", "--sandbox", sandbox, "-m", model, "-c",
                            f'model_reasoning_effort="{effort}"', prompt],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = f"timed out after {timeout}s"
    text, usage, error = "", {}, None
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
            text = ev["item"].get("text", "")
        elif t == "turn.completed":
            for k, v in (ev.get("usage") or {}).items():
                usage[k] = usage.get(k, 0) + int(v or 0)
        elif t == "turn.failed":
            error = (ev.get("error") or {}).get("message") or json.dumps(ev)
        elif t == "error" and error is None:
            error = ev.get("message")
    if rc != 0 and error is None:
        error = (err or "").strip().splitlines()[-1] if (err or "").strip() else f"exit {rc}"
    return {"rc": rc, "text": text, "usage": usage, "error": error, "raw": out, "stderr": err}


def classify(res: dict) -> str:
    if res["rc"] == 0:
        return "ok"
    msg = res["error"] or ""
    if LIMIT_RE.search(msg):
        return "limit"
    if MODEL_RE.search(msg):
        return "model_error"
    return "failed"


def gate_notify(gate: str, reset: dt.datetime | None, now: dt.datetime, waiting: list[str]) -> bool:
    """Ask for one PushNotification if the gate's reset is > 3 h away or after its deadline."""
    deadline = GATES[gate]
    if reset is not None and reset - now <= NOTIFY_IF_RESET_BEYOND and reset <= deadline:
        print(f"codex: GATE {gate} waiting for Codex: limits reset {fmt_et(reset)} (< 3 h); will run then")
        return False
    flag = ops_dir() / f"gate-blocked-{gate}.json"
    if flag.exists():
        print(f"codex: GATE {gate} still blocked (reset {fmt_et(reset)}); Aniket already notified")
        return False
    flag.write_text(json.dumps({"gate": gate, "blocked_at": now.isoformat(timespec="seconds"),
                                "reset": reset.isoformat() if reset else None,
                                "deadline": deadline.isoformat(), "waiting": waiting}) + "\n")
    late = " AFTER its deadline" if reset is None or reset > deadline else ""
    msg = (f"Codex gate '{gate}' blocked by usage limits until {fmt_et(reset)}{late} "
           f"(deadline {fmt_et(deadline)}). Waiting: {', '.join(waiting) or gate}. Spend a reset?")
    append_jsonl(ops_dir() / "notify.jsonl", {"time": now.isoformat(timespec="seconds"), "gate": gate,
                                              "message": msg, "sent": False})
    print(f"PUSH-NOTIFY-ANIKET: {msg}")
    return True


def run(script: str, prompt: str, out: Path, log: Path, *, gate: str | None = None, sandbox: str = "read-only",
        changed: list[str] | None = None, argv: list[str] | None = None, cwd: str | None = None,
        what: str = "") -> int:
    now = now_utc()
    usage_path = ops_dir() / "usage.jsonl"
    from_queue = os.environ.get("CODEX_FROM_QUEUE")
    request = from_queue or uuid.uuid4().hex[:12]
    mode = "gate" if gate else "routine"
    base = {"time": now.isoformat(timespec="seconds"), "date_et": str(now.astimezone(ET).date()),
            "script": script, "mode": mode, "gate": gate, "request": request, "queue_id": from_queue,
            "out": str(out)}

    def log_row(**kw):
        append_jsonl(usage_path, {**base, **kw})

    if gate and gate not in GATES:
        print(f"codex: REFUSED (unknown gate {gate!r}; gates are {sorted(GATES)})")
        return EXIT_REFUSED
    attempts = list(PROFILES["gate" if gate else script])
    if os.environ.get("CODEX_MODEL") or os.environ.get("CODEX_EFFORT"):
        attempts = [(os.environ.get("CODEX_MODEL") or attempts[0][0],
                     os.environ.get("CODEX_EFFORT") or attempts[0][1])]
    try:
        for m, e in attempts:
            model_check(m, e, bool(gate))
        if script == "review" and not gate:
            review_paths_check(changed or [])
        if not gate and script != "build":
            routine_check(script, read_jsonl(usage_path), now)
    except Policy as p:
        if p.code == EXIT_QUEUED:
            qid = from_queue or enqueue(script, argv or [], gate, str(p), now)
            log_row(model=attempts[0][0], effort=attempts[0][1], outcome="queued", reason=str(p), queue_id=qid)
            print(f"codex: QUEUED ({p}). Codex second opinion pending: {qid}")
        else:
            log_row(model=attempts[0][0], effort=attempts[0][1], outcome="refused", reason=str(p))
            print(f"codex: REFUSED ({p})")
        return p.code

    log.parent.mkdir(parents=True, exist_ok=True)
    results, skipped = [], []
    with log.open("w") as logf:
        for m, e in attempts:
            lim = active_limit(m, now_utc())
            if lim:  # known limit: skip without calling (never retry in a loop)
                skipped.append((m, lim))
                log_row(model=m, effort=e, outcome="skipped-limit", reset=lim.isoformat())
                continue
            logf.write(f"=== codex exec --json -m {m} -c model_reasoning_effort={e} ({now_utc().isoformat()})\n")
            res = codex_exec(m, e, sandbox, prompt, cwd)
            logf.write(res["raw"] + (res["stderr"] or ""))
            kind = classify(res)
            reset = record_limit(m, res["error"] or "", now_utc()) if kind == "limit" else None
            results.append((m, e, res, kind))
            log_row(model=m, effort=e, outcome=kind, exit=res["rc"], reset=reset.isoformat() if reset else None,
                    error=(res["error"] or "")[:300] or None, **{f"tok_{k}": v for k, v in res["usage"].items()})
            if kind in ("limit", "model_error"):
                continue  # next profile entry (gates only have one), never the same model again
            break

    limited = bool(skipped) or any(r[3] == "limit" for r in results)
    finished = results and results[-1][3] in ("ok", "failed")
    if finished or (results and not limited):  # a real answer/failure, or only model errors
        m, e, res, kind = results[-1]
        notes = [f"{r[0]} {r[3].replace('_', ' ')}" for r in results[:-1]] + \
                [f"{s[0]} skipped (limit until {fmt_et(s[1])})" for s in skipped]
        tok = res["usage"]
        tok_txt = (f" | tokens in {tok.get('input_tokens', 0)} (cached {tok.get('cached_input_tokens', 0)}) "
                   f"out {tok.get('output_tokens', 0)} (reasoning {tok.get('reasoning_output_tokens', 0)})"
                   if tok else "")
        status = "" if res["rc"] == 0 else f" | {kind.upper()} exit {res['rc']}: {res['error']}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"codex model: {m} | reasoning effort: {e}"
                       f"{' (fallback: ' + '; '.join(notes) + ')' if notes else ''}{tok_txt}{status}\n\n"
                       f"{res['text']}\n")
        if res["rc"] == 0 and gate:
            (ops_dir() / f"gate-blocked-{gate}.json").unlink(missing_ok=True)
        return res["rc"]

    # Every model for this request is (now) limited. Never wait; queue it.
    resets = [s[1] for s in skipped] + [dt.datetime.fromisoformat(limits()[r[0]]["reset"])
                                        for r in results if r[3] == "limit"]
    earliest = min(resets) if resets else None
    qid = from_queue or enqueue(script, argv or [], gate, f"usage limit until {fmt_et(earliest)}", now)
    log_row(model=attempts[0][0], effort=attempts[0][1], outcome="queued", reason="usage limit", queue_id=qid)
    if not gate:
        print(f"codex: usage limit until {fmt_et(earliest)}; skipped. Codex second opinion pending: {qid} "
              "(runs automatically after the reset; does not block the merge)")
        return EXIT_QUEUED
    waiting = [what or f"{script} {' '.join(argv or [])}".strip()]
    waiting += [f"{it['script']} ({it['gate'] or 'routine'})" for it in queued_items() if it["id"] != qid]
    gate_notify(gate, earliest, now_utc(), waiting)
    print(f"codex: GATE {gate} BLOCKED; queued as {qid}. The gated step must wait for this review.")
    return EXIT_GATE_BLOCKED


# ---- usage summary ----------------------------------------------------------------------------
def summary(now: dt.datetime | None = None) -> str:
    now = now or now_utc()
    usage = read_jsonl(ops_dir() / "usage.jsonl")
    today = str(now.astimezone(ET).date())
    called = [u for u in usage if u.get("outcome") in ("ok", "failed", "limit", "model_error")]
    rt = {u["request"] for u in called if u["mode"] == "routine" and u["date_et"] == today}
    rv = {u["request"] for u in called if u["mode"] == "routine" and u["script"] == "review"
          and u["date_et"] == today}
    week_start = now - dt.timedelta(days=7)
    per = defaultdict(lambda: [0, 0, 0])
    for u in called:
        if dt.datetime.fromisoformat(u["time"]) >= week_start:
            p = per[u["model"]]
            p[0] += 1
            p[1] += u.get("tok_input_tokens", 0)
            p[2] += u.get("tok_output_tokens", 0)
    models = ", ".join(f"{m} {n} calls {i / 1000:.0f}k in/{o / 1000:.1f}k out" for m, (n, i, o) in per.items()) \
        or "no calls"
    lim = [f"{m} until {fmt_et(dt.datetime.fromisoformat(d['reset']))}" for m, d in limits().items()
           if dt.datetime.fromisoformat(d["reset"]) > now]
    q = queued_items()
    pending = [n for n in read_jsonl(ops_dir() / "notify.jsonl") if not n.get("sent")]
    routine_txt = (f"routine today {len(rt)}/{ROUTINE_DAILY_CAP} (reviews {len(rv)}/{ROUTINE_REVIEWS_PER_DAY})"
                   if now.astimezone(ET).date() < NO_ROUTINE_FROM_ET else "routine off (Nov 1+)")
    return (f"Codex usage: {routine_txt}; 7d {models}; limits: {', '.join(lim) or 'none'}; "
            f"queue {len(q)} ({sum(1 for it in q if it.get('gate'))} gates)"
            f"{'; ' + str(len(pending)) + ' notification(s) to send' if pending else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", choices=["review", "ask", "build", "summary"])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--log", type=Path)
    ap.add_argument("--prompt")
    ap.add_argument("--gate")
    ap.add_argument("--sandbox", default="read-only")
    ap.add_argument("--changed-file", type=Path, help="file listing changed paths (review)")
    ap.add_argument("--cwd")
    ap.add_argument("--what", default="", help="short description of what waits on this call")
    ap.add_argument("--argv", default="[]", help="JSON list of the wrapper's argv (replayed from the queue)")
    a = ap.parse_args()
    if a.script == "summary":
        print(summary())
        return 0
    changed = a.changed_file.read_text().split() if a.changed_file else None
    return run(a.script, a.prompt, a.out, a.log, gate=a.gate, sandbox=a.sandbox, changed=changed,
               argv=json.loads(a.argv), cwd=a.cwd, what=a.what)


if __name__ == "__main__":
    sys.exit(main())
