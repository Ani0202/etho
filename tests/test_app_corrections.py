import numpy as np
import pytest

from etho.app import corrections as C

NAMES = ["attack", "investigation", "mount"]


def val(payload, **kw):
    return C.validate(payload, names=NAMES, duration_s=60.0, fps=10.0, max_total=5, **kw)


def good(**o):
    return {"kind": "wrong", "behavior": "attack", "start_s": 1.0, "end_s": 2.0, "correct_label": "none", **o}


def test_valid_payload_roundtrip():
    cs = val({"corrections": [good(), {"kind": "missed", "behavior": "mount", "start_s": 10, "end_s": 12.5}]})
    assert [c.kind for c in cs] == ["wrong", "missed"] and cs[1].correct_label is None


@pytest.mark.parametrize("payload,why", [
    ({}, "corrections"),
    ({"corrections": []}, "corrections"),
    ({"corrections": [good(kind="maybe")]}, "kind"),
    ({"corrections": [good(behavior="grooming")]}, "unknown behavior"),
    ({"corrections": [good(correct_label="grooming")]}, "unknown label"),
    ({"corrections": [good(correct_label="attack")]}, "same as the scored"),
    ({"corrections": [good(correct_label=None)]}, "correct label"),
    ({"corrections": [{"kind": "missed", "behavior": "mount", "start_s": 1, "end_s": 2, "correct_label": "x"}]},
     "no correct_label"),
    ({"corrections": [good(start_s=5, end_s=5)]}, "after start"),
    ({"corrections": [good(start_s=-1)]}, "start_s"),
    ({"corrections": [good(start_s=0, end_s=200)]}, "at most"),
    ({"corrections": [good(start_s=70, end_s=71)]}, "after the end"),
    ({"corrections": [good(start_s=59, end_s=65)]}, "ends after"),
    ({"corrections": [good(start_s="a")]}, "start_s"),
    ({"corrections": [good(extra=1)]}, "extra"),
    ({"corrections": [good()] * 6}, "at most 5"),
])
def test_invalid_payloads_are_rejected_with_a_reason(payload, why):
    with pytest.raises(C.CorrectionError) as e:
        val(payload)
    assert why in str(e.value)


def test_total_cap_counts_earlier_corrections():
    with pytest.raises(C.CorrectionError, match="at most 5"):
        val({"corrections": [good(), good()]}, existing=4)


def test_conversion_to_revise_format():
    labels = np.full(100, 3, np.int8)
    labels[20:40] = 0     # attack
    cs = val({"corrections": [
        good(start_s=2.0, end_s=4.0, correct_label="none"),                       # FP of attack
        good(start_s=2.0, end_s=3.0, correct_label="mount"),                      # really mount: FP + FN
        {"kind": "missed", "behavior": "investigation", "start_s": 6.0, "end_s": 7.0}]})
    out = C.to_revise_format(cs, labels, NAMES, 10.0)
    keys = {"seq", "s0", "s1", "behavior", "kind", "predicted_label", "correct_label"}
    assert all(set(o) == keys and o["seq"] == "video" for o in out)
    assert out[0] == {"seq": "video", "s0": 20, "s1": 40, "behavior": "attack", "kind": "fp",
                      "predicted_label": "attack", "correct_label": "none"}
    assert [(o["behavior"], o["kind"]) for o in out[1:3]] == [("attack", "fp"), ("mount", "fn")]
    assert out[2]["predicted_label"] == "attack" and out[2]["correct_label"] == "mount"
    assert out[3] == {"seq": "video", "s0": 60, "s1": 70, "behavior": "investigation", "kind": "fn",
                      "predicted_label": "none", "correct_label": "investigation"}


def test_window_is_clamped_and_never_empty():
    c = C.Correction(kind="missed", behavior="attack", start_s=9.95, end_s=10.0)
    s0, s1 = C.window(c, 10.0, 100)
    assert s1 > s0 and s1 <= 100
    s0, s1 = C.window(C.Correction(kind="missed", behavior="attack", start_s=9.9, end_s=10.0), 10.0, 100)
    assert (s0, s1) == (99, 100)


def test_satisfied_counts_on_final_labels():
    labels = np.full(100, 3, np.int8)
    labels[20:40] = 0
    corr = [{"s0": 20, "s1": 40, "behavior": "attack", "kind": "fp"},       # still attack: not satisfied
            {"s0": 50, "s1": 60, "behavior": "attack", "kind": "fp"},       # not attack: satisfied
            {"s0": 20, "s1": 30, "behavior": "attack", "kind": "fn"},       # is attack: satisfied
            {"s0": 60, "s1": 70, "behavior": "mount", "kind": "fn"}]        # not mount: not satisfied
    assert C.satisfied(labels, corr, NAMES) == {"satisfied": 2, "n": 4}
