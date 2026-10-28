"""OS-level (bubblewrap) isolation of the rule sandbox. No network; skipped when bwrap is unusable."""
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

from etho.rules import sandbox as sb

pytestmark = pytest.mark.skipif(not sb._probe_bwrap()[0], reason="bwrap unavailable")

TABLE = {"a": np.linspace(0, 1, 300), "b": np.sin(np.linspace(0, 20, 300))}


def _run(code, mode="bwrap"):
    return sb._run_unchecked(code, TABLE, 10.0, mode=mode)


def test_mode_reported(monkeypatch):
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    assert sb.isolation_mode() == "bwrap"


def test_results_identical_to_rlimit_path(monkeypatch):
    code = "def detect(t, fps):\n    x = smooth(t['b'], 5)\n    return bouts_clean(hysteresis(x, 0.3, 0.1), fps)\n"
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    a = sb.run_detector(code, TABLE, 10.0)
    monkeypatch.setenv("ETHO_SANDBOX", "rlimit")
    assert sb.isolation_mode() == "rlimit-only"
    b = sb.run_detector(code, TABLE, 10.0)
    assert a.dtype == b.dtype and np.array_equal(a, b) and a.any()


def test_cannot_read_home_file():
    with tempfile.NamedTemporaryFile("w", dir=Path.home(), delete=False, suffix=".secret") as f:
        f.write("TOPSECRET")
    try:
        code = f"def detect(t, fps):\n    s = open({f.name!r}).read()\n    return np.ones(len(t['a']), bool)\n"
        assert _run(code, "rlimit-only").all()  # control: without bwrap the file is readable
        with pytest.raises(sb.SandboxError, match="FileNotFoundError|No such file"):
            _run(code)
    finally:
        os.unlink(f.name)


def test_cannot_see_sensitive_dirs():
    repo = Path(sb.__file__).resolve().parents[2]
    for p in (Path.home() / ".config" / "etho", Path.home() / ".codex", Path.home() / ".ssh", repo / "ops",
              repo / "data"):
        code = ("def detect(t, fps):\n    import os\n"
                f"    return np.full(len(t['a']), os.path.exists({str(p)!r}))\n")
        assert not _run(code).any(), p


def test_no_network():
    code = ("def detect(t, fps):\n    import socket\n    s = socket.socket()\n    s.settimeout(2)\n"
            "    s.connect(('1.1.1.1', 80))\n    return np.ones(len(t['a']), bool)\n")
    with pytest.raises(sb.SandboxError, match="OSError|unreachable|denied|Errno"):
        _run(code)
    code2 = ("def detect(t, fps):\n    import os\n"
             "    import socket\n    return np.full(len(t['a']), [n for _, n in socket.if_nameindex()] == ['lo'])\n")
    assert _run(code2).all()


def test_cannot_write_outside_output():
    for target in ("/usr/x_etho_test", "/io/code.py", "/io/t.npz"):
        code = f"def detect(t, fps):\n    open({target!r}, 'w').write('x')\n    return np.ones(len(t['a']), bool)\n"
        with pytest.raises(sb.SandboxError, match="Error"):
            _run(code)
    name = "etho_sandbox_probe_%d" % os.getpid()  # private tmpfs /tmp: writable but not shared with the host
    code = f"def detect(t, fps):\n    open('/tmp/{name}', 'w').write('x')\n    return np.ones(len(t['a']), bool)\n"
    assert _run(code).all()
    assert not Path("/tmp", name).exists()


def test_env_cleared(monkeypatch):
    monkeypatch.setenv("ETHO_FAKE_SECRET", "hunter2")
    code = ("def detect(t, fps):\n    import os\n"
            "    return np.full(len(t['a']), 'ETHO_FAKE_SECRET' in os.environ or 'HOME' in os.environ)\n")
    assert not _run(code).any()


def test_force_bwrap_unavailable_raises(monkeypatch):
    monkeypatch.setenv("ETHO_SANDBOX", "bwrap")
    monkeypatch.setattr(sb, "_probe_bwrap", lambda: (False, "mock"))
    with pytest.raises(sb.SandboxError, match="ETHO_SANDBOX=bwrap"):
        sb.isolation_mode()


def test_fallback_when_probe_fails(monkeypatch):
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "_probe_bwrap", lambda: (False, "mock"))
    assert sb.isolation_mode() == "rlimit-only"
    out = sb.run_detector("def detect(t, fps):\n    return t['a'] > 0.5\n", TABLE, 10.0)
    assert np.array_equal(out, TABLE["a"] > 0.5)


def test_error_types_preserved(monkeypatch):
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    with pytest.raises(sb.SandboxError, match="detector failed"):
        sb.run_detector("def detect(t, fps):\n    return 1/0\n", TABLE, 10.0)
    with pytest.raises(sb.SandboxError, match="one value per sample"):
        sb.run_detector("def detect(t, fps):\n    return np.ones(3, bool)\n", TABLE, 10.0)
    with pytest.raises(sb.SandboxError, match="timed out"):
        sb.run_detector("def detect(t, fps):\n    while True:\n        pass\n", TABLE, 10.0, timeout_s=1.5)
