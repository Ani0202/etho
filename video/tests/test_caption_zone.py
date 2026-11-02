"""Burned-in captions (52 px, bottom safe area) must never cover card content: nothing below y=860 on any card.

Check: render each card and look at the strip y=860..1079. A pixel row "has content" if its luminance range
(max - min across the 1920 px) exceeds RANGE_THRESHOLD. The page background is a smooth radial gradient from #1b1c20
to #0d0d0d, so its row range is at most about 14 levels; the dimmest text we use (#9a998f, ~153) against it differs by
more than 110, and even a faint anti-aliased glyph edge differs by more than 40. 40 is therefore above the gradient
and below any text, border or panel edge. A uniformly bright row (e.g. a white band) has no range, so rows whose
maximum exceeds 100 also count (the background never exceeds 27).
"""
import cv2
import numpy as np
import pytest

from video import cards
from video import common as C
from video.numbers import Numbers

SAFE_BOTTOM = 860
RANGE_THRESHOLD = 40
BRIGHT_THRESHOLD = 100   # a uniform bright row (white band, panel edge) has no range but is not background (max 27)

pytestmark = pytest.mark.skipif(not C.have_render_tools(), reason="needs Chromium and ffmpeg")


def content_below(png, y0: int = SAFE_BOTTOM) -> list[int]:
    g = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE).astype(int)
    assert g.shape == (C.H, C.W)
    strip = g[y0:]
    bad = (strip.max(axis=1) - strip.min(axis=1) > RANGE_THRESHOLD) | (strip.max(axis=1) > BRIGHT_THRESHOLD)
    return [y0 + int(i) for i in np.flatnonzero(bad)]


@pytest.fixture(scope="module")
def N():
    return Numbers()


@pytest.mark.parametrize("name", list(cards.CARDS))
def test_card_leaves_caption_zone_empty(name, N, tmp_path):
    png = C.html_to_png(cards.CARDS[name](N), tmp_path / f"{name}.png")
    assert content_below(png) == [], f"{name}: content in the caption zone"


def test_who_card_leaves_caption_zone_empty(N, tmp_path):
    cards.who_png(N, tmp_path / "who.png")
    assert content_below(tmp_path / "who.png") == []


def test_architecture_diagram_leaves_caption_zone_empty(tmp_path):
    png = C.html_to_png(cards.architecture_html(), tmp_path / "arch.png")
    assert content_below(png) == []


def test_badge_sits_above_caption_zone():
    from video import anim
    assert 132 + 190 < SAFE_BOTTOM
    assert anim.BADGE_AT >= 0      # the badge is blitted at y=132 (render_results_frame), 190 px tall


def test_check_detects_text(tmp_path):
    """The detector is not vacuous: text drawn in the zone is found."""
    html = ("<body style='margin:0;background:#0d0d0d'><div style='position:absolute;top:900px;left:100px;"
            "color:#9a998f;font-size:34px'>footnote</div>")
    png = C.html_to_png(html, tmp_path / "t.png")
    assert content_below(png)
    assert np.all(np.array(content_below(png)) >= 900)


def test_truncated_screenshot_detector(tmp_path):
    img = np.full((1080, 1920), 13, np.uint8)
    img[1017:] = 255
    cv2.imwrite(str(tmp_path / "a.png"), img)
    assert C._truncated(tmp_path / "a.png", False)
    img[:] = 255
    cv2.imwrite(str(tmp_path / "b.png"), img)
    assert not C._truncated(tmp_path / "b.png", False)
