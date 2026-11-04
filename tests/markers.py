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


def bwrap_usable() -> tuple[bool, str]:
    """(usable, why): bwrap is on PATH and starts the interpreter under the sandbox layout (the app's cached probe)."""
    return _probe_bwrap()


def git_checkout() -> bool:
    """The repo is a git work tree with git on PATH (a ZIP download of the repo is not)."""
    if not shutil.which("git") or not (ROOT / ".git").exists():
        return False   # a ZIP unpacked inside some other git repo must not count as this repo's checkout
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == ROOT.resolve()


_BWRAP_OK, _BWRAP_WHY = bwrap_usable()
needs_bwrap = pytest.mark.skipif(
    not _BWRAP_OK,
    reason=f"needs a usable bubblewrap ({_BWRAP_WHY}): public mode checks uploads and runs detector code only under it "
           "and refuses live use without it (503); bubblewrap does not exist on macOS")
needs_git = pytest.mark.skipif(
    not git_checkout(),
    reason="needs a git checkout: reads git metadata (commit, status or file modes); a ZIP download has no .git")
