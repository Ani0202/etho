"""OS-level (bubblewrap) isolation of the rule sandbox. No network; skipped when bwrap is unusable."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from etho.rules import sandbox as sb

needs_bwrap = pytest.mark.skipif(not sb._probe_bwrap()[0], reason="bwrap unavailable")

TABLE = {"a": np.linspace(0, 1, 300), "b": np.sin(np.linspace(0, 20, 300))}


def _run(code, mode="bwrap"):
    return sb._run_unchecked(code, TABLE, 10.0, mode=mode)


@needs_bwrap
def test_mode_reported(monkeypatch):
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    assert sb.isolation_mode() == "bwrap"


@needs_bwrap
def test_results_identical_to_rlimit_path(monkeypatch):
    code = "def detect(t, fps):\n    x = smooth(t['b'], 5)\n    return bouts_clean(hysteresis(x, 0.3, 0.1), fps)\n"
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    a = sb.run_detector(code, TABLE, 10.0)
    monkeypatch.setenv("ETHO_SANDBOX", "rlimit")
    assert sb.isolation_mode() == "rlimit-only"
    b = sb.run_detector(code, TABLE, 10.0)
    assert a.dtype == b.dtype and np.array_equal(a, b) and a.any()


@needs_bwrap
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


@needs_bwrap
def test_cannot_see_sensitive_dirs():
    repo = Path(sb.__file__).resolve().parents[2]
    made = []
    try:
        for base in (Path.home(), repo):
            d = Path(tempfile.mkdtemp(dir=base, prefix=".etho_iso_test_"))
            made.append(d)
            (d / "marker.txt").write_text("secret")
        for d in made:
            code = ("def detect(t, fps):\n    import os\n"
                    f"    return np.full(len(t['a']), os.path.exists({str(d / 'marker.txt')!r}))\n")
            assert _run(code, "rlimit-only").all(), d   # control: visible without bwrap
            assert not _run(code).any(), d
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)


@needs_bwrap
def test_no_network():
    code = ("def detect(t, fps):\n    import socket\n    s = socket.socket()\n    s.settimeout(2)\n"
            "    s.connect(('1.1.1.1', 80))\n    return np.ones(len(t['a']), bool)\n")
    with pytest.raises(sb.SandboxError, match="OSError|unreachable|denied|Errno"):
        _run(code)
    ifaces = ("def detect(t, fps):\n    import socket\n"
              "    return np.full(len(t['a']), [n for _, n in socket.if_nameindex()] == ['lo'])\n")
    assert _run(ifaces).all()


@needs_bwrap
def test_cannot_write_outside_output():
    for target in ("/usr/x_etho_test", "/io/code.py", "/io/t.npz"):
        code = f"def detect(t, fps):\n    open({target!r}, 'w').write('x')\n    return np.ones(len(t['a']), bool)\n"
        with pytest.raises(sb.SandboxError, match="Error"):
            _run(code)
    name = "etho_sandbox_probe_%d" % os.getpid()  # private tmpfs /tmp: writable but not shared with the host
    code = f"def detect(t, fps):\n    open('/tmp/{name}', 'w').write('x')\n    return np.ones(len(t['a']), bool)\n"
    assert _run(code).all()
    assert not Path("/tmp", name).exists()


@needs_bwrap
def test_env_cleared(monkeypatch):
    monkeypatch.setenv("ETHO_FAKE_SECRET", "hunter2")
    code = ("def detect(t, fps):\n    import os\n"
            "    return np.full(len(t['a']), 'ETHO_FAKE_SECRET' in os.environ or 'HOME' in os.environ)\n")
    assert not _run(code).any()


def test_force_bwrap_unavailable_raises(monkeypatch):
    monkeypatch.setenv("ETHO_SANDBOX", "bwrap")
    monkeypatch.setattr(sb, "_probe_bwrap", lambda: (False, "mock"))
    with pytest.raises(sb.SandboxUnavailable, match="ETHO_SANDBOX=bwrap"):
        sb.isolation_mode()


def test_fallback_when_probe_fails(monkeypatch):
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "_probe_bwrap", lambda: (False, "mock"))
    assert sb.isolation_mode() == "rlimit-only"
    out = sb.run_detector("def detect(t, fps):\n    return t['a'] > 0.5\n", TABLE, 10.0)
    assert np.array_equal(out, TABLE["a"] > 0.5)


@needs_bwrap
def test_error_types_preserved(monkeypatch):
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    with pytest.raises(sb.SandboxError, match="detector failed"):
        sb.run_detector("def detect(t, fps):\n    return 1/0\n", TABLE, 10.0)
    with pytest.raises(sb.SandboxError, match="one value per sample"):
        sb.run_detector("def detect(t, fps):\n    return np.ones(3, bool)\n", TABLE, 10.0)
    with pytest.raises(sb.SandboxError, match="timed out"):
        sb.run_detector("def detect(t, fps):\n    while True:\n        pass\n", TABLE, 10.0, timeout_s=1.5)


# ---- tests that do not need a working bwrap -------------------------------------------------------
def test_invalid_env_value_raises(monkeypatch):
    monkeypatch.setenv("ETHO_SANDBOX", "yes-please")
    with pytest.raises(sb.SandboxUnavailable):
        sb.isolation_mode()
    assert not issubclass(sb.SandboxUnavailable, sb.SandboxError)


def test_probe_caches_success_only(monkeypatch):
    calls = []
    monkeypatch.setattr(sb, "_probe_state", {"ok": None, "why": "", "at": 0.0, "logged": None})
    results = iter([(False, "transient"), (True, "ok"), (False, "never reached")])
    monkeypatch.setattr(sb, "_probe_once", lambda: calls.append(1) or next(results))
    t = [1000.0]
    monkeypatch.setattr(sb.time, "monotonic", lambda: t[0])
    assert sb._probe_bwrap() == (False, "transient")
    assert sb._probe_bwrap() == (False, "transient") and len(calls) == 1   # failure cached briefly
    t[0] += sb._REPROBE_S + 1
    assert sb._probe_bwrap() == (True, "ok") and len(calls) == 2           # re-probed after the window
    t[0] += 10 * sb._REPROBE_S
    assert sb._probe_bwrap() == (True, "ok") and len(calls) == 2           # success cached for good


def test_unsafe_roots_refused(monkeypatch):
    monkeypatch.setattr(sb.sys, "prefix", "/")
    with pytest.raises(sb._UnsafeRoots):
        sb._py_roots()
    monkeypatch.setattr(sb.sys, "prefix", str(Path.home().parent))
    with pytest.raises(sb._UnsafeRoots):
        sb._py_roots()


def test_unsafe_roots_fallback_and_forced(monkeypatch):
    monkeypatch.setattr(sb, "_probe_state", {"ok": None, "why": "", "at": 0.0, "logged": None})
    monkeypatch.setattr(sb.sys, "prefix", "/")
    monkeypatch.delenv("ETHO_SANDBOX", raising=False)
    assert sb.isolation_mode() == "rlimit-only"
    assert "refusing" in sb.isolation_status()["detail"]
    monkeypatch.setenv("ETHO_SANDBOX", "bwrap")
    with pytest.raises(sb.SandboxUnavailable):
        sb.isolation_mode()


def test_symlink_output_rejected(tmp_path):
    secret = tmp_path / "secret.npy"
    np.save(secret, np.ones(4, bool))
    link = tmp_path / "out.npy"
    link.symlink_to(secret)
    with pytest.raises(sb.SandboxError, match="unreadable"):
        sb._read_output(link)
    fifo = tmp_path / "fifo.npy"
    os.mkfifo(fifo)
    with pytest.raises(sb.SandboxError, match="unreadable"):
        sb._read_output(fifo)
    ok = tmp_path / "ok.npy"
    np.save(ok, np.ones(4, bool))
    assert sb._read_output(ok).all()


def test_oversize_output_rejected(tmp_path, monkeypatch):
    f = tmp_path / "big.npy"
    np.save(f, np.ones(1000, bool))
    monkeypatch.setattr(sb, "_MAX_OUT_BYTES", 100)
    with pytest.raises(sb.SandboxError, match="unreadable"):
        sb._read_output(f)


@needs_bwrap
def test_child_symlink_output_end_to_end():
    code = ("def detect(t, fps):\n    import os\n    return np.ones(len(t['a']), bool)\n")
    # the runner writes out.npy itself; emulate a hostile child by pre-creating the symlink via the detector
    evil = ("def detect(t, fps):\n    import os\n    os.symlink('/etc/passwd', '/io/out/out.npy')\n"
            "    return np.ones(len(t['a']), bool)\n")
    assert _run(code).all()
    with pytest.raises(sb.SandboxError):   # np.save onto an existing symlink to a read-only file fails or is rejected
        _run(evil)


@needs_bwrap
def test_bwrap_startup_failure_is_unavailable(monkeypatch):
    real = sb._bwrap_argv
    monkeypatch.setattr(sb, "_bwrap_argv", lambda w, o: real(w, o) + ["--bogus-flag"])
    with pytest.raises(sb.SandboxUnavailable):
        _run("def detect(t, fps):\n    return np.ones(len(t['a']), bool)\n")
    assert not issubclass(sb.SandboxUnavailable, sb.SandboxError)


@needs_bwrap
def test_no_leftover_processes_after_timeout():
    marker = 987654
    with pytest.raises(sb.SandboxError, match="timed out"):
        sb._run_unchecked("def detect(t, fps):\n    while True:\n        pass\n", TABLE, 10.0, cpu_s=marker,
                          timeout_s=1.5, mode="bwrap")
    import time
    time.sleep(0.5)
    r = subprocess.run(["pgrep", "-f", f"runner.py .*{marker}"], capture_output=True, text=True)
    assert r.stdout.strip() == ""


def test_app_job_fails_on_unavailable_sandbox(monkeypatch):
    from types import SimpleNamespace

    from etho.app.backend import Backend
    monkeypatch.setenv("ETHO_SANDBOX", "bogus")
    state = {"names": ["a"], "defs": {"a": "x"}, "video": {}, "input": {"subject": "s", "partner": "p"}, "table": TABLE}
    ctx = SimpleNamespace(state=state, public_data=True, say=lambda *_: None, warnings=[])
    be = Backend.__new__(Backend)
    with pytest.raises(sb.SandboxUnavailable):
        be.stage_synthesize(ctx)
