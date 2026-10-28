"""Run model-written detector code safely.

The code may only define functions and use numpy (as `np`), math, and the helpers below. It is
checked statically (no imports, no dunder access, no I/O or introspection builtins) and then
executed in a separate `python -I` process with CPU, memory, and wall-clock limits.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
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
out = ns["detect"](data, fps)
out = np.asarray(out)
np.save(sys.argv[4], out)
'''


class SandboxError(RuntimeError):
    pass


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
        if isinstance(node, ast.FunctionDef) and node.name == "detect":
            has_detect = True
    if not has_detect:
        raise SandboxError("code must define detect(t, fps)")


def run_detector(code: str, table: dict[str, np.ndarray], fps: float, cpu_s: int = 30,
                 timeout_s: float = 60.0) -> np.ndarray:
    """Execute `detect(t, fps)` in a sandboxed subprocess; returns a bool array of len n."""
    check_source(code)
    n = len(next(iter(table.values())))
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        np.savez(d / "t.npz", __fps__=np.float64(fps), **table)
        (d / "code.py").write_text(HELPERS + "\n" + code)
        (d / "runner.py").write_text(RUNNER)
        try:
            p = subprocess.run([sys.executable, "-I", str(d / "runner.py"), str(d / "t.npz"), str(d / "code.py"),
                                str(cpu_s), str(d / "out.npy")], capture_output=True, text=True,
                               timeout=timeout_s, cwd=d, env={"PATH": "/usr/bin:/bin"})
        except subprocess.TimeoutExpired as e:
            raise SandboxError(f"detector timed out after {timeout_s}s") from e
        if p.returncode != 0:
            raise SandboxError("detector failed:\n" + p.stderr[-2000:])
        out = np.load(d / "out.npy", allow_pickle=False)
    if out.shape != (n,):
        raise SandboxError(f"detect() must return one value per sample: got shape {out.shape}, need ({n},)")
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


__all__ = ["run_detector", "check_source", "describe", "SandboxError", "HELPERS", json.__name__]
