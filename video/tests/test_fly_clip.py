"""Fly clip helpers. The render test skips when the git-ignored run data is absent."""
import numpy as np
import pytest

from video import fly_clip as F


def test_runs_and_codes():
    wing = np.array([0, 1, 1, 0, 0, 0])
    lunge = np.array([0, 0, 1, 0, 1, 0])
    codes = F.frame_codes(wing, lunge)
    assert codes.tolist() == [0, 1, 2, 0, 2, 0]          # lunge wins the tie
    assert F.runs_of(codes) == [(1, 3), (4, 5)]


def test_pick_window_centers_on_longest_bout_and_clamps():
    codes = np.zeros(1000, np.uint8)
    codes[100:104] = 2
    codes[600:612] = 2                                    # longest
    s, e, bout = F.pick_window(codes, 30.0)
    assert bout == (600, 612) and s == 600 - 75 and e == 600 + 135
    codes2 = np.zeros(200, np.uint8)
    codes2[10:20] = 1
    s, e, _ = F.pick_window(codes2, 30.0)
    assert s == 0 and e == 145
    with pytest.raises(ValueError):
        F.pick_window(np.zeros(10, np.uint8), 30.0)


def test_clip_shows_no_accuracy_number_and_has_required_labels():
    assert F.BADGE == "Qualitative preview: not scored"
    assert F.CREDIT == "Video: Fly vs. Fly (Eyjolfsdottir et al. 2014), CC0"
    assert 13 <= (F.PRE_S + F.POST_S) / F.SPEED <= 15
    shown = " ".join([F.BADGE, F.CREDIT, *F.NAMES.values()]).lower()
    assert "f1" not in shown and "%" not in shown


def test_draw_masks_marks_active_fly_thicker():
    m = np.zeros((10, 10), np.uint8)
    m[2:8, 2:8] = 1
    canvas = np.zeros((100, 100, 3), np.uint8)
    xy = np.array([[5.0, 5.0], [50.0, 50.0]])
    F.draw_masks(canvas, [((0, 0), m)], xy, {0}, (0, 0, 255), 10.0)
    assert (canvas == np.array([0, 0, 255])).all(-1).any()
