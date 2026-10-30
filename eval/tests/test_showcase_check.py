from eval.showcase_check import bout_category, decide, merged_count


def test_merged_count_merges_only_gaps_strictly_below_threshold():
    bs = [(0, 10), (14, 20), (25, 30), (60, 70)]  # gaps 4, 5, 30 samples
    assert merged_count(bs, 5) == 3   # only the gap of 4 merges
    assert merged_count(bs, 6) == 2
    assert merged_count(bs, 31) == 1
    assert merged_count([], 5) == 0


def test_decide_mirrors_app_restriction():
    assert decide({"label": "mount", "rule_label": "mount", "confidence": 0.9}) == "agreed_with_rule"
    assert decide({"label": "attack", "rule_label": "mount", "confidence": 0.9}) == "set_aside_by_restriction"
    assert decide({"label": "investigation", "rule_label": "mount", "confidence": 0.9}) == "applied"
    low = "set_aside_low_confidence_or_unsure"
    assert decide({"label": "none", "rule_label": "mount", "confidence": 0.59}) == low
    assert decide({"label": "unsure", "rule_label": "mount", "confidence": 0.99}) == low


def test_bout_category():
    assert bout_category([]).startswith("rule-only")
    assert bout_category(["agreed_with_rule"]).startswith("confirmed")
    assert bout_category(["set_aside_by_restriction"]).startswith("set aside")
    assert bout_category(["agreed_with_rule", "set_aside_by_restriction"]).startswith("other")
