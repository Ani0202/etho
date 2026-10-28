"""Two-fly features: geometry, approach/toward velocity, heading sign from motion, pair-level symmetry."""

import numpy as np

from etho.tracking.fly_features import FLY_FEATURE_DOC, PER_FLY_RAW, edge_ok, finalize_fly, resolve_heading

FPS = 30.0
BL = 27.0


def blank(n):
    t = {}
    for p in ("a", "b"):
        for c in PER_FLY_RAW:
            t[f"{p}_{c}"] = np.full(n, np.nan)
        t[f"{p}_present"][:] = 1
        t[f"{p}_angle"][:] = 0.0
        t[f"{p}_area"][:] = 210.0
        t[f"{p}_elong"][:] = 2.7
        t[f"{p}_major_len"][:] = 27.0
        t[f"{p}_minor_len"][:] = 10.0
        t[f"{p}_solidity"][:] = 0.93
        t[f"{p}_thin_frac"][:] = 0.0
        t[f"{p}_reach_ratio"][:] = 1.0
    t["gap"] = np.full(n, 20.0)
    return t


def fin(t):
    """finalize_fly with the body centroid defaulting to the full centroid (synthetic tables)."""
    for p in ("a", "b"):
        for c in ("x", "y"):
            if not np.isfinite(t[f"{p}_b{c}"]).any():
                t[f"{p}_b{c}"] = t[f"{p}_c{c}"].copy()
    return finalize_fly(t, FPS, BL)


def test_approach_toward_speed_facing_and_symmetry():
    n = 200
    t = blank(n)
    g = np.arange(n)
    t["a_cx"], t["a_cy"] = 20.0 + 1.0 * g, np.full(n, 100.0)       # 1 px/sample = 30 px/s toward B
    t["b_cx"], t["b_cy"] = np.full(n, 250.0), np.full(n, 100.0)    # B stays put
    fin(t)
    mid = slice(20, n - 20)
    np.testing.assert_allclose(t["a_speed"][mid], 30.0, atol=0.5)
    np.testing.assert_allclose(t["a_toward_speed"][mid], 30.0, atol=0.5)
    np.testing.assert_allclose(t["b_toward_speed"][mid], 0.0, atol=0.5)
    np.testing.assert_allclose(t["approach"][mid], -30.0, atol=0.5)
    np.testing.assert_allclose(t["dist"][mid], 230.0 - g[mid], atol=1e-6)
    np.testing.assert_allclose(t["dist_bl"][mid], t["dist"][mid] / BL)
    assert t["a_facing"][mid].max() < 0.05 and t["a_axis_align"][mid].max() < 0.05
    # B never moves, so its heading falls back to the raw axis (+x) and it "faces away" from A (documented);
    # the axis-based feature needs no sign and says B's axis points along the line to A
    assert t["b_axis_align"][mid].max() < 0.05
    assert abs(t["b_facing"][100] - np.pi) < 0.05
    # pair-level features are symmetric maxima/minima
    np.testing.assert_allclose(t["max_toward_speed"][mid], 30.0, atol=0.5)
    np.testing.assert_allclose(t["max_speed"][mid], 30.0, atol=0.5)
    np.testing.assert_allclose(t["min_axis_align"][mid], 0.0, atol=0.05)


def test_swapping_fly_labels_leaves_pair_features_unchanged():
    n = 120
    rng = np.random.default_rng(0)
    t = blank(n)
    t["a_cx"], t["a_cy"] = np.cumsum(rng.normal(0, 1, n)) + 60, np.cumsum(rng.normal(0, 1, n)) + 60
    t["b_cx"], t["b_cy"] = np.cumsum(rng.normal(0, 1, n)) + 90, np.cumsum(rng.normal(0, 1, n)) + 70
    t["a_angle"], t["b_angle"] = rng.uniform(-1.5, 1.5, n), rng.uniform(-1.5, 1.5, n)
    s = {k: v.copy() for k, v in t.items()}
    for c in list(t):
        if c.startswith("a_"):
            s["b_" + c[2:]], s[c] = t[c].copy(), t["b_" + c[2:]].copy()
    fin(t)
    fin(s)
    pair_keys = [k for k in t if k.startswith(("max_", "min_"))] + [
        "dist", "dist_bl", "dist_body", "approach", "gap", "contact", "both_present", "vel_ok", "vel_ok_s"]
    assert len(pair_keys) >= 20
    for k in pair_keys:
        np.testing.assert_allclose(t[k], s[k], equal_nan=True, err_msg=k)


def test_wing_like_shape_change_shows_in_pair_level_features():
    n = 100
    t = blank(n)
    t["a_cx"], t["a_cy"], t["b_cx"], t["b_cy"] = np.full(n, 40.0), np.full(n, 40.0), np.full(n, 90.0), np.full(n, 40.0)
    sl = slice(40, 60)
    t["b_area"][sl] = 210.0 * 1.3
    t["b_minor_len"][sl] = 14.0
    t["b_elong"][sl] = 1.9
    t["b_solidity"][sl] = 0.6
    t["b_thin_frac"][sl] = 0.2
    fin(t)
    assert t["max_area_ratio"][50] > 1.2 and t["max_area_ratio"][10] < 1.05
    assert t["max_minor_ratio"][50] > 1.3
    assert t["min_elong"][50] < 2.0 and t["min_solidity"][50] < 0.7 and t["max_thin_frac"][50] > 0.15


def test_heading_sign_comes_from_motion_and_survives_axis_flips():
    n = 120
    g = np.arange(n)
    # moves down the image (+y) at 60 px/s; the axis angle is reported as +pi/2 or -pi/2 at random
    rng = np.random.default_rng(1)
    angle = np.where(rng.random(n) < 0.5, np.pi / 2, -np.pi / 2) + rng.normal(0, 0.01, n)
    vx, vy = np.zeros(n), np.full(n, 60.0)
    speed = np.hypot(vx, vy)
    h = resolve_heading(angle, vx, vy, speed, thr=27.0)
    assert np.all(np.abs(h - np.pi / 2) < 0.1)
    # stationary start, then movement: the head sign found at the first anchor is back-filled
    speed2, vy2 = np.where(g >= 60, 60.0, 0.0), np.where(g >= 60, -60.0, 0.0)   # moves up the image
    ang2 = np.full(n, np.pi / 2)
    h2 = resolve_heading(ang2, np.zeros(n), vy2, speed2, thr=27.0)
    assert np.all(np.abs(h2 + np.pi / 2) < 0.1)


def test_missing_flies_give_nans_not_errors_and_every_feature_is_documented():
    n = 50
    t = blank(n)
    t["a_cx"], t["a_cy"] = np.linspace(10, 60, n), np.full(n, 30.0)
    t["b_cx"], t["b_cy"] = np.full(n, 100.0), np.full(n, 30.0)
    for c in list(t):
        if c.startswith("b_") and c != "b_present":
            t[c][20:30] = np.nan
    t["b_present"][20:30] = 0
    t["n_objects"], t["id_epoch"], t["id_unknown"] = np.full(n, 2.0), np.zeros(n), np.zeros(n)
    t["src_frame"] = np.arange(n)
    fin(t)
    assert np.isnan(t["dist"][25]) and np.isnan(t["b_toward_speed"][25]) and t["both_present"][25] == 0
    assert np.isfinite(t["max_speed"][25])          # one fly is enough for the symmetric pair-level max
    documented = set()
    for k in FLY_FEATURE_DOC:
        for name in k.split(", "):
            documented.add(name)
    for k in t:
        base = k[2:] if k[:2] in ("a_", "b_") else k
        assert base in documented, f"undocumented feature {k}"


def test_velocity_columns_are_nan_around_epoch_changes_and_unknown_run_edges():
    n = 300
    t = blank(n)
    g = np.arange(n)
    t["a_cx"], t["a_cy"] = 20.0 + 0.5 * g, np.full(n, 100.0)
    t["b_cx"], t["b_cy"] = np.full(n, 250.0), 100.0 + 0.2 * g
    t["id_epoch"] = np.where(g >= 100, 1.0, 0.0)
    t["id_unknown"] = np.where((g >= 200) & (g < 220), 1.0, 0.0)
    fin(t)
    k_fast, k_slow = 3, 15
    for edge in (100, 200, 220):
        assert not t["vel_ok"][edge] and not t["vel_ok"][edge - k_fast] and not t["vel_ok"][edge + k_fast - 1]
        assert t["vel_ok"][edge - k_fast - 3] and t["vel_ok"][edge + k_fast + 3]
        assert not t["vel_ok_s"][edge - k_slow] and not t["vel_ok_s"][edge + k_slow - 1]
        for c in ("a_speed", "b_toward_speed", "approach", "max_speed", "max_toward_speed", "a_ang_speed"):
            assert np.isnan(t[c][edge]), c
        for c in ("a_speed_s", "a_toward_speed_s", "a_heading", "a_facing", "min_facing", "max_speed_s"):
            assert np.isnan(t[c][edge]), c
    assert t["vel_ok_s"][100 - k_slow - 3] and t["vel_ok_s"][100 + k_slow + 3]
    assert np.isfinite(t["a_speed"][150]) and np.isfinite(t["a_heading"][150]) and np.isfinite(t["max_speed"][150])
    # inside an unknown run (away from its edges) velocities are still reported
    assert np.isfinite(t["a_speed"][210]) and t["vel_ok"][210] == 1
    # position, shape and distance columns are not touched
    assert np.isfinite(t["a_cx"][100]) and np.isfinite(t["dist"][100]) and np.isfinite(t["a_axis_align"][100])


def test_abrupt_mask_area_change_invalidates_nearby_velocity():
    n = 100
    t = blank(n)
    t["a_area"][50:] = 210.0 * 1.6                    # a wing appears in the mask at sample 50
    t["a_cx"], t["a_cy"] = np.full(n, 40.0), np.full(n, 40.0)
    t["b_cx"], t["b_cy"] = np.full(n, 90.0), np.full(n, 40.0)
    ok = edge_ok(t, 3)
    assert not ok[50] and not ok[48] and ok[40] and ok[60]
    # slow columns keep the short margin for shape jumps
    assert edge_ok(t, 15, k_jump=3)[60]
