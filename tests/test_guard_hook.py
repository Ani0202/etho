import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard-bash.py"


def run(cmd: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True).returncode


@pytest.mark.parametrize("cmd", [
    "curl https://api.meta.ai/v1/chat/completions -d @x.json",
    "wget -qO- https://inference.baseten.co/v1/models",
    "python -c \"import httpx; httpx.get('https://api.fireworks.ai/inference/v1/models')\"",
    "ls && curl -s https://api.together.xyz/v1/models",
    "git push --force origin main",
    "git push origin main --force",
    "git -C . push -f",
    "git push origin +main",
    "git push --force-with-lease",
    "cat ~/.config/etho/secrets.env",
    "cat ~/.codex/auth.json",
])
def test_blocked(cmd):
    assert run(cmd) == 2


@pytest.mark.parametrize("cmd", [
    "grep -rn api.meta.ai etho/",
    "git log -S api.fireworks.ai",
    "git push origin main",
    "git push -u origin feature/x",
    "uv run pytest",
    "bin/muse --effort low --tag dev hello",
])
def test_allowed(cmd):
    assert run(cmd) == 0
