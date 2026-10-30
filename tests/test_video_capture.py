"""Tests for video/capture helpers (no network, no browser, no model calls)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "video" / "capture" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ca = _load("code_assets")


@pytest.mark.parametrize("ex", ca.EXCERPTS, ids=lambda e: e[0])
def test_excerpts_match_source(ex):
    key, rel, title, cap, ranges, anchor = ex
    rows = [r for r in ca.read_ranges(rel, ranges) if r is not None]
    assert anchor in rows[0][1]
    assert 15 <= len(rows) <= 30
    assert max(len(t) for _, t in rows) <= 125     # fits 1920 px at the slide font size


def test_highlight_has_markup():
    html_, css = ca.highlight([(1, "def f(x):"), None, (9, "    return x")])
    assert "<span" in html_ and "..." in html_ and ".code" in css


def test_pan_expr_and_duration():
    e = ca.pan_expr(3, 2.0, 1.0)
    assert e.count("clip(") == 6 and "1920*" in e   # 3 per boundary, 2 boundaries
    assert ca.pan_duration(5, 2.6, 1.0) == pytest.approx(17.0)


def test_parse_pytest_summary():
    assert (ca.parse_pytest_summary("....\n662 passed, 3 skipped in 91.20s (0:01:31)\n")
            == "662 passed, 3 skipped in 91.20s (0:01:31)")
    with pytest.raises(ValueError):
        ca.parse_pytest_summary("nothing here")


def test_date_span():
    assert ca.date_span({"first": "2026-10-28", "last": "2026-10-28"}) == "Oct 28"
    assert ca.date_span({"first": "2026-10-27", "last": "2026-10-30"}) == "Oct 27 to Oct 30"
