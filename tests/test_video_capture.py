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


# ---- capture_live.py: the one-job live-run recorder (no browser, no network)
cl = _load("capture_live")


def test_live_definitions_are_the_dataset_authors_text():
    d = cl.load_definitions()
    assert d["subject"] == "the resident (black) mouse" and d["partner"] == "the intruder (white) mouse"
    assert [n for n, _ in d["behaviors"]] == ["attack", "investigation", "mount"]
    assert all(len(t) > 80 for _, t in d["behaviors"])
    assert cl.form_matches(d, d) == []
    changed = dict(d, behaviors=[(n, t + " ") for n, t in d["behaviors"]])
    assert cl.form_matches(changed, d) == ["behaviors"]
    assert cl.form_matches(dict(d, subject="x"), d) == ["subject"]


def test_live_job_id_from_url():
    assert cl.job_id_from_url("http://127.0.0.1:8770/p/jabc123?present=1") == "jabc123"
    assert cl.job_id_from_url("http://127.0.0.1:8770/") is None


def test_live_lock_refuses_second_submit(tmp_path):
    lock = tmp_path / "sub" / "lock.json"
    cl.claim_lock(lock, "http://x")
    cl.record_job_id(lock, "j1", "http://x/p/j1")
    assert '"j1"' in lock.read_text()
    with pytest.raises(SystemExit):
        cl.claim_lock(lock, "http://x")


def test_live_real_flag_required_for_8770(tmp_path):
    with pytest.raises(SystemExit):
        cl.main(["--base", "http://127.0.0.1:8770", "--video", str(tmp_path / "v.mp4")])


# ---- cut_live_run.py: the 29 s scene-3 cut of the raw live-run recording
cut = _load("cut_live_run")


def test_cut_is_29_seconds_and_tagged_at_every_skip():
    cl = cut.cut_list()
    assert cl["total_s"] == cut.TOTAL_S == 29.0
    assert [r["tag"] for r in cl["segments"]] == [None, "time skipped 0:23", "time skipped 3:13", "time skipped 0:04",
                                                  "earlier in the run", "time skipped 0:04"]
    assert all(r["raw_out"] <= 271.7 and r["raw_in"] >= 0 for r in cl["segments"])
    ends = [r["out_out"] for r in cl["segments"]]
    assert ends == sorted(ends)


def test_cut_filter_graph_has_one_input_per_segment_and_a_tag_per_skip():
    g = cut.filter_graph()
    assert g.count("concat=n=6") == 1 and g.count("drawtext") == 5
    assert "[5:v]" in g and "[6:v]" not in g
    args = cut.inputs("raw.mp4")
    assert args.count("-i") == 6 and args[:4] == ["-ss", "20.2", "-t", "1.500"]
