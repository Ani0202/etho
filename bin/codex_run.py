#!/usr/bin/env python3
"""Codex runner with Etho's ChatGPT-Plus budget policy (CLAUDE.md, "Second opinions from Codex").

Used by bin/codex-review, bin/codex-ask, bin/codex-build, bin/codex-queue, bin/codex-usage.
Standard library only.

Profiles
  review (routine)  gpt-6.1-sol  high
  ask    (routine)  gpt-6.1-sol  xhigh
  --gate NAME       gpt-6-astra  xhigh; on a usage-limit (or model) error, gpt-6.1-sol at max
  build             gpt-6.1-sol  high; disabled unless --force (Aniket approves each use)
Never: max on Astra, max outside a gate, Astra outside a gate, effort "ultra" (checked after
normalizing case/whitespace, including CODEX_MODEL / CODEX_EFFORT overrides).

Budget (routine = every non-gate call)
  - routine reviews only for diffs touching gateway / tracking / scoring / eval code;
  - at most 1 routine review and 4 routine calls per ET calendar day; past that, queue;
  - from Nov 1 ET, no routine calls at all (the weekly allowance is reserved for the gates).

Usage limits
  - A usage-limit error records the parsed reset time per model in ops/codex/limit.json (a model
    error records a 6 h backoff there too). While a model is unavailable, calls to it are skipped
    at once and the request goes to ops/codex/queue/ (one JSON file each, deduplicated).
  - bin/codex-queue --drain (cron, every 10 min) runs queued requests after the reset: gates
    first, then oldest first.
  - Never retry in a loop: at most one attempt per model per request per invocation; failed queued
    items back off (1 h, then dropped after 3 attempts, with a notification for gates).
  - Never block: routine requests return immediately (exit 75, "Codex second opinion pending").
    Only gates wait. A gate blocked with its reset > 3 h away (or after its deadline) asks for one
    PushNotification to Aniket (printed + ops/codex/notify.jsonl).
  - Codex calls are serialized by a lock: a routine call that finds another call running is queued;
    a gate waits for the lock.

Every attempt (with token usage from `codex exec --json` turn.completed) and every skip is logged to
ops/codex/usage.jsonl.

Exit codes: 0 ok, 75 queued/skipped, 76 gate blocked, 77 refused by policy, other = Codex failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from contextlib import contextmanager
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
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
ROUTINE_DAILY_CAP = 4
ROUTINE_REVIEWS_PER_DAY = 1
NO_ROUTINE_FROM_ET = dt.date(2026, 11, 1)
NOTIFY_IF_RESET_BEYOND = dt.timedelta(hours=3)
UNPARSED_RESET_BACKOFF = dt.timedelta(hours=1)  # unknown reset: probe again (once) after an hour
MODEL_ERROR_BACKOFF = dt.timedelta(hours=6)
QUEUE_RETRY_BACKOFF = dt.timedelta(hours=1)
QUEUE_MAX_ATTEMPTS = 3
GATES = {  # latest useful time for each gate (ET)
    "eval-plan": dt.datetime(2026, 10, 31, 23, 59, tzinfo=ET),       # before targets are frozen
    "test-results": dt.datetime(2026, 11, 2, 23, 59, tzinfo=ET),     # before feature freeze
    "pre-submission": dt.datetime(2026, 11, 4, 12, 0, tzinfo=ET),    # internal target
}
# Code that warrants a routine Codex review. Update when the scoring/adjudication modules land.
REVIEW_PATHS = ("etho/gateway/", "etho/tracking/", "etho/scoring/", "etho/rules/",
                "etho/adjudication/", "eval/")
EXIT_QUEUED, EXIT_GATE_BLOCKED, EXIT_REFUSED = 75, 76, 77

LIMIT_RE = re.compile(r"usage[ _]limit|hit your (usage )?limit|rate[ _]limit|too many requests|\b429\b", re.I)
MODEL_RE = re.compile(r"model_not_found|unknown model|unsupported model|invalid model|"
                      r"model .*(not (found|supported|available|exist)|does not exist|unavailable|no access|"
                      r"not allowed)|(do not|don't) have access to .*model", re.I)


class Policy(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# ---- small helpers ----------------------------------------------------------------------------
def warn(msg: str) -> None:
    print(f"codex: warning: {msg}", file=sys.stderr)


def now_utc() -> dt.datetime:
    override = os.environ.get("ETHO_NOW") if os.environ.get("CODEX_TEST_MODE") == "1" else None
    if override:
        t = dt.datetime.fromisoformat(override)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def ops_dir() -> Path:
    return Path(os.environ.get("CODEX_OPS_DIR", Path(__file__).resolve().parents[1] / "ops" / "codex"))


def write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as e:
        warn(f"ignoring unreadable {p}: {e}")
        return default


def read_jsonl(p: Path) -> list[dict]:
    try:
        lines = p.read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for x in lines:
        if not x.strip():
            continue
        try:
            out.append(json.loads(x))
        except json.JSONDecodeError:
            warn(f"skipping a corrupt line in {p}")
    return out


def append_jsonl(p: Path, row: dict) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as e:
        warn(f"could not append to {p}: {e}")


def parse_time(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def fmt_et(t: dt.datetime | None) -> str:
    return t.astimezone(ET).strftime("%a %b %d %H:%M ET") if t else "unknown"


@contextmanager
def file_lock(name: str, blocking: bool):
    """Yields True if the lock was acquired (blocking waits), False if busy (non-blocking)."""
    ops_dir().mkdir(parents=True, exist_ok=True)
    with open(ops_dir() / name, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---- limits -----------------------------------------------------------------------------------
_DUR = re.compile(r"(\d+)\s*(weeks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)
_UNIT_S = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_reset(text: str, now: dt.datetime) -> dt.datetime | None:
    """Best-effort reset time from a Codex limit message: 'try again in 2 days 3 hours',
    'try again at Nov 3rd, 2026 4:12 PM', 'resets at 16:00 (UTC)', or structured
    'resets_in_seconds' / 'resets_at' fields. Naive times are read as UTC (the VM clock)."""
    m = re.search(r'"?resets?_in_seconds"?\s*[:=]\s*(\d+)', text)
    if m:
        return now + dt.timedelta(seconds=int(m.group(1)))
    m = re.search(r'"?resets?_at"?\s*[:=]\s*"?([0-9T:.+\-Z ]+)"?', text)
    if m:
        v = m.group(1).strip()
        if v.isdigit():
            return dt.datetime.fromtimestamp(int(v), dt.timezone.utc)
        t = parse_time(v.replace("Z", "+00:00"))
        if t:
            return t
    m = re.search(r"(?:try again|resets?|available again)\s+in\s+([^.\n]+)", text, re.I)
    if m:
        secs = sum(int(n) * _UNIT_S[u[0].lower()] for n, u in _DUR.findall(m.group(1)))
        if secs:
            return now + dt.timedelta(seconds=secs)
    m = re.search(r"(?:try again|resets?|available again)\s+(?:at|on|after)\s+([^\n]+?)(?:\.\s|\.$|$)",
                  text, re.I)
    if m:
        raw = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group(1).strip().rstrip("."))
        raw = re.sub(r"\s*\(?(UTC|GMT)\)?$", "", raw).strip()
        t = parse_time(raw)
        if t:
            return t
        for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d %Y %I:%M %p", "%Y-%m-%d %H:%M"):
            try:
                return dt.datetime.strptime(raw, fmt).replace(tzinfo=dt.timezone.utc)
            except ValueError:
                pass
        for fmt in ("%b %d %I:%M %p", "%b %d, %I:%M %p", "%B %d %I:%M %p"):  # no year: next occurrence
            try:
                t = dt.datetime.strptime(f"{now.year} {raw}", f"%Y {fmt}").replace(tzinfo=dt.timezone.utc)
                return t if t > now else t.replace(year=now.year + 1)
            except ValueError:
                pass
        for fmt in ("%I:%M %p", "%H:%M"):
            try:
                tt = dt.datetime.strptime(raw, fmt).time()
                cand = dt.datetime.combine(now.date(), tt, tzinfo=dt.timezone.utc)
                return cand if cand > now else cand + dt.timedelta(days=1)
            except ValueError:
                pass
    return None


def limits() -> dict:
    d = read_json(ops_dir() / "limit.json", {})
    return d if isinstance(d, dict) else {}


def active_limit(model: str, now: dt.datetime) -> dt.datetime | None:
    d = limits().get(model)
    reset = parse_time((d or {}).get("reset")) if isinstance(d, dict) else None
    return reset if reset and reset > now else None


def record_limit(model: str, kind: str, message: str, now: dt.datetime) -> dt.datetime:
    parsed = parse_reset(message, now) if kind == "limit" else None
    reset = parsed or now + (UNPARSED_RESET_BACKOFF if kind == "limit" else MODEL_ERROR_BACKOFF)
    with file_lock(".state.lock", blocking=True):
        lim = limits()
        lim[model] = {"kind": kind, "reset": reset.isoformat(), "reset_parsed": parsed is not None,
                      "message": message[:500], "seen": now.isoformat(timespec="seconds")}
        write_atomic(ops_dir() / "limit.json", json.dumps(lim, indent=1, sort_keys=True) + "\n")
    return reset


# ---- queue ------------------------------------------------------------------------------------
def queue_dir() -> Path:
    return ops_dir() / "queue"


def queued_items() -> list[dict]:
    items = []
    if queue_dir().exists():
        for p in sorted(queue_dir().glob("*.json")):
            d = read_json(p, None)
            if isinstance(d, dict) and "id" in d and "script" in d:
                items.append(d)
    return sorted(items, key=lambda it: (it.get("gate") is None, it.get("created", ""), it["id"]))  # gates first


def find_queued(script: str, argv: list[str], gate: str | None) -> dict | None:
    for it in queued_items():
        if it["script"] == script and it.get("argv") == argv and it.get("gate") == gate:
            return it
    return None


def save_item(it: dict) -> None:
    write_atomic(queue_dir() / f"{it['id']}.json", json.dumps(it, indent=1, sort_keys=True) + "\n")


def enqueue(script: str, argv: list[str], gate: str | None, reason: str, now: dt.datetime) -> str:
    if (it := find_queued(script, argv, gate)) is not None:  # the same request is queued once
        return it["id"]
    qid = f"{now.strftime('%Y%m%dT%H%M%S')}-{script}-{uuid.uuid4().hex[:6]}"
    save_item({"id": qid, "created": now.isoformat(timespec="seconds"), "script": script, "gate": gate,
               "argv": argv, "reason": reason, "attempts": 0})
    return qid


def resolve(qid: str, status: str, **extra) -> None:
    src = queue_dir() / f"{qid}.json"
    d = read_json(src, None)
    if d is None:
        return
    d = {**d, "status": status, "resolved": now_utc().isoformat(timespec="seconds"), **extra}
    write_atomic(queue_dir() / status / f"{qid}.json", json.dumps(d, indent=1, sort_keys=True) + "\n")
    src.unlink(missing_ok=True)
    if d.get("gate"):
        clear_gate_flag(d["gate"])


# ---- policy checks ----------------------------------------------------------------------------
def routine_check(script: str, usage: list[dict], now: dt.datetime) -> None:
    today = now.astimezone(ET).date()
    if today >= NO_ROUTINE_FROM_ET:
        raise Policy(EXIT_REFUSED, f"no routine Codex calls from {NO_ROUTINE_FROM_ET} ET (allowance reserved "
                                   "for the gates); use the Claude reviewer subagent")
    # Count calls that consumed allowance; a call rejected for a limit or model error used none.
    called = [u for u in usage if u.get("mode") == "routine" and u.get("date_et") == str(today)
              and u.get("outcome") in ("ok", "failed")]
    if len({u.get("request") for u in called}) >= ROUTINE_DAILY_CAP:
        raise Policy(EXIT_QUEUED, f"routine cap reached ({ROUTINE_DAILY_CAP}/day ET)")
    if script == "review" and len({u.get("request") for u in called if u.get("script") == "review"}) \
            >= ROUTINE_REVIEWS_PER_DAY:
        raise Policy(EXIT_QUEUED, "already one routine Codex review today (ET); batched into the next one")


def review_paths_check(changed: list[str]) -> None:
    if not any(p.startswith(REVIEW_PATHS) for p in changed):
        raise Policy(EXIT_REFUSED, "diff touches no gateway/tracking/scoring/eval code: use the Claude "
                                   "reviewer subagent only")


def model_check(model: str, effort: str, gate: bool) -> None:
    if effort == "ultra":
        raise Policy(EXIT_REFUSED, "reasoning effort 'ultra' is never allowed")
    if effort not in EFFORTS:
        raise Policy(EXIT_REFUSED, f"unknown reasoning effort {effort!r}")
    if "astra" in model and not gate:
        raise Policy(EXIT_REFUSED, "Astra is reserved for the three gates (--gate)")
    if "astra" in model and effort == "max":
        raise Policy(EXIT_REFUSED, "never use max on Astra")
    if effort == "max" and not gate:
        raise Policy(EXIT_REFUSED, "effort max is only for the gate fallback (Sol)")


# ---- running Codex ----------------------------------------------------------------------------
def codex_exec(model: str, effort: str, sandbox: str, prompt: str, cwd: str | None) -> dict:
    """One `codex exec --json` call. Returns rc, final text, usage, and the terminal error message."""
    timeout = int(os.environ.get("CODEX_TIMEOUT", "2700"))
    try:
        p = subprocess.run(["codex", "exec", "--json", "--sandbox", sandbox, "-m", model, "-c",
                            f'model_reasoning_effort="{effort}"', prompt],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                           timeout=timeout, cwd=cwd)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = f"timed out after {timeout}s"
    except OSError as e:  # e.g. codex not installed / not on PATH
        return {"rc": 127, "text": "", "usage": {}, "error": f"could not run codex: {e}", "raw": "",
                "stderr": str(e), "env_error": True}
    text, usage, error = "", {}, None
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        item = ev.get("item")
        if t == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            if item.get("text"):
                text = item["text"]
        elif t == "turn.completed" and isinstance(ev.get("usage"), dict):
            for k, v in ev["usage"].items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    usage[k] = usage.get(k, 0) + int(v)
        elif t == "turn.failed":
            e = ev.get("error")
            error = (e.get("message") if isinstance(e, dict) else e) or json.dumps(ev)
        elif t == "error" and error is None:
            error = ev.get("message")
    if rc == 0 and not text.strip():
        rc, error = 1, error or "codex returned no answer"
    if rc != 0 and error is None:
        error = (err or "").strip()[-2000:] or f"exit {rc}"
    return {"rc": rc, "text": text, "usage": usage, "error": str(error) if error else None, "raw": out,
            "stderr": err, "env_error": False}


def classify(res: dict) -> str:
    if res["rc"] == 0:
        return "ok"
    if res.get("env_error"):
        return "env_error"
    msg = res["error"] or ""
    if LIMIT_RE.search(msg):
        return "limit"
    if MODEL_RE.search(msg):
        return "model_error"
    return "failed"


def gate_flag(gate: str) -> Path:
    return ops_dir() / f"gate-blocked-{gate}.json"


def clear_gate_flag(gate: str) -> None:
    gate_flag(gate).unlink(missing_ok=True)


def notify(msg: str, gate: str | None) -> None:
    append_jsonl(ops_dir() / "notify.jsonl", {"time": now_utc().isoformat(timespec="seconds"), "gate": gate,
                                              "message": msg, "sent": False})
    print(f"PUSH-NOTIFY-ANIKET: {msg}")


def gate_notify(gate: str, reset: dt.datetime | None, now: dt.datetime, waiting: list[str], qid: str) -> bool:
    """Ask for one PushNotification if the gate's reset is > 3 h away or after its deadline."""
    deadline = GATES[gate]
    if reset is not None and reset - now <= NOTIFY_IF_RESET_BEYOND and reset <= deadline:
        print(f"codex: GATE {gate} waiting for Codex: limits reset {fmt_et(reset)} (< 3 h); will run then")
        return False
    flag = read_json(gate_flag(gate), None)
    if isinstance(flag, dict) and flag.get("queue_id") in {it["id"] for it in queued_items()}:
        print(f"codex: GATE {gate} still blocked (reset {fmt_et(reset)}); Aniket already notified")
        return False
    write_atomic(gate_flag(gate), json.dumps({"gate": gate, "queue_id": qid, "blocked_at": now.isoformat(),
                                              "reset": reset.isoformat() if reset else None,
                                              "deadline": deadline.isoformat(), "waiting": waiting}) + "\n")
    late = " AFTER its deadline" if reset is None or reset > deadline else ""
    notify(f"Codex gate '{gate}' blocked by usage limits until {fmt_et(reset)}{late} "
           f"(deadline {fmt_et(deadline)}). Waiting: {', '.join(waiting) or gate}. Spend a reset?", gate)
    return True


def run(script: str, prompt: str, out: Path, log: Path, *, gate: str | None = None, sandbox: str = "read-only",
        changed: list[str] | None = None, argv: list[str] | None = None, cwd: str | None = None,
        what: str = "", force: bool = False) -> int:
    argv = argv or []
    if gate is not None and gate not in GATES:
        print(f"codex: REFUSED (unknown gate {gate!r}; gates are {sorted(GATES)})")
        return EXIT_REFUSED
    if script == "build" and not force:
        print("codex: REFUSED (codex-build is disabled: needs --force and Aniket's approval)")
        return EXIT_REFUSED
    # Serialize Codex calls: routine calls queue if another call is running; gates wait their turn.
    with file_lock(".run.lock", blocking=bool(gate) or script == "build") as got:
        if not got:
            qid = os.environ.get("CODEX_FROM_QUEUE") or enqueue(script, argv, gate, "another Codex call running",
                                                                 now_utc())
            print(f"codex: another Codex call is running; QUEUED. Codex second opinion pending: {qid}")
            return EXIT_QUEUED
        return _run_locked(script, prompt, out, log, gate, sandbox, changed, argv, cwd, what)


def _run_locked(script, prompt, out, log, gate, sandbox, changed, argv, cwd, what) -> int:
    now = now_utc()
    usage_path = ops_dir() / "usage.jsonl"
    from_queue = os.environ.get("CODEX_FROM_QUEUE")
    match = None if from_queue else find_queued(script, argv, gate)
    request = from_queue or (match["id"] if match else uuid.uuid4().hex[:12])
    mode = "gate" if gate else "routine"
    base = {"time": now.isoformat(timespec="seconds"), "date_et": str(now.astimezone(ET).date()),
            "script": script, "mode": mode, "gate": gate, "request": request, "queue_id": from_queue,
            "out": str(out)}

    def log_row(**kw):
        append_jsonl(usage_path, {**base, **kw})

    attempts = list(PROFILES["gate" if gate else script])
    om, oe = os.environ.get("CODEX_MODEL"), os.environ.get("CODEX_EFFORT")
    if om or oe:
        attempts = [((om or attempts[0][0]), (oe or attempts[0][1]))]
    attempts = [(m.strip().lower(), e.strip().lower()) for m, e in attempts]
    try:
        for m, e in attempts:
            model_check(m, e, bool(gate))
        if script == "review" and not gate:
            review_paths_check(changed or [])
        if not gate and script != "build":
            routine_check(script, read_jsonl(usage_path), now)
    except Policy as p:
        if p.code == EXIT_QUEUED:
            qid = from_queue or enqueue(script, argv, gate, str(p), now)
            log_row(model=attempts[0][0], effort=attempts[0][1], outcome="queued", reason=str(p), queue_id=qid)
            print(f"codex: QUEUED ({p}). Codex second opinion pending: {qid}")
        else:
            log_row(model=attempts[0][0], effort=attempts[0][1], outcome="refused", reason=str(p))
            print(f"codex: REFUSED ({p})")
        return p.code

    results, skipped = [], []
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as logf:
        for m, e in attempts:  # each model at most once per invocation: never a retry loop
            lim = active_limit(m, now_utc())
            if lim:
                skipped.append((m, lim))
                log_row(model=m, effort=e, outcome="skipped-limit", reset=lim.isoformat())
                continue
            logf.write(f"=== codex exec --json -m {m} -c model_reasoning_effort={e} ({now_utc().isoformat()})\n")
            res = codex_exec(m, e, sandbox, prompt, cwd)
            logf.write(res["raw"] + (res["stderr"] or ""))
            logf.flush()
            kind = classify(res)
            reset = record_limit(m, kind, res["error"] or "", now_utc()) if kind in ("limit", "model_error") \
                else None
            results.append((m, e, res, kind))
            log_row(model=m, effort=e, outcome=kind, exit=res["rc"], reset=reset.isoformat() if reset else None,
                    error=(res["error"] or "")[:300] or None, **{f"tok_{k}": v for k, v in res["usage"].items()})
            if kind in ("limit", "model_error"):
                continue  # next profile entry (only gates have one)
            break

    finished = bool(results) and results[-1][3] in ("ok", "failed", "env_error")
    unavailable = bool(skipped) or any(r[3] in ("limit", "model_error") for r in results)
    if finished or (results and not unavailable):
        m, e, res, kind = results[-1]
        notes = [f"{r[0]} {r[3].replace('_', ' ')}" for r in results[:-1]] + \
                [f"{s[0]} skipped (unavailable until {fmt_et(s[1])})" for s in skipped]
        tok = res["usage"]
        tok_txt = (f" | tokens in {tok.get('input_tokens', 0)} (cached {tok.get('cached_input_tokens', 0)}) "
                   f"out {tok.get('output_tokens', 0)} (reasoning {tok.get('reasoning_output_tokens', 0)})"
                   if tok else "")
        status = "" if res["rc"] == 0 else f" | {kind.upper()} exit {res['rc']}: {res['error']}"
        write_atomic(out, f"codex model: {m} | reasoning effort: {e}"
                          f"{' (fallback: ' + '; '.join(notes) + ')' if notes else ''}{tok_txt}{status}\n\n"
                          f"{res['text']}\n")
        if res["rc"] == 0:
            # A direct run supersedes an identical queued request (never replay it later).
            if match is not None:
                resolve(match["id"], "done", superseded_by=request, out=str(out))
            if gate:
                clear_gate_flag(gate)
        return res["rc"]

    # Every model for this request is unavailable. Never wait; queue it (builds are not queued).
    resets = [s[1] for s in skipped] + [active_limit(r[0], now) or now for r in results
                                        if r[3] in ("limit", "model_error")]
    earliest = min(resets) if resets else None
    if script == "build":
        print(f"codex: usage limit until {fmt_et(earliest)}; build not run (builds are never queued)")
        return EXIT_QUEUED
    qid = from_queue or enqueue(script, argv, gate, f"usage limit until {fmt_et(earliest)}", now)
    log_row(model=attempts[0][0], effort=attempts[0][1], outcome="queued", reason="usage limit", queue_id=qid)
    if not gate:
        print(f"codex: usage limit until {fmt_et(earliest)}; skipped. Codex second opinion pending: {qid} "
              "(runs automatically after the reset; does not block the merge)")
        return EXIT_QUEUED
    waiting = [what or f"{script} {' '.join(argv)}".strip()]
    waiting += [f"{it['script']} ({it.get('gate') or 'routine'})" for it in queued_items() if it["id"] != qid]
    gate_notify(gate, earliest, now_utc(), waiting, qid)
    print(f"codex: GATE {gate} BLOCKED; queued as {qid}. The gated step must wait for this review.")
    return EXIT_GATE_BLOCKED


# ---- usage summary ----------------------------------------------------------------------------
def summary(now: dt.datetime | None = None) -> str:
    now = now or now_utc()
    usage = read_jsonl(ops_dir() / "usage.jsonl")
    today = str(now.astimezone(ET).date())
    called = [u for u in usage if u.get("outcome") in ("ok", "failed")]
    rt = {u.get("request") for u in called if u.get("mode") == "routine" and u.get("date_et") == today}
    rv = {u.get("request") for u in called if u.get("mode") == "routine" and u.get("script") == "review"
          and u.get("date_et") == today}
    week_start = now - dt.timedelta(days=7)
    per = defaultdict(lambda: [0, 0, 0])
    for u in called:
        t = parse_time(u.get("time"))
        if t and t >= week_start:
            p = per[u.get("model", "?")]
            p[0] += 1
            p[1] += u.get("tok_input_tokens", 0)
            p[2] += u.get("tok_output_tokens", 0)
    models = ", ".join(f"{m} {n} calls {i / 1000:.0f}k in/{o / 1000:.1f}k out" for m, (n, i, o) in per.items()) \
        or "no calls"
    lim = [f"{m} until {fmt_et(r)}" for m in limits() if (r := active_limit(m, now))]
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
    ap.add_argument("--force", action="store_true", help="required for build (Aniket's approval)")
    ap.add_argument("--sandbox", default="read-only")
    ap.add_argument("--changed-file", type=Path, help="file listing changed paths, one per line (review)")
    ap.add_argument("--cwd")
    ap.add_argument("--what", default="", help="short description of what waits on this call")
    ap.add_argument("--argv", default="[]", help="JSON list of the wrapper's argv (replayed from the queue)")
    a = ap.parse_args()
    if a.script == "summary":
        print(summary())
        return 0
    changed = a.changed_file.read_text().splitlines() if a.changed_file else None
    return run(a.script, a.prompt, a.out, a.log, gate=a.gate, sandbox=a.sandbox, changed=changed,
               argv=json.loads(a.argv), cwd=a.cwd, what=a.what, force=a.force)


if __name__ == "__main__":
    sys.exit(main())
