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
