"""Run model-written detector code safely.

The code may only define functions and use numpy (as `np`), math, and the helpers below. It is
checked statically (no imports, no dunder access, no I/O or introspection builtins) and then
executed in a separate `python -I` process with CPU, memory, and wall-clock limits. When bubblewrap
(`bwrap`) works, the child additionally runs in fresh namespaces with no network, a minimal read-only
filesystem (interpreter, libraries, numpy), a private /tmp, and a cleared environment; see `isolation_mode()`.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import numpy as np

FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
                   "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview",
                   "object", "type", "super", "classmethod", "staticmethod", "property", "os", "sys",
                   "subprocess", "importlib", "builtins"}
FORBIDDEN_NP_ATTRS = {"load", "save", "savez", "savez_compressed", "loadtxt", "savetxt", "genfromtxt",
                      "fromfile", "tofile", "memmap", "lib", "ctypeslib", "distutils", "f2py", "testing"}

HELPERS = '''
import math
import numpy as np

def smooth(x, k):
    """Centered moving average over k samples, NaN-aware."""
    x = np.asarray(x, float)
    if k <= 1:
        return x
    v = np.where(np.isfinite(x), x, 0.0); c = np.isfinite(x).astype(float); ker = np.ones(int(k))
    num = np.convolve(v, ker, mode="same"); den = np.convolve(c, ker, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)

def hysteresis(x, on, off):
    """True from where x >= on until x drops below off (NaN counts as below)."""
    x = np.nan_to_num(np.asarray(x, float), nan=-np.inf)
    out = np.zeros(len(x), bool); state = False
    for i, v in enumerate(x):
        state = v >= on if not state else v >= off
        out[i] = state
    return out

def bouts_clean(mask, fps, min_s=0.2, max_gap_s=0.3):
    """Fill gaps shorter than max_gap_s, then drop bouts shorter than min_s."""
    m = np.asarray(mask, bool).copy(); n = len(m)
    gap = int(round(max_gap_s * fps)); mn = int(round(min_s * fps))
    i = 0
    while i < n:
        if not m[i]:
            j = i
            while j < n and not m[j]:
                j += 1
            if 0 < i and j < n and (j - i) <= gap:
                m[i:j] = True
            i = j
        else:
            i += 1
    i = 0
    while i < n:
        if m[i]:
            j = i
            while j < n and m[j]:
                j += 1
            if (j - i) < mn:
                m[i:j] = False
            i = j
        else:
            i += 1
    return m
'''

RUNNER = r'''
import json, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (int(sys.argv[3]), int(sys.argv[3])))
resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
import numpy as np
data = dict(np.load(sys.argv[1]))
fps = float(data.pop("__fps__"))
src = open(sys.argv[2]).read()
ns = {}
exec(compile(src, "<detector>", "exec"), ns)
fn = ns.get("detect") or ns.get("classify")
out = fn(data, fps)
try:
    out = np.asarray(out, dtype=float) if np.asarray(out).dtype == object else np.asarray(out)
except (TypeError, ValueError) as e:
    raise TypeError(f"detect() must return a 1-D numpy bool array, got {type(out).__name__}: {e}")
if out.dtype == object or out.ndim != 1:
    raise TypeError(f"detect() must return a 1-D numpy bool array, got dtype {out.dtype} shape {out.shape}")
np.save(sys.argv[4], out)
'''


class SandboxError(RuntimeError):
    pass


_PATH_ENV = "/usr/bin:/bin"
_IO = "/io"  # mount point of the input/output files inside the bwrap child


def _py_roots() -> list[str]:
    """Directories the child interpreter needs beyond /usr: the venv/prefix and numpy's install dir."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sysconfig.get_paths()["purelib"],
             sysconfig.get_paths()["platlib"], str(Path(np.__file__).resolve().parent.parent)}
    out = []
    for r in sorted(roots, key=len):
        r = os.path.realpath(r)
        if r == "/usr" or r.startswith("/usr/") or any(r == o or r.startswith(o + "/") for o in out):
            continue
        out.append(r)
    return out


def _bwrap_argv(workdir: Path, outdir: Path) -> list[str]:
    """bwrap prefix: no network/pid/ipc/uts/cgroup, read-only minimal fs, private /tmp, empty environment.
    Deliberately NOT mounted: the home directory (except the venv if it lives there), /etc secrets, the repo's ops/."""
    a = [shutil.which("bwrap") or "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
         "--clearenv", "--setenv", "PATH", _PATH_ENV, "--ro-bind", "/usr", "/usr"]
    for top in ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32"):
        if os.path.islink(top):
            a += ["--symlink", os.readlink(top), top]
        elif os.path.isdir(top):
            a += ["--ro-bind", top, top]
    for f in ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d"):
        if os.path.exists(f):
            a += ["--ro-bind", f, f]
    for r in _py_roots():
        a += ["--ro-bind", r, r]
    a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", _IO,
          "--ro-bind", str(workdir / "t.npz"), f"{_IO}/t.npz", "--ro-bind", str(workdir / "code.py"), f"{_IO}/code.py",
          "--ro-bind", str(workdir / "runner.py"), f"{_IO}/runner.py", "--bind", str(outdir), f"{_IO}/out",
          "--chdir", f"{_IO}/out"]
    return a


@functools.lru_cache(maxsize=1)
def _probe_bwrap() -> tuple[bool, str]:
    """One-time self-probe (cached per process): can bwrap start the interpreter with numpy under our layout?"""
    if not shutil.which("bwrap"):
        return False, "bwrap not found"
    try:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ("t.npz", "code.py", "runner.py"):
                (d / n).write_text("")
            (d / "out").mkdir()
            p = subprocess.run(_bwrap_argv(d, d / "out") + [sys.executable, "-I", "-c", "import numpy; print(1)"],
                               capture_output=True, text=True, timeout=20)
        if p.returncode == 0 and p.stdout.strip() == "1":
            return True, "ok"
        return False, f"probe failed: {p.stderr.strip()[-300:]}"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"probe error: {e}"


def isolation_mode() -> str:
    """"bwrap" if children run under bubblewrap, else "rlimit-only". ETHO_SANDBOX=bwrap|rlimit forces a mode
    (forcing bwrap when it is unavailable raises SandboxError)."""
    want = os.environ.get("ETHO_SANDBOX", "").strip().lower()
    if want == "rlimit":
        return "rlimit-only"
    ok, why = _probe_bwrap()
    if want == "bwrap" and not ok:
        raise SandboxError(f"ETHO_SANDBOX=bwrap but bubblewrap is unusable: {why}")
    return "bwrap" if ok else "rlimit-only"


def check_source(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxError(f"syntax error: {e}") from e
    has_detect = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise SandboxError("imports are not allowed (np, math and the helpers are provided)")
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.ClassDef, ast.AsyncFunctionDef, ast.Await,
                             ast.With, ast.AsyncWith, ast.Try, ast.Raise, ast.Lambda)) and \
                not isinstance(node, ast.Lambda):
            raise SandboxError(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            raise SandboxError(f"name {node.id!r} is not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise SandboxError(f"attribute {node.attr!r} is not allowed")
            if node.attr in FORBIDDEN_NP_ATTRS:
                raise SandboxError(f"np.{node.attr} is not allowed")
        if isinstance(node, ast.FunctionDef) and node.name in ("detect", "classify"):
            has_detect = True
    if not has_detect:
        raise SandboxError("code must define detect(t, fps) (or classify(t, fps) for a joint classifier)")


def run_detector(code: str, table: dict[str, np.ndarray], fps: float, cpu_s: int = 30,
                 timeout_s: float = 60.0, labels: int | None = None) -> np.ndarray:
    """Execute `detect(t, fps)` in a sandboxed subprocess; returns a bool array of len n.
    With `labels=K`, the code is a joint `classify(t, fps)` returning ints in 0..K-1 (one label per sample)."""
    check_source(code)
    return _finish(_run_unchecked(code, table, fps, cpu_s, timeout_s), len(next(iter(table.values()))), labels)


def _run_unchecked(code: str, table: dict[str, np.ndarray], fps: float, cpu_s: int = 30,
                   timeout_s: float = 60.0, mode: str | None = None) -> np.ndarray:
    """Run code in the child WITHOUT the AST check (used by tests to probe OS-level isolation).
    `mode` overrides isolation_mode(): "bwrap" or "rlimit-only"."""
    mode = mode or isolation_mode()
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        np.savez(d / "t.npz", __fps__=np.float64(fps), **table)
        (d / "code.py").write_text(HELPERS + "\n" + code)
        (d / "runner.py").write_text(RUNNER)
        (d / "out").mkdir()
        if mode == "bwrap":
            cmd = _bwrap_argv(d, d / "out") + [sys.executable, "-I", f"{_IO}/runner.py", f"{_IO}/t.npz",
                                               f"{_IO}/code.py", str(cpu_s), f"{_IO}/out/out.npy"]
        else:
            cmd = [sys.executable, "-I", str(d / "runner.py"), str(d / "t.npz"), str(d / "code.py"),
                   str(cpu_s), str(d / "out" / "out.npy")]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=d,
                               env={"PATH": _PATH_ENV})
        except subprocess.TimeoutExpired as e:
            raise SandboxError(f"detector timed out after {timeout_s}s") from e
        if p.returncode != 0:
            raise SandboxError("detector failed:\n" + p.stderr[-2000:])
        try:
            return np.load(d / "out" / "out.npy", allow_pickle=False)
        except (OSError, ValueError) as e:
            raise SandboxError(f"detect() output unreadable: {e}") from e


def _finish(out: np.ndarray, n: int, labels: int | None) -> np.ndarray:
    if out.shape != (n,):
        raise SandboxError(f"detect() must return one value per sample: got shape {out.shape}, need ({n},)")
    if labels is not None:
        if not np.issubdtype(out.dtype, np.number):
            raise SandboxError(f"classify() returned dtype {out.dtype}")
        v = np.nan_to_num(out.astype(float), nan=labels - 1)
        if not np.all((v >= 0) & (v <= labels - 1) & (v == np.round(v))):
            raise SandboxError(f"classify() must return integer labels in 0..{labels - 1}")
        return v.astype(np.int8)
    if out.dtype != bool:
        if not np.issubdtype(out.dtype, np.number):
            raise SandboxError(f"detect() returned dtype {out.dtype}")
        out = np.nan_to_num(out.astype(float)) > 0.5
    return out


def describe(table: dict[str, np.ndarray]) -> str:
    """Label-free feature statistics for the prompt (percentiles over finite values)."""
    lines = []
    for k, v in table.items():
        v = np.asarray(v, float)
        f = v[np.isfinite(v)]
        if len(f) == 0:
            lines.append(f"{k}: all NaN")
            continue
        q = np.percentile(f, [5, 25, 50, 75, 95])
        lines.append(f"{k}: finite {len(f) / len(v):.0%}; p5 {q[0]:.3g}, p25 {q[1]:.3g}, median {q[2]:.3g}, "
                     f"p75 {q[3]:.3g}, p95 {q[4]:.3g}")
    return "\n".join(lines)


__all__ = ["run_detector", "isolation_mode", "check_source", "describe", "SandboxError", "HELPERS", json.__name__]
