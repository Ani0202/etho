import numpy as np

from eval import demo_clip as dc
from eval.common import VOCAB

ATK, INV, MNT, OTH = VOCAB["attack"], VOCAB["investigation"], VOCAB["mount"], VOCAB["other"]


def test_bouts_min_len():
    x = np.array([OTH, ATK, ATK, OTH, ATK, OTH, ATK, ATK, ATK])
    assert dc.bouts(x, ATK) == [(1, 3), (4, 5), (6, 9)]
    assert dc.bouts(x, ATK, 2) == [(1, 3), (6, 9)]


def _seq(n_frames=30 * 400):
    gt = np.full(n_frames, OTH, np.int8)
    # 4 attack + 4 mount + 4 investigation bouts of 1 s inside [100 s, 220 s)
    for k in range(4):
        gt[3000 + k * 600: 3000 + k * 600 + 30] = ATK
        gt[3200 + k * 600: 3200 + k * 600 + 30] = MNT
        gt[3400 + k * 600: 3400 + k * 600 + 30] = INV
    return gt


def test_best_window_requires_bouts_and_prefers_f1():
    gt = _seq()
    pred = gt.copy()
    bw = dc.best_window(gt, pred)
    assert bw is not None and bw["f1_window"]["macro"] == 1.0
    assert bw["bouts_min15"]["attack"] >= 3 and bw["bouts_min15"]["mount"] >= 3
    assert bw["f0"] % 300 == 0 and bw["f1"] - bw["f0"] == bw["len_s"] * 30
    # no mount at all -> no qualifying window
    gt2 = gt.copy()
    gt2[gt2 == MNT] = OTH
    assert dc.best_window(gt2, gt2) is None


def test_biggest_error_offsets():
    gt = np.array([INV, INV, INV, INV, OTH, INV, INV])
    pred = np.array([ATK, ATK, INV, ATK, ATK, ATK, ATK])
    e = dc.biggest_error(gt, pred, ATK, INV, f_off=100)
    assert e == {"f0": 105, "f1": 107, "n_frames": 2} or e == {"f0": 100, "f1": 102, "n_frames": 2}
