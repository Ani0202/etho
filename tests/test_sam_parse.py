import numpy as np

from etho.tracking.sam_parse import mask_moments


def test_mask_moments_horizontal_bar():
    m = np.zeros((10, 40), dtype=np.uint8)
    m[4:6, 5:35] = 1
    area, cx, cy, ang, el = mask_moments(m, left=100, top=50)
    assert area == 60
    assert cx == 100 + 19.5 and cy == 50 + 4.5
    assert abs(ang) < 1e-6 and el > 10


def test_mask_moments_vertical():
    m = np.zeros((40, 10), dtype=np.uint8)
    m[5:35, 4:6] = 1
    _, _, _, ang, _ = mask_moments(m, 0, 0)
    assert abs(abs(ang) - np.pi / 2) < 1e-6


def _numpy_moments(m, left, top):
    ys, xs = np.nonzero(m)
    cx, cy = xs.mean(), ys.mean()
    dx, dy = xs - cx, ys - cy
    mu20, mu02, mu11 = (dx * dx).mean(), (dy * dy).mean(), (dx * dy).mean()
    angle = 0.5 * np.arctan2(2 * mu11, mu20 - mu02)
    common = np.sqrt(4 * mu11 ** 2 + (mu20 - mu02) ** 2)
    l1, l2 = (mu20 + mu02 + common) / 2, (mu20 + mu02 - common) / 2
    return len(xs), cx + left, cy + top, angle, np.sqrt(l1 / l2)


def test_mask_moments_numba_matches_numpy_formulation():
    rng = np.random.default_rng(0)
    for _ in range(20):
        h, w = int(rng.integers(2, 80)), int(rng.integers(2, 80))
        m = (rng.random((h, w)) < 0.4).astype(np.uint8)
        got = mask_moments(m, 5, 9)
        want = _numpy_moments(m, 5, 9)
        assert got[0] == want[0]
        np.testing.assert_allclose(got[1:], want[1:], rtol=1e-9, atol=1e-9)


def test_mask_moments_empty_and_single_pixel():
    area, cx, cy, ang, el = mask_moments(np.zeros((4, 4), np.uint8), 1, 2)
    assert area == 0 and np.isnan(cx) and np.isnan(cy) and np.isnan(ang) and np.isnan(el)
    m = np.zeros((4, 4), np.uint8)
    m[2, 3] = 1
    area, cx, cy, _, el = mask_moments(m, 10, 20)
    assert (area, cx, cy) == (1, 13.0, 22.0) and el == float("inf")
