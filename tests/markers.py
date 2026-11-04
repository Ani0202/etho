"""Skip markers for machines without bubblewrap (e.g. macOS) or without a git checkout (e.g. a ZIP download).

Without bwrap, a public deployment refuses live use (503 sandbox_unavailable) by design: uploads are probed by ffprobe
and detector code runs only under bubblewrap there. Tests that drive a public-mode upload or the bwrap sandbox itself
therefore need a usable bwrap; on a machine without it they are skipped, not failed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from etho.rules.sandbox import _probe_bwrap

ROOT = Path(__file__).resolve().parents[1]


def bwrap_usable() -> bool:
    """bwrap is on PATH and starts the interpreter under the sandbox layout (the app's own probe, cached)."""
    return shutil.which("bwrap") is not None and _probe_bwrap()[0]


def git_checkout() -> bool:
    """The repo is a git work tree with git on PATH (a ZIP download of the repo is not)."""
    if not shutil.which("git"):
        return False
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


needs_bwrap = pytest.mark.skipif(
    not bwrap_usable(),
    reason="needs a usable bubblewrap (bwrap): public mode checks uploads and runs detector code only under it and "
           "refuses live use without it (503). Install it with `apt install bubblewrap`; it does not exist on macOS")
needs_git = pytest.mark.skipif(
    not git_checkout(),
    reason="needs a git checkout: reads git metadata (commit, status or file modes); a ZIP download has no .git")
