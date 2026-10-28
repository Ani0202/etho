"""bin/codex_run.py + bin/codex-queue budget policy against a stub `codex` (no network, no real Codex)."""
# ruff: noqa: E501  (the stub is a long shell script in a string)
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("codex_run", ROOT / "bin" / "codex_run.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

# Mimics `codex exec --json`: JSONL events on stdout. Per-model behavior via STUB_ASTRA / STUB_SOL.
STUB = r"""#!/usr/bin/env bash
# args: exec --json --sandbox S -m MODEL -c model_reasoning_effort="E" PROMPT
model=$6; effort=${8#model_reasoning_effort=}; effort=${effort//\"/}
echo "$model $effort" >> "$STUB_CALLS"
case "$model" in gpt-6-astra) mode=${STUB_ASTRA:-ok}; reset=${STUB_ASTRA_RESET:-at Nov 6th, 2026 9:00 AM};;
                 *) mode=${STUB_SOL:-ok}; reset=${STUB_SOL_RESET:-in 2 hours 5 minutes};; esac
echo '{"type":"thread.started","thread_id":"t"}'
echo '{"type":"turn.started"}'
if [ "$mode" = limit ]; then
  msg="You've hit your usage limit. Upgrade to Pro or try again $reset."
  echo "{\"type\":\"error\",\"message\":\"$msg\"}"
  echo "{\"type\":\"turn.failed\",\"error\":{\"message\":\"$msg\"}}"; exit 1; fi
if [ "$mode" = model ]; then
  echo "{\"type\":\"turn.failed\",\"error\":{\"message\":\"The '$model' model is not supported when using Codex with a ChatGPT account.\"}}"; exit 1; fi
echo "{\"type\":\"item.completed\",\"item\":{\"id\":\"i\",\"type\":\"agent_message\",\"text\":\"answer from $model at $effort\"}}"
echo '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":200,"output_tokens":50,"reasoning_output_tokens":30}}'
"""

GW = ["etho/gateway/gateway.py"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "codex").write_text(STUB)
    (b / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_OPS_DIR", str(tmp_path / "ops"))
    monkeypatch.setenv("STUB_CALLS", str(tmp_path / "calls"))
    monkeypatch.setenv("ETHO_NOW", "2026-10-28T15:00:00+00:00")
    for k in ("CODEX_MODEL", "CODEX_EFFORT", "STUB_ASTRA", "STUB_SOL", "CODEX_FROM_QUEUE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "calls").write_text("")

    class E:
        ops = tmp_path / "ops"

        def run(self, script="review", gate=None, changed=GW, name="x"):
            out = tmp_path / f"{name}.md"
            rc = cr.run(script, "prompt", out, tmp_path / f"{name}.log", gate=gate, changed=changed,
                        argv=["--head", "abc", "def"] if script == "review" else ["ctx.md", "q"])
            return rc, (out.read_text() if out.exists() else None)

        def usage(self):
            return cr.read_jsonl(self.ops / "usage.jsonl")

        def calls(self):
            return (tmp_path / "calls").read_text().split("\n")[:-1]
    return E()


# ---- models and effort ------------------------------------------------------------------------
def test_routine_review_sol_high_logs_tokens(env):
    rc, out = env.run("review")
    assert rc == 0
    assert out.splitlines()[0] == ("codex model: gpt-6.1-sol | reasoning effort: high | tokens in 1000 "
                                   "(cached 200) out 50 (reasoning 30)")
    u = env.usage()[-1]
    assert (u["mode"], u["model"], u["effort"], u["outcome"]) == ("routine", "gpt-6.1-sol", "high", "ok")
    assert (u["tok_input_tokens"], u["tok_output_tokens"], u["tok_reasoning_output_tokens"]) == (1000, 50, 30)


def test_routine_ask_sol_xhigh(env):
    assert env.run("ask")[0] == 0 and env.calls() == ["gpt-6.1-sol xhigh"]


def test_gate_astra_xhigh_not_counted_as_routine(env):
    for i in range(5):
        assert env.run("ask", gate="eval-plan", name=f"g{i}")[0] == 0
    assert env.calls() == ["gpt-6-astra xhigh"] * 5
    assert env.run("review")[0] == 0


@pytest.mark.parametrize("model,effort,gate", [
    ("gpt-6-astra", "max", "eval-plan"),   # never max on Astra
    ("gpt-6-astra", "high", None),         # Astra only for gates
    ("gpt-6.1-sol", "ultra", None),        # never ultra
])
def test_overrides_cannot_break_the_rules(env, monkeypatch, model, effort, gate):
    monkeypatch.setenv("CODEX_MODEL", model)
    monkeypatch.setenv("CODEX_EFFORT", effort)
    assert env.run("ask", gate=gate)[0] == cr.EXIT_REFUSED and env.calls() == []


def test_build_requires_force():
    p = subprocess.run([str(ROOT / "bin" / "codex-build"), "x", "spec"], capture_output=True, text=True)
    assert p.returncode == 77 and "--force" in p.stderr


# ---- budget rules -----------------------------------------------------------------------------
def test_review_refused_for_non_critical_paths(env):
    assert env.run("review", changed=["docs/STATE.md", "etho/ui/app.py"])[0] == cr.EXIT_REFUSED
    assert env.calls() == []


def test_one_routine_review_per_et_day_then_queued(env):
    assert env.run("review")[0] == 0
    assert env.run("review", name="y")[0] == cr.EXIT_QUEUED
    assert len(env.calls()) == 1 and len(cr.queued_items()) == 1


def test_routine_cap_four_per_et_day(env, monkeypatch):
    assert env.run("review")[0] == 0
    for i in range(3):
        assert env.run("ask", name=f"a{i}")[0] == 0
    assert env.run("ask", name="a4")[0] == cr.EXIT_QUEUED
    monkeypatch.setenv("ETHO_NOW", "2026-10-29T03:59:00+00:00")  # still Oct 28 ET
    assert env.run("ask", name="a5")[0] == cr.EXIT_QUEUED
    monkeypatch.setenv("ETHO_NOW", "2026-10-29T05:00:00+00:00")  # Oct 29 ET
    assert env.run("ask", name="a6")[0] == 0
    assert len(env.calls()) == 5


def test_no_routine_from_nov_1_et_but_gates_run(env, monkeypatch):
    monkeypatch.setenv("ETHO_NOW", "2026-11-01T03:30:00+00:00")  # Oct 31 23:30 ET
    assert env.run("ask")[0] == 0
    monkeypatch.setenv("ETHO_NOW", "2026-11-01T04:30:00+00:00")  # Nov 1 00:30 ET
    assert env.run("ask", name="b")[0] == cr.EXIT_REFUSED
    assert env.run("review", name="c")[0] == cr.EXIT_REFUSED
    assert env.run("ask", gate="pre-submission", name="g")[0] == 0


# ---- usage limits -----------------------------------------------------------------------------
def test_limit_saved_then_routine_skips_immediately_and_queues(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_SOL", "limit")
    assert env.run("ask")[0] == cr.EXIT_QUEUED
    lim = json.loads((env.ops / "limit.json").read_text())["gpt-6.1-sol"]
    assert lim["reset"] == "2026-10-28T17:05:00+00:00" and lim["reset_parsed"]
    assert "Codex second opinion pending" in capsys.readouterr().out
    # While the reset is in the future: no call at all, just queued.
    assert env.run("review", name="r")[0] == cr.EXIT_QUEUED
    assert env.calls() == ["gpt-6.1-sol xhigh"]  # exactly one real call, never a retry loop
    assert len(cr.queued_items()) == 2
    assert [u["outcome"] for u in env.usage()] == ["limit", "queued", "skipped-limit", "queued"]


def test_model_error_is_not_a_limit(env, monkeypatch):
    monkeypatch.setenv("STUB_SOL", "model")
    rc, out = env.run("ask")
    assert rc == 1 and not (env.ops / "limit.json").exists() and cr.queued_items() == []
    assert "MODEL_ERROR" in out.splitlines()[0]


def test_gate_falls_back_to_sol_max_on_astra_limit(env, monkeypatch):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    rc, out = env.run("ask", gate="eval-plan")
    assert rc == 0 and env.calls() == ["gpt-6-astra xhigh", "gpt-6.1-sol max"]
    assert out.startswith("codex model: gpt-6.1-sol | reasoning effort: max (fallback: gpt-6-astra limit)")
    # Next gate call skips Astra without calling it (its limit is recorded).
    assert env.run("ask", gate="eval-plan", name="g2")[0] == 0
    assert env.calls()[-1] == "gpt-6.1-sol max" and env.calls().count("gpt-6-astra xhigh") == 1


def test_gate_blocked_reset_within_3h_waits_without_notifying(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")  # Sol resets in 2h05
    assert env.run("ask", gate="eval-plan")[0] == cr.EXIT_GATE_BLOCKED
    out = capsys.readouterr().out
    assert "PUSH-NOTIFY-ANIKET" not in out and "< 3 h" in out
    assert cr.queued_items()[0]["gate"] == "eval-plan"


def test_gate_blocked_reset_beyond_3h_notifies_once(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")
    monkeypatch.setenv("STUB_SOL_RESET", "in 5 hours")
    assert env.run("review", gate="pre-submission")[0] == cr.EXIT_GATE_BLOCKED
    first = capsys.readouterr().out
    assert first.count("PUSH-NOTIFY-ANIKET") == 1
    assert "Wed Oct 28 16:00 ET" in first and "Waiting: review --head abc def" in first
    assert env.run("review", gate="pre-submission", name="again")[0] == cr.EXIT_GATE_BLOCKED
    assert "PUSH-NOTIFY-ANIKET" not in capsys.readouterr().out
    assert len(cr.queued_items()) == 1  # the retried gate is not queued twice
    assert len(cr.read_jsonl(env.ops / "notify.jsonl")) == 1
    assert env.calls() == ["gpt-6-astra xhigh", "gpt-6.1-sol max"]  # second attempt made no calls


def test_drain_after_reset_runs_gates_first_then_oldest(env, monkeypatch):
    monkeypatch.setenv("STUB_SOL", "limit")
    assert env.run("ask", name="routine1")[0] == cr.EXIT_QUEUED        # routine, oldest
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("ETHO_NOW", "2026-10-28T15:10:00+00:00")
    assert env.run("ask", gate="eval-plan", name="gate1")[0] == cr.EXIT_GATE_BLOCKED  # Astra limited now too
    items = cr.queued_items()
    assert [it["gate"] for it in items] == ["eval-plan", None]          # gates first

    def runnable():
        return [it for it in cr.queued_items()
                if not all(cr.active_limit(m, cr.now_utc()) for m, _ in cr.PROFILES["gate" if it["gate"] else it["script"]])]
    assert runnable() == []  # before the reset nothing runs
    # After Sol's reset (Astra still limited): the gate can run on Sol max, and goes first.
    monkeypatch.setenv("ETHO_NOW", "2026-10-28T17:30:00+00:00")
    assert [it["gate"] for it in runnable()] == ["eval-plan", None]


def test_drain_script_end_to_end(env, monkeypatch, tmp_path):
    """bin/codex-queue --drain replays queued requests through the real wrappers after the reset."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "AGENTS.md").write_text("x\n")
    (repo / "ctx.md").write_text("claim\n")
    for d in ("bin",):
        (repo / d).mkdir()
    for f in ("codex_run.py", "codex-ask", "codex-review", "codex-queue"):
        (repo / "bin" / f).write_text((ROOT / "bin" / f).read_text())
        (repo / "bin" / f).chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   check=True)
    e = {**os.environ, "CODEX_OPS_DIR": str(repo / "ops" / "codex"), "STUB_SOL": "limit"}
    (repo / "ops" / "codex").mkdir(parents=True)
    p = subprocess.run([str(repo / "bin" / "codex-ask"), "ctx.md", "Is it right?"], cwd=repo, env=e,
                       capture_output=True, text=True)
    assert p.returncode == 75 and "second opinion pending" in p.stdout
    # Still limited: drain does nothing and makes no call.
    n_calls = len(Path(e["STUB_CALLS"]).read_text().splitlines())
    p = subprocess.run([str(repo / "bin" / "codex-queue"), "--drain"], cwd=repo, env=e, capture_output=True,
                       text=True)
    assert p.returncode == 0 and len(Path(e["STUB_CALLS"]).read_text().splitlines()) == n_calls
    # After the reset it runs once and resolves the item.
    e2 = {**e, "ETHO_NOW": "2026-10-28T18:00:00+00:00", "STUB_SOL": "ok"}
    p = subprocess.run([str(repo / "bin" / "codex-queue"), "--drain"], cwd=repo, env=e2, capture_output=True,
                       text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert list((repo / "ops" / "codex" / "queue").glob("*.json")) == []
    assert len(list((repo / "ops" / "codex" / "queue" / "done").glob("*.json"))) == 1
    asks = sorted((repo / "ops" / "codex").glob("ask-*.md"))
    assert asks and asks[-1].read_text().startswith("codex model: gpt-6.1-sol | reasoning effort: xhigh")


def test_parse_reset_formats():
    now = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    assert cr.parse_reset("try again in 2 days 3 hours", now) == datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    assert cr.parse_reset("Try again at Nov 3rd, 2026 4:12 PM.", now) == \
        datetime(2026, 11, 3, 16, 12, tzinfo=timezone.utc)
    assert cr.parse_reset("limit resets at 16:00", now) == datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)
    assert cr.parse_reset("you hit a limit", now) is None


def test_unparsed_reset_backs_off_one_hour(env, monkeypatch):
    monkeypatch.setenv("STUB_SOL", "limit")
    monkeypatch.setenv("STUB_SOL_RESET", "later")
    env.run("ask")
    lim = json.loads((env.ops / "limit.json").read_text())["gpt-6.1-sol"]
    assert lim["reset"] == "2026-10-28T16:00:00+00:00" and lim["reset_parsed"] is False


def test_summary_line(env, monkeypatch):
    env.run("review")
    monkeypatch.setenv("STUB_SOL", "limit")
    env.run("ask", name="q")
    s = cr.summary()
    assert s.startswith("Codex usage: routine today 2/4 (reviews 1/1)")
    assert "gpt-6.1-sol until Wed Oct 28 13:05 ET" in s and "queue 1 (0 gates)" in s
