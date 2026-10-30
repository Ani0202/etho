"""The beat timeline must match docs/DEMO_PLAN.md and fit the 3:00 hard limit; assembler pieces must tile it."""
import re

from video import assemble
from video import timeline as T


def test_timeline_sums_within_hard_limit():
    assert sum(b.dur for b in T.BEATS) <= T.HARD_LIMIT_S
    assert T.total() == sum(b.dur for b in T.BEATS) == 176.0


def test_timeline_matches_plan_table():
    rows = T.parse_plan_table()
    assert len(rows) == len(T.BEATS) == 9
    for (title, s, e), b in zip(rows, T.BEATS, strict=True):
        assert (title, s, e) == (b.title, b.start, b.end)


def test_beats_are_contiguous_from_zero():
    t = 0.0
    for b in T.BEATS:
        assert b.start == t and b.end > b.start
        t = b.end


def test_pieces_tile_each_beat_exactly():
    for b in T.BEATS:
        assert abs(sum(d for _, _, d in assemble.PLAN[b.key]) - b.dur) < 1e-9, b.key
    assert set(assemble.PLAN) == {b.key for b in T.BEATS}


def test_still_times_fall_inside_their_beats():
    for b in T.BEATS:
        assert b.start <= assemble.STILL_AT[b.key] < b.end, b.key


def test_captions_fit_inside_the_cut_and_are_big_enough(tmp_path):
    caps = assemble.srt_count(assemble.C.ROOT / "docs" / "demo_captions_v5.srt")
    assert len(caps) == 34 and max(e for _, e in caps) <= T.total()
    ass = tmp_path / "c.ass"
    assert assemble.srt_to_ass(assemble.C.ROOT / "docs" / "demo_captions_v5.srt", ass) == 34
    txt = ass.read_text()
    assert "PlayResY: 1080" in txt and assemble.CAP_FONT_PX >= 48
    style = next(ln for ln in txt.splitlines() if ln.startswith("Style:"))
    assert int(style.split(",")[2]) == assemble.CAP_FONT_PX
    assert re.search(r"Dialogue: 0,0:00:00\.30,0:00:06\.50,Default", txt)


def test_hard_limit_constant_is_three_minutes():
    assert T.HARD_LIMIT_S == 180.0
