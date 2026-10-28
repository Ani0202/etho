import numpy as np

from etho.adjudication.tier1 import apply, make_windows, parse_verdict

CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}
LABELS = ["attack", "investigation", "mount"]


def test_parse_verdict_variants():
    v = parse_verdict('{"label": "mount", "confidence": 0.8, "reason": "hunched"}', LABELS)
    assert v == ("mount", 0.8, "hunched")
    v = parse_verdict('thinking...\n```json\n{"label":"Attack","confidence":"0.7"}\n```', LABELS)
    assert v[:2] == ("attack", 0.7)
    assert parse_verdict('{"label": "none", "confidence": 2}', LABELS)[:2] == ("none", 1.0)
    assert parse_verdict("I think this is investigation.", LABELS)[:2] == ("investigation", 0.0)
    assert parse_verdict("no idea", LABELS)[0] == "unsure"


def test_make_windows_splits_long_bouts_and_adds_contact():
    lab = np.full(100, 3, np.int8)
    lab[10:75] = 2                       # 6.5 s mount -> 3 windows of <= 3 s
    t = {"gap": np.full(100, 100.0)}
    t["gap"][80:90] = 0.0                # unlabeled contact episode
    ws = make_windows("s", lab, t, CODES, 10.0)
    assert [(w.s0, w.s1, w.rule_label, w.source) for w in ws] == [
        (10, 40, "mount", "rule"), (40, 70, "mount", "rule"), (70, 75, "mount", "rule"), (80, 90, "other", "contact")]


def test_apply_respects_confidence():
    lab = np.full(20, 3, np.int8)
    v = [{"s0": 0, "s1": 5, "label": "attack", "confidence": 0.9},
         {"s0": 5, "s1": 10, "label": "mount", "confidence": 0.3},
         {"s0": 10, "s1": 15, "label": "none", "confidence": 0.9},
         {"s0": 15, "s1": 20, "label": "unsure", "confidence": 0.0}]
    lab[10:15] = 1
    out = apply(lab, v, CODES, 0.6, allow=frozenset({'attack', 'investigation', 'mount', 'none'}))
    assert out[:5].tolist() == [0] * 5 and out[5:10].tolist() == [3] * 5 and out[10:15].tolist() == [3] * 5


def test_default_apply_never_promotes_to_attack_or_mount():
    lab = np.full(10, 3, np.int8)
    v = [{"s0": 0, "s1": 5, "label": "attack", "confidence": 0.95},
         {"s0": 5, "s1": 10, "label": "investigation", "confidence": 0.95}]
    assert apply(lab, v, CODES).tolist() == [3] * 5 + [1] * 5
