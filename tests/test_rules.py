import numpy as np
import pytest

from etho.rules import synthesize as S
from etho.rules.sandbox import SandboxError, check_source, run_detector

T = {"dist": np.array([10.0, 200.0, 15.0, np.nan]), "r_present": np.ones(4)}
GOOD = "def detect(t, fps):\n    return np.nan_to_num(t['dist'], nan=1e9) < 50\n"


def test_runs_good_detector():
    assert run_detector(GOOD, T, 10.0).tolist() == [True, False, True, False]


@pytest.mark.parametrize("code", [
    "import os\ndef detect(t, fps):\n    return t['dist'] < 1",
    "def detect(t, fps):\n    open('/etc/passwd')\n    return t['dist'] < 1",
    "def detect(t, fps):\n    return t.__class__",
    "def detect(t, fps):\n    np.save('x', t['dist'])\n    return t['dist'] < 1",
    "def detect(t, fps):\n    return getattr(np, 'load')('x')",
    "x = 1\n",
])
def test_rejects_unsafe_or_incomplete_code(code):
    with pytest.raises(SandboxError):
        check_source(code)


def test_wrong_shape_and_runtime_errors_are_reported():
    with pytest.raises(SandboxError, match="one value per sample"):
        run_detector("def detect(t, fps):\n    return np.zeros(2, bool)\n", T, 10.0)
    with pytest.raises(SandboxError, match="KeyError"):
        run_detector("def detect(t, fps):\n    return t['nope'] > 0\n", T, 10.0)


def test_infinite_loop_is_killed():
    with pytest.raises(SandboxError):
        run_detector("def detect(t, fps):\n    while True:\n        pass\n", T, 10.0, cpu_s=1, timeout_s=10)


def test_helpers_available():
    code = ("def detect(t, fps):\n"
            "    return bouts_clean(hysteresis(-np.nan_to_num(t['dist'], nan=1e9), -20, -60), fps, 0.0, 0.0)\n")
    assert run_detector(code, T, 10.0).tolist() == [True, False, True, False]


class FakeGW:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def chat(self, provider, model, msgs, **kw):
        self.calls.append((model, kw, msgs))

        class R:
            text = self.answers.pop(0)
            cost_usd, usage, cache_hit, latency_s = 0.01, {}, False, 0.1
        return R()


def test_synthesize_repairs_once_then_succeeds():
    gw = FakeGW(["```python\ndef detect(t, fps):\n    return t['missing'] > 0\n```", f"```python\n{GOOD}```"])
    det = S.synthesize("investigation", "close contact", subject="r", partner="i", tables=[T], fps=10.0,
                       frame_wh=(1024, 570), gw=gw, public_data=True)
    assert det.code.strip() == GOOD.strip() and len(det.calls) == 2
    model, kw, msgs = gw.calls[1]
    assert kw["effort"] == "high" and "KeyError" in msgs[-1]["content"]
    assert "label" not in msgs[1]["content"].lower().replace("label-free", "")  # no labels in the prompt


def test_synthesize_gives_up_after_max_repairs():
    bad = "```python\ndef detect(t, fps):\n    return t['missing'] > 0\n```"
    with pytest.raises(SandboxError, match="still failing"):
        S.synthesize("attack", "x", subject="r", partner="i", tables=[T], fps=10.0, frame_wh=(1, 1),
                     gw=FakeGW([bad] * 3), public_data=True)


def test_object_or_ragged_output_is_a_sandbox_error():
    with pytest.raises(SandboxError, match="bool array"):
        run_detector("def detect(t, fps):\n    return [np.zeros(2), 1]\n", T, 10.0)
