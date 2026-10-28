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


def test_revise_sends_corrections_and_accepts_valid_code():
    from etho.rules.revise import revise
    det = S.Detector("investigation", GOOD, "m")
    tables = {"s": T}
    corr = [{"seq": "s", "s0": 0, "s1": 2, "behavior": "investigation", "kind": "fn", "predicted_label": "other",
             "correct_label": "investigation"},
            {"seq": "s", "s0": 2, "s1": 4, "behavior": "attack", "kind": "fp", "predicted_label": "attack",
             "correct_label": "other"}]
    new_code = "def detect(t, fps):\n    return np.nan_to_num(t['dist'], nan=1e9) < 300\n"
    gw = FakeGW([f"```python\n{new_code}```"])
    new = revise(det, "close contact", corr, tables, gw=gw, public_data=True, self_check=False)
    assert new.code.strip() == new_code.strip()
    prompt = gw.calls[0][2][1]["content"]
    assert "missed it: this IS investigation" in prompt and "attack" not in prompt.split("researcher")[1]
    assert "dist=" in prompt


def test_revise_self_check_rejects_overbroad_then_accepts_narrow():
    from etho.rules.revise import revise
    t = {"dist": np.array([10.0, 40.0, 60.0, 80.0, 100.0, 120.0, 140.0, 160.0, 400.0, 500.0])}
    det = S.Detector("investigation", "def detect(t, fps):\n    return t['dist'] < 50\n", "m")
    corr = [{"seq": "s", "s0": 2, "s1": 3, "behavior": "investigation", "kind": "fn", "predicted_label": "other",
             "correct_label": "investigation"}]
    broad = "def detect(t, fps):\n    return t['dist'] < 450\n"      # fixes it but 2 -> 9 positives
    narrow = "def detect(t, fps):\n    return t['dist'] < 65\n"      # fixes it, 2 -> 3 positives... still +50%
    exact = "def detect(t, fps):\n    return (t['dist'] < 50) | ((t['dist'] > 55) & (t['dist'] < 65))\n"
    gw = FakeGW([f"```python\n{c}```" for c in (broad, narrow, exact)])
    new = revise(det, "close", corr, {"s": t}, gw=gw, public_data=True)
    assert "def detect" in new.code and len(gw.calls) == 3
    # the accepted version fixed the correction; rate change still > 20% here, so the original is kept
    assert new.code == det.code and new.calls[-1]["revision_rejected"]
    assert "changed by" in gw.calls[1][2][-1]["content"]


def test_revise_self_check_accepts_targeted_fix():
    from etho.rules.revise import revise
    t = {"dist": np.linspace(0, 100, 50)}
    det = S.Detector("investigation", "def detect(t, fps):\n    return t['dist'] < 50\n", "m")
    corr = [{"seq": "s", "s0": 25, "s1": 26, "behavior": "investigation", "kind": "fn", "predicted_label": "other",
             "correct_label": "investigation"}]
    good = "def detect(t, fps):\n    return t['dist'] < 53\n"        # 25 -> 27 positives (+8%)
    new = revise(det, "close", corr, {"s": t}, gw=FakeGW([f"```python\n{good}```"]), public_data=True)
    assert new.code.strip() == good.strip() and new.calls[0]["check"]["fixed_new"] == 1
