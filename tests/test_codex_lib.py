"""bin/codex-lib.sh against a stub `codex` binary (no network, no real Codex)."""
import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "bin" / "codex-lib.sh"

STUB = r"""#!/usr/bin/env bash
# args: exec --sandbox S -m MODEL -c model_reasoning_effort=E PROMPT
model=$5; effort=${7#model_reasoning_effort=}
case "$model" in
  noheader) exit 42 ;;
esac
printf 'OpenAI Codex\n--------\nmodel: %s\nreasoning effort: %s\n--------\nuser\n%s\n' "$model" "$effort" "$8" >&2
case "$model" in
  bad) echo "ERROR: The 'bad' model is not supported when using Codex with a ChatGPT account." >&2; exit 1 ;;
  flaky) echo "ERROR: stream disconnected before completion" >&2; exit 1 ;;
esac
echo "answer from $model"
"""


@pytest.fixture
def run(tmp_path):
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    (bindir / "codex").write_text(STUB)
    (bindir / "codex").chmod(0o755)

    def _run(default, fallback, prompt="hi", env=None):
        out, log = tmp_path / "out.md", tmp_path / "out.log"
        script = (f'set -euo pipefail; source "{LIB}"; rc=0; '
                  f'codex_run {default} {fallback} xhigh read-only "{out}" "{log}" "{prompt}" || rc=$?; exit $rc')
        e = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **(env or {})}
        e.pop("CODEX_MODEL", None) if not (env and "CODEX_MODEL" in env) else None
        e.pop("CODEX_EFFORT", None) if not (env and "CODEX_EFFORT" in env) else None
        p = subprocess.run(["bash", "-c", script], env=e, capture_output=True, text=True)
        return p.returncode, out.read_text() if out.exists() else None
    return _run


def test_primary_ok_first_line_records_model(run):
    rc, out = run("good", "fallback")
    assert rc == 0
    assert out.splitlines()[0] == "codex model: good | reasoning effort: xhigh"
    assert "answer from good" in out


def test_model_error_falls_back_once(run):
    rc, out = run("bad", "good")
    assert rc == 0
    assert out.splitlines()[0].startswith("codex model: good | reasoning effort: xhigh (fallback: bad")


def test_model_error_words_in_prompt_do_not_trigger_fallback(run):
    rc, out = run("flaky", "good", prompt="the log says model_not_found and unknown model")
    assert rc == 1
    first = out.splitlines()[0]
    assert first.startswith("codex model: flaky") and "FAILED exit 1" in first


def test_missing_header_still_writes_report_and_keeps_exit_code(run):
    rc, out = run("noheader", "-")
    assert rc == 42
    assert "FAILED exit 42" in out.splitlines()[0]


def test_env_overrides_and_disable_fallback(run):
    rc, out = run("good", "other", env={"CODEX_MODEL": "bad", "CODEX_EFFORT": "max"})
    assert rc == 1  # explicit model: no fallback
    assert out.splitlines()[0].startswith("codex model: bad | reasoning effort: max")


def test_ultra_is_refused(run):
    rc, out = run("good", "-", env={"CODEX_EFFORT": "ultra"})
    assert rc == 2 and out is None
