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

# Mimics `codex exec --json`: JSONL events on stdout. Per-model behavior via STUB_ASTRA / STUB_SOL:
# ok | limit | model | fail | empty | nested
STUB = r"""#!/usr/bin/env bash
# args: exec --json --sandbox S -m MODEL -c model_reasoning_effort="E" PROMPT
model=$6; effort=${8#model_reasoning_effort=}; effort=${effort//\"/}
echo "$model $effort" >> "$STUB_CALLS"
case "$model" in gpt-6-astra) mode=${STUB_ASTRA:-ok}; reset=${STUB_ASTRA_RESET:-at Nov 6th, 2026 9:00 AM};;
                 *) mode=${STUB_SOL:-ok}; reset=${STUB_SOL_RESET:-in 2 hours 5 minutes};; esac
echo '{"type":"thread.started","thread_id":"t"}'
echo '{"type":"turn.started"}'
case "$mode" in
  limit) msg="You've hit your usage limit. Upgrade to Pro or try again $reset."
         echo "{\"type\":\"error\",\"message\":\"$msg\"}"
         echo "{\"type\":\"turn.failed\",\"error\":{\"message\":\"$msg\"}}"; exit 1;;
  model) echo "{\"type\":\"turn.failed\",\"error\":{\"message\":\"The '$model' model is not supported when using Codex with a ChatGPT account.\"}}"; exit 1;;
  fail)  echo '{"type":"turn.failed","error":"stream disconnected before completion"}'; exit 1;;
  empty) echo '{"type":"turn.completed","usage":{"input_tokens":5}}'; exit 0;;
esac
echo "{\"type\":\"item.completed\",\"item\":{\"id\":\"i\",\"type\":\"agent_message\",\"text\":\"answer from $model at $effort\"}}"
if [ "$mode" = nested ]; then
  echo '{"type":"turn.completed","usage":{"input_tokens":7,"details":{"x":1},"output_tokens":"n/a"}}'
else
  echo '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":200,"output_tokens":50,"reasoning_output_tokens":30}}'
fi
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
    monkeypatch.setenv("CODEX_TEST_MODE", "1")
    monkeypatch.setenv("ETHO_NOW", "2026-10-28T15:00:00+00:00")
    for k in ("CODEX_MODEL", "CODEX_EFFORT", "STUB_ASTRA", "STUB_SOL", "CODEX_FROM_QUEUE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "calls").write_text("")

    class E:
        ops = tmp_path / "ops"
        tmp = tmp_path

        def run(self, script="review", gate=None, changed=GW, name="x", argv=None):
            out = tmp_path / f"{name}.md"
            if argv is None:
                argv = ["--head", "abc", "def"] if script == "review" else ["ctx.md", "q"]
            rc = cr.run(script, "prompt", out, tmp_path / f"{name}.log", gate=gate, changed=changed, argv=argv)
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
    ("gpt-6-astra", "MAX", "eval-plan"),
    (" GPT-6-Astra ", " xhigh ", None),    # Astra only for gates, any spelling
    ("gpt-6-astra", "high", None),
    ("gpt-6.1-sol", "ultra", None),        # never ultra
    ("gpt-6.1-sol", "Ultra", "eval-plan"),
    ("gpt-6.1-sol", "max", None),          # max only as the gate fallback
    ("gpt-6.1-sol", "turbo", None),        # unknown effort
])
def test_overrides_cannot_break_the_rules(env, monkeypatch, model, effort, gate):
    monkeypatch.setenv("CODEX_MODEL", model)
    monkeypatch.setenv("CODEX_EFFORT", effort)
    assert env.run("ask", gate=gate)[0] == cr.EXIT_REFUSED and env.calls() == []


def test_build_requires_force_in_wrapper_and_runner(env):
    p = subprocess.run([str(ROOT / "bin" / "codex-build"), "x", "spec"], capture_output=True, text=True)
    assert p.returncode == 77 and "--force" in p.stderr
    assert env.run("build", argv=[])[0] == cr.EXIT_REFUSED and env.calls() == []


def test_etho_now_ignored_outside_test_mode(monkeypatch):
    monkeypatch.delenv("CODEX_TEST_MODE", raising=False)
    monkeypatch.setenv("ETHO_NOW", "2020-01-01T00:00:00")
    assert cr.now_utc().year >= 2026


# ---- budget rules -----------------------------------------------------------------------------
def test_review_refused_for_non_critical_paths(env):
    assert env.run("review", changed=["docs/STATE.md", "etho/ui/app.py"])[0] == cr.EXIT_REFUSED
    assert env.calls() == []


def test_one_routine_review_per_et_day_then_queued(env):
    assert env.run("review")[0] == 0
    assert env.run("review", name="y", argv=["--head", "b2", "def"])[0] == cr.EXIT_QUEUED
    assert len(env.calls()) == 1 and len(cr.queued_items()) == 1


def test_routine_cap_four_per_et_day(env, monkeypatch):
    assert env.run("review")[0] == 0
    for i in range(3):
        assert env.run("ask", name=f"a{i}", argv=[f"c{i}", "q"])[0] == 0
    assert env.run("ask", name="a4", argv=["c4", "q"])[0] == cr.EXIT_QUEUED
    monkeypatch.setenv("ETHO_NOW", "2026-10-29T03:59:00+00:00")  # still Oct 28 ET
    assert env.run("ask", name="a5", argv=["c5", "q"])[0] == cr.EXIT_QUEUED
    monkeypatch.setenv("ETHO_NOW", "2026-10-29T05:00:00+00:00")  # Oct 29 ET
    assert env.run("ask", name="a6", argv=["c6", "q"])[0] == 0
    assert len(env.calls()) == 5


def test_no_routine_from_nov_1_et_but_gates_run(env, monkeypatch):
    monkeypatch.setenv("ETHO_NOW", "2026-11-01T03:30:00+00:00")  # Oct 31 23:30 ET
    assert env.run("ask")[0] == 0
    monkeypatch.setenv("ETHO_NOW", "2026-11-01T04:30:00+00:00")  # Nov 1 00:30 ET
    assert env.run("ask", name="b", argv=["b", "q"])[0] == cr.EXIT_REFUSED
    assert env.run("review", name="c")[0] == cr.EXIT_REFUSED
    assert env.run("ask", gate="pre-submission", name="g")[0] == 0


def test_routine_call_queues_when_another_call_holds_the_lock(env):
    with cr.file_lock(".run.lock", blocking=True):
        assert env.run("ask")[0] == cr.EXIT_QUEUED
    assert env.calls() == [] and len(cr.queued_items()) == 1


# ---- usage limits -----------------------------------------------------------------------------
def test_limit_saved_then_routine_skips_immediately_and_queues(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_SOL", "limit")
    assert env.run("ask")[0] == cr.EXIT_QUEUED
    lim = json.loads((env.ops / "limit.json").read_text())["gpt-6.1-sol"]
    assert lim["reset"] == "2026-10-28T17:05:00+00:00" and lim["reset_parsed"] and lim["kind"] == "limit"
    assert "Codex second opinion pending" in capsys.readouterr().out
    assert env.run("review", name="r")[0] == cr.EXIT_QUEUED       # no call while limited
    assert env.calls() == ["gpt-6.1-sol xhigh"]                    # exactly one real call
    assert len(cr.queued_items()) == 2
    assert [u["outcome"] for u in env.usage()] == ["limit", "queued", "skipped-limit", "queued"]


def test_model_error_is_not_a_usage_limit_but_backs_off(env, monkeypatch):
    monkeypatch.setenv("STUB_SOL", "model")
    rc, out = env.run("ask")
    assert rc == cr.EXIT_QUEUED
    lim = json.loads((env.ops / "limit.json").read_text())["gpt-6.1-sol"]
    assert lim["kind"] == "model_error" and lim["reset"] == "2026-10-28T21:00:00+00:00"  # 6 h backoff


def test_context_limit_error_is_not_a_usage_limit(env, monkeypatch, tmp_path):
    res = {"rc": 1, "error": "context length limit exceeded", "env_error": False}
    assert cr.classify(res) == "failed"


def test_gate_falls_back_to_sol_max_on_astra_limit(env, monkeypatch):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    rc, out = env.run("ask", gate="eval-plan")
    assert rc == 0 and env.calls() == ["gpt-6-astra xhigh", "gpt-6.1-sol max"]
    assert out.startswith("codex model: gpt-6.1-sol | reasoning effort: max (fallback: gpt-6-astra limit)")
    assert env.run("ask", gate="eval-plan", name="g2")[0] == 0      # Astra skipped, not called again
    assert env.calls().count("gpt-6-astra xhigh") == 1


def test_gate_falls_back_on_astra_model_error(env, monkeypatch):
    monkeypatch.setenv("STUB_ASTRA", "model")
    rc, out = env.run("ask", gate="test-results")
    assert rc == 0 and env.calls() == ["gpt-6-astra xhigh", "gpt-6.1-sol max"]


def test_gate_blocked_reset_within_3h_waits_without_notifying(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")  # Sol resets in 2h05
    assert env.run("ask", gate="eval-plan")[0] == cr.EXIT_GATE_BLOCKED
    out = capsys.readouterr().out
    assert "PUSH-NOTIFY-ANIKET" not in out and "< 3 h" in out
    assert cr.queued_items()[0]["gate"] == "eval-plan"


def test_gate_blocked_short_reset_but_past_deadline_notifies(env, monkeypatch, capsys):
    monkeypatch.setenv("ETHO_NOW", "2026-11-04T16:30:00+00:00")  # 11:30 ET, deadline 12:00 ET
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")
    monkeypatch.setenv("STUB_SOL_RESET", "in 1 hour")             # 12:30 ET: < 3 h but too late
    assert env.run("review", gate="pre-submission")[0] == cr.EXIT_GATE_BLOCKED
    assert "AFTER its deadline" in capsys.readouterr().out


def test_gate_blocked_beyond_3h_notifies_once_and_queues_once(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")
    monkeypatch.setenv("STUB_SOL_RESET", "in 5 hours")
    assert env.run("review", gate="pre-submission")[0] == cr.EXIT_GATE_BLOCKED
    first = capsys.readouterr().out
    assert first.count("PUSH-NOTIFY-ANIKET") == 1
    assert "Wed Oct 28 16:00 ET" in first and "Waiting: review --head abc def" in first
    assert env.run("review", gate="pre-submission", name="again")[0] == cr.EXIT_GATE_BLOCKED
    assert "PUSH-NOTIFY-ANIKET" not in capsys.readouterr().out
    assert len(cr.read_jsonl(env.ops / "notify.jsonl")) == 1 and len(cr.queued_items()) == 1
    assert env.calls() == ["gpt-6-astra xhigh", "gpt-6.1-sol max"]


def test_stale_gate_flag_does_not_suppress_a_new_notification(env, monkeypatch, capsys):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")
    monkeypatch.setenv("STUB_SOL_RESET", "in 5 hours")
    env.run("review", gate="pre-submission")
    cr.resolve(cr.queued_items()[0]["id"], "dropped")              # resolved another way
    env.run("review", gate="pre-submission", name="later", argv=["--head", "x2", "def"])
    assert capsys.readouterr().out.count("PUSH-NOTIFY-ANIKET") == 2


def test_direct_success_supersedes_the_queued_copy(env, monkeypatch):
    monkeypatch.setenv("STUB_ASTRA", "limit")
    monkeypatch.setenv("STUB_SOL", "limit")
    assert env.run("ask", gate="eval-plan")[0] == cr.EXIT_GATE_BLOCKED
    assert len(cr.queued_items()) == 1
    monkeypatch.setenv("ETHO_NOW", "2026-10-28T18:00:00+00:00")  # after Sol's reset
    monkeypatch.setenv("STUB_SOL", "ok")
    assert env.run("ask", gate="eval-plan", name="direct")[0] == 0
    assert cr.queued_items() == []                                 # never replayed later
    done = json.loads(next((env.ops / "queue" / "done").glob("*.json")).read_text())
    assert done["superseded_by"] == done["id"]


def test_unparsed_reset_backs_off_one_hour(env, monkeypatch):
    monkeypatch.setenv("STUB_SOL", "limit")
    monkeypatch.setenv("STUB_SOL_RESET", "later")
    env.run("ask")
    lim = json.loads((env.ops / "limit.json").read_text())["gpt-6.1-sol"]
    assert lim["reset"] == "2026-10-28T16:00:00+00:00" and lim["reset_parsed"] is False


def test_parse_reset_formats():
    now = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    u = timezone.utc
    assert cr.parse_reset("try again in 2 days 3 hours", now) == datetime(2026, 10, 30, 18, 0, tzinfo=u)
    assert cr.parse_reset("try again in 1 week", now) == datetime(2026, 11, 4, 15, 0, tzinfo=u)
    assert cr.parse_reset("Try again at Nov 3rd, 2026 4:12 PM.", now) == datetime(2026, 11, 3, 16, 12, tzinfo=u)
    assert cr.parse_reset("try again at Nov 3 4:12 PM (UTC).", now) == datetime(2026, 11, 3, 16, 12, tzinfo=u)
    assert cr.parse_reset("limit resets at 16:00", now) == datetime(2026, 10, 28, 16, 0, tzinfo=u)
    assert cr.parse_reset('{"type":"usage_limit_reached","resets_in_seconds":7200}', now) == \
        datetime(2026, 10, 28, 17, 0, tzinfo=u)
    assert cr.parse_reset('"resets_at": 1793174400', now) == datetime.fromtimestamp(1793174400, u)
    assert cr.parse_reset("you hit a limit", now) is None


# ---- robustness -------------------------------------------------------------------------------
def test_corrupt_state_files_do_not_crash(env):
    env.ops.mkdir(parents=True, exist_ok=True)
    (env.ops / "limit.json").write_text("{torn")
    (env.ops / "queue").mkdir()
    (env.ops / "queue" / "bad.json").write_text("{")
    (env.ops / "usage.jsonl").write_text('{"ok": 1}\n{torn\n')
    assert env.run("ask")[0] == 0
    assert "Codex usage:" in cr.summary()


def test_missing_codex_binary_is_an_env_error(env, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    rc, out = env.run("ask")
    assert rc == 127 and "ENV_ERROR" in out.splitlines()[0]


def test_usage_parsing_edge_cases(env, monkeypatch):
    monkeypatch.setenv("STUB_SOL", "nested")
    rc, out = env.run("ask")
    assert rc == 0 and env.usage()[-1]["tok_input_tokens"] == 7 and "tok_details" not in env.usage()[-1]
    monkeypatch.setenv("STUB_SOL", "empty")
    rc, out = env.run("ask", name="e", argv=["e", "q"])
    assert rc == 1 and "no answer" in out.splitlines()[0]
    monkeypatch.setenv("STUB_SOL", "fail")
    rc, out = env.run("ask", name="f", argv=["f", "q"])
    assert rc == 1 and "stream disconnected" in out.splitlines()[0]


def test_summary_line(env, monkeypatch):
    env.run("review")
    monkeypatch.setenv("STUB_SOL", "limit")
    env.run("ask", name="q")
    s = cr.summary()
    assert s.startswith("Codex usage: routine today 1/4 (reviews 1/1)")  # the limited call used no allowance
    assert "gpt-6.1-sol until Wed Oct 28 13:05 ET" in s and "queue 1 (0 gates)" in s


# ---- the drain, end to end through the real wrappers ------------------------------------------
@pytest.fixture
def repo(env, tmp_path):
    r = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    (r / "bin").mkdir()
    for f in ("codex_run.py", "codex-ask", "codex-review", "codex-queue"):
        (r / "bin" / f).write_text((ROOT / "bin" / f).read_text())
        (r / "bin" / f).chmod(0o755)
    (r / "AGENTS.md").write_text("x\n")
    (r / "ctx.md").write_text("claim\n")

    def commit(path, text):
        (r / path).parent.mkdir(parents=True, exist_ok=True)
        (r / path).write_text(text)
        subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(r), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", path],
                       check=True)
        return subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    class R:
        path = r
        ops = r / "ops" / "codex"

        def sh(self, *args, **envkw):
            e = {**os.environ, "CODEX_OPS_DIR": str(self.ops), **envkw}
            return subprocess.run([str(r / "bin" / args[0]), *args[1:]], cwd=r, env=e, capture_output=True,
                                  text=True)
    R.commit = staticmethod(commit)
    R.base = commit("README", "r\n")
    (r / "ops" / "codex").mkdir(parents=True)
    return R()


def test_drain_runs_after_reset_and_never_before(repo, env):
    p = repo.sh("codex-ask", "ctx.md", "Is it right?", STUB_SOL="limit")
    assert p.returncode == 75 and "second opinion pending" in p.stdout
    n = len(env.calls())
    assert repo.sh("codex-queue", "--drain", STUB_SOL="limit").returncode == 0 and len(env.calls()) == n
    p = repo.sh("codex-queue", "--drain", ETHO_NOW="2026-10-28T18:00:00+00:00")
    assert p.returncode == 0, p.stdout + p.stderr
    assert list((repo.ops / "queue").glob("*.json")) == []
    done = json.loads(next((repo.ops / "queue" / "done").glob("*.json")).read_text())
    assert done["out"].startswith("ops/codex/ask-")
    assert (repo.path / done["out"]).read_text().startswith("codex model: gpt-6.1-sol | reasoning effort: xhigh")


def test_drain_coalesces_reviews_on_one_line(repo, env):
    h1 = repo.commit("eval/a.py", "a\n")
    assert repo.sh("codex-review", "--head", h1, repo.base, "focus A", STUB_SOL="limit").returncode == 75
    h2 = repo.commit("eval/b.py", "b\n")
    assert repo.sh("codex-review", "--head", h2, repo.base, "focus B").returncode == 75  # still limited
    assert len(list((repo.ops / "queue").glob("*.json"))) == 2
    p = repo.sh("codex-queue", "--drain", ETHO_NOW="2026-10-28T18:00:00+00:00")
    assert p.returncode == 0, p.stdout + p.stderr
    assert env.calls().count("gpt-6.1-sol high") == 2  # the limited first try + ONE merged review
    assert list((repo.ops / "queue").glob("*.json")) == []
    assert len(list((repo.ops / "queue" / "done").glob("*.json"))) == 2


def test_drain_continues_past_a_capped_item_and_drops_on_nov_1(repo, env):
    h1 = repo.commit("eval/a.py", "a\n")
    assert repo.sh("codex-review", "--head", h1, repo.base, STUB_SOL="limit").returncode == 75
    assert repo.sh("codex-ask", "ctx.md", "q").returncode == 75
    p = repo.sh("codex-queue", "--drain", ETHO_NOW="2026-11-01T06:00:00+00:00")  # Nov 1 ET
    assert p.returncode == 0
    assert list((repo.ops / "queue").glob("*.json")) == []
    assert len(list((repo.ops / "queue" / "dropped").glob("*.json"))) == 2


def test_drain_failure_backs_off_then_drops(repo, env):
    assert repo.sh("codex-ask", "ctx.md", "q", STUB_SOL="limit").returncode == 75
    times = ["2026-10-28T18:00:00+00:00", "2026-10-28T18:10:00+00:00", "2026-10-28T19:05:00+00:00",
             "2026-10-28T20:10:00+00:00"]
    for t in times:
        repo.sh("codex-queue", "--drain", ETHO_NOW=t, STUB_SOL="fail")
    # 18:00 attempt 1, 18:10 skipped (backoff), 19:05 attempt 2, 20:10 attempt 3 -> dropped as failed
    assert env.calls().count("gpt-6.1-sol xhigh") == 1 + 3
    assert list((repo.ops / "queue").glob("*.json")) == []
    assert len(list((repo.ops / "queue" / "failed").glob("*.json"))) == 1
