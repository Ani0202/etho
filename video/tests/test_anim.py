"""Animation helpers. Data-dependent tests skip when the (git-ignored) showcase run is absent."""
import numpy as np
import pytest

from video import anim, common as C, draw
from video.data import expert_codes, showcase

needs_showcase = pytest.mark.skipif(not (C.SHOWCASE / "result.json").exists(), reason="showcase run not on disk")


@needs_showcase
def test_final_labels_equal_final_bouts():
    sc = showcase()
    for i, b in enumerate(C.BEHAVIORS):
        m = np.zeros(sc["n"], bool)
        for s0, s1 in sc["etho"][b]:
            m[s0:s1] = True
        assert ((sc["labels"] == i) == m).all(), b


@needs_showcase
def test_traced_error_is_the_thirty_eight_second_mount():
    assert anim.traced_error(showcase()) == (37.5, 44.0)


@needs_showcase
def test_opening_segment_is_lively_and_not_cherry_picked():
    """The comment in anim.py claims 121-126 s is fully active, has all three behaviors, >= 2 label changes, and
    agrees with the expert on well under 100% of frames."""
    sc = showcase()
    lo, hi = int(anim.OPEN_T0 * sc["fps"]), int((anim.OPEN_T0 + anim.OPEN_DUR) * sc["fps"])
    a, e = sc["labels"][lo:hi], expert_codes(sc)[lo:hi]
    assert (a != 3).all()
    assert set(a.tolist()) == {0, 1, 2}
    assert (np.diff(a) != 0).sum() >= 2
    assert 0.5 < (a == e).mean() < 1.0
    assert anim.OPEN_DUR <= 5.0 and anim.OPEN_DUR >= 3.0


def test_runs_roundtrip():
    codes = np.array([3, 3, 0, 0, 1, 3])
    assert list(anim.runs(codes)) == [(3, 0, 2), (0, 2, 4), (1, 4, 5), (3, 5, 6)]


def test_fill_bar_uses_app_colors_and_clips():
    img = np.zeros((40, 100, 3), np.uint8)
    draw.fill_bar(img, 10, 50, 5, 25, "attack")
    assert tuple(img[10, 20]) == C.hex2bgr(C.COLORS["attack"])      # solid slot
    assert img[30, 20].sum() == 0
    draw.fill_bar(img, 90, 400, 5, 25, "mount")                    # clipped at the frame edge, no exception
    assert C.COLORS == {"attack": "#3987e5", "investigation": "#d95926", "mount": "#199e70"}


def test_view_zoom_interpolates_between_full_clip_and_error_window():
    assert anim.results_view(0.0, 300.0) == (0.0, 300.0)
    assert anim.results_view(anim.ZOOM[1] + 1, 300.0) == anim.ERR_VIEW


@pytest.mark.skipif(not C.have_render_tools(), reason="needs Chromium + ffmpeg")
def test_title_card_renders_1080p(tmp_path):
    import cv2
    from video import cards
    from video.numbers import Numbers
    png = C.html_to_png(cards.title_html(Numbers()), tmp_path / "t.png")
    assert cv2.imread(str(png)).shape == (1080, 1920, 3)
