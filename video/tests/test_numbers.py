"""Every number on a card must be traceable to a committed results file (no network, no model calls)."""
import re

import pytest

from video import cards
from video.numbers import NAME_TOKENS, Numbers, numeric_tokens, untraceable


@pytest.fixture(scope="module")
def N():
    return Numbers()


def test_rounding_matches_displayed_values(N):
    want = {"macro_f1": "0.58", "f1_attack": "0.449", "f1_investigation": "0.635", "f1_mount": "0.656",
            "sup_1dconv": ".793", "sup_taskprog": ".829", "sup_mabe_top1": ".864", "sup_lo": "0.79", "sup_hi": "0.86",
            "speed_pooled": "0.88", "speed_worst": "1.54", "manual_lo": "3", "manual_hi": "4",
            "cost_per_hour": "$11.25", "clip_macro": "0.42", "n_test_videos": "19", "n_dev_videos": "13"}
    for k, v in want.items():
        assert N.s(k) == v, k


def test_every_number_has_a_committed_source(N):
    for n in N._all.values():
        assert n.file.startswith("eval/results/"), n
        assert n.where


def test_clip_scores_are_not_the_pooled_ones(N):
    assert N.get("clip_macro").raw < N.get("macro_f1").raw  # "one of the harder clips"
    assert N.s("clip_f1_mount") == "0.000"


CARD_BUILDERS = {**cards.CARDS, "who": cards.who_html, "badge": cards.badge_html}


@pytest.mark.parametrize("name", sorted(CARD_BUILDERS))
def test_card_numbers_are_traceable(name, N):
    text = cards.card_text(CARD_BUILDERS[name](N))
    assert untraceable(text, N) == [], f"{name}: untyped numbers {untraceable(text, N)}"


def test_the_checker_catches_a_typed_number(N):
    assert untraceable("reaches 0.59 F1", N) == ["0.59"]
    assert untraceable("about 12 times", N) == ["12"]
    assert untraceable("macro F1 0.58 on SAM 3.1", N) == []
    assert "3.1" in NAME_TOKENS and numeric_tokens("0:30 axis, F1") == []


def test_no_hardcoded_result_numbers_in_card_sources():
    """The card scripts must not contain result-looking literals; numbers come from Numbers.s()/get()."""
    import inspect
    src = inspect.getsource(cards)
    for tok in ("0.58", "0.449", "0.635", "0.656", ".793", ".829", ".864", "11.25", "0.88", "1.54", "0.42"):
        assert tok not in src, tok


def test_honesty_checks(N):
    for name, fn in CARD_BUILDERS.items():
        text = cards.card_text(fn(N)).lower()
        assert "beats supervised" not in text and "outperform" not in text
        assert not re.search(r"\d\s*(x|×)\s*faster|faster", text)
        assert "fly" not in text and "flies" not in text            # no fly F1 anywhere on a result card
        if N.s("macro_f1") in text:                                  # 0.58 always with the three per-behavior F1s
            for b in ("attack", "investigation", "mount"):
                assert N.s(f"f1_{b}") in text, (name, b)
        if "training-free" in text or "no training" in text:         # claim always with its footnote
            assert N.s("footnote_text").lower() in text, name


def test_evidence_labels_supervised_correctly(N):
    t = cards.card_text(cards.evidence_html(N))
    assert "trained on labeled data" in t and "pose keypoints" in t
    assert "Segalin et al. 2021" in t


def test_title_card_has_required_text(N):
    t = cards.card_text(cards.title_html(N))
    assert "Etho" in t and "Push the Frontier" in t and "Training-free" in t


def test_close_card_has_models_and_placeholders(N):
    t = cards.card_text(cards.close_html(N))
    for s in ("SAM 3.1", "Muse Spark", "Muse Spark Open", "for every lab that still scores behavior by hand",
              "[placeholder: public repo URL]", "[placeholder: live app URL]"):
        assert s.lower() in t.lower(), s
