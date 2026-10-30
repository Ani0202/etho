"""Tests for eval/fly_clip_window.py (synthetic labels, no network, no data files)."""

import numpy as np

from eval import flies_extract as fx
from eval import fly_clip_window as w


def _actions(rows: dict) -> dict:
    """rows: {(fly, beh): [(s, e), ...]} python half-open."""
    bouts = {b: [np.array(rows.get((f, b), []), np.int64).reshape(-1, 2) for f in range(2)] for b in fx.ACTION_BEHS}
    return {"behs": list(fx.ACTION_BEHS), "bouts": bouts, "flags": {}}


def test_window_scores_half_open_and_count():
    s0 = w.SPAN[0]
    sc = w.window_scores([s0, s0 + 449, s0 + 450, w.SPAN[1] - 1])
    assert len(sc) == 286  # offsets 0..285 s
    assert sc[0] == 2  # onset at frame 450 is outside [0, 450)
    assert sc[1] == 2  # [30, 480): 449 and 450
    assert sc[285] == 1  # last window [8550, 9000) holds the final frame


def test_rank_ties_earlier_movie_then_start():
    r = w.rank({3: np.array([2, 5, 5]), 2: np.array([5, 1, 5]), 4: np.array([6, 0, 0])})
    assert r[0] == (4, 0, 6)
    assert r[1:4] == [(2, 0, 5), (2, 2, 5), (3, 1, 5)]


def test_bout_onsets_per_fly_full_movie_runs_and_unknown_drop():
    s0 = w.SPAN[0]
    a = _actions({
        (0, "lunge"): [(s0 - 5, s0 + 3), (s0 + 100, s0 + 104), (s0 + 104, s0 + 110)],  # straddler; touching -> 1 run
        (1, "lunge"): [(s0 + 100, s0 + 103)],  # other fly: its own bout
        (0, "wing_threat"): [(s0 + 200, s0 + 230)],
        (1, "wing_threat"): [(s0 + 300, s0 + 302)],  # fully unknown -> dropped
        (0, "unknown"): [(s0 + 300, s0 + 302)],
        (0, "charge"): [(s0 + 400, s0 + 401)],  # not a scored behavior
    })
    got = sorted((b["behavior"], b["fly"], b["start"]) for b in w.bout_onsets(a))
    assert got == [("lunge", 0, s0 + 100), ("lunge", 1, s0 + 100), ("wing_threat", 0, s0 + 200)]
