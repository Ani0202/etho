"""Identity for identical flies: slotting inside a chunk, Hungarian stitching across overlaps, unknown when close."""

import numpy as np

from etho.tracking.fly_identity import (
    Traj,
    major_len,
    slot_chunk,
    stitch,
    suspect_swaps,
    unknown_mask,
)

BL = 27.0


def traj(points):
    t = Traj()
    for g, (x, y, a) in points.items():
        t.add(g, x, y, a)
    return t


def obj(g, track, x, y, area=210.0):
    return {"g": g, "track": track, "cx": x, "cy": y, "area": area}


def test_major_len_of_a_fly_sized_ellipse():
    assert abs(major_len(204.0, 2.6) - 26.0) < 0.5   # axes 13 x 5
    assert np.isnan(major_len(0.0, 2.0))


def test_stitch_follows_positions_not_slot_order():
    # global A near (20, 20) moving right, B near (100, 100); the new chunk lists them in the other order
    ov = list(range(30, 40))
    A = traj({g: (20 + g, 20, 210) for g in range(0, 40)})
    B = traj({g: (100, 100 - 0.5 * g, 210) for g in range(0, 40)})
    nA = traj({g: (20 + g, 20, 212) for g in ov})
    nB = traj({g: (100, 100 - 0.5 * g, 208) for g in ov})
    st = stitch((A, B), (nB, nA), ov, BL)               # new slot 0 is really B
    assert st.perm == (1, 0) and not st.ambiguous and st.n_compared == 10 and st.n_apart == 10
    st = stitch((A, B), (nA, nB), ov, BL)
    assert st.perm == (0, 1) and not st.ambiguous


def test_stitch_close_flies_but_distinct_positions_is_not_ambiguous():
    # 20 px apart (< 1 body length): per-sample identity is unknown, but the stitch is still decisive
    ov = list(range(30, 40))
    A = traj({g: (50, 50, 210) for g in range(40)})
    B = traj({g: (50, 70, 210) for g in range(40)})
    nA = traj({g: (50.3, 50, 210) for g in ov})
    nB = traj({g: (50.3, 70, 210) for g in ov})
    st = stitch((A, B), (nB, nA), ov, BL)
    assert st.perm == (1, 0) and not st.ambiguous and st.n_apart == 0


def test_stitch_overlapping_flies_is_ambiguous():
    ov = list(range(30, 40))
    A = traj({g: (50, 50, 210) for g in range(40)})
    B = traj({g: (53, 50, 210) for g in range(40)})     # 3 px apart: position cannot decide
    nA = traj({g: (50, 50, 210) for g in ov})
    nB = traj({g: (53, 50, 210) for g in ov})
    st = stitch((A, B), (nB, nA), ov, BL)
    assert st.ambiguous and "margin" in st.reason


def test_stitch_without_common_samples_extrapolates_constant_velocity():
    # the global flies end at g=39; the new chunk's first observation is g=44 (acquisition lag)
    A = traj({g: (20 + 2 * g, 20, 210) for g in range(40)})      # 2 px/sample to the right
    B = traj({g: (20 + 2 * g, 100, 210) for g in range(40)})
    nA = traj({g: (20 + 2 * g, 20, 210) for g in range(44, 50)})
    nB = traj({g: (20 + 2 * g, 100, 210) for g in range(44, 50)})
    st = stitch((A, B), (nB, nA), list(range(30, 40)), BL)       # overlap samples exist in A/B but not in n*
    assert st.perm == (1, 0) and not st.ambiguous
    assert st.cost < 0.1                                          # extrapolated 5 samples ahead, near-exact


def test_stitch_area_is_a_tiebreak_between_equidistant_candidates():
    ov = list(range(10))
    A = traj({g: (50, 50, 300) for g in range(10)})      # a big fly
    B = traj({g: (70, 50, 150) for g in range(10)})      # a small fly
    nA = traj({g: (60, 50, 150) for g in ov})            # equidistant (10 px) from both; small
    nB = traj({g: (60, 50, 300) for g in ov})            # equidistant; big
    st = stitch((A, B), (nA, nB), ov, BL)
    assert st.perm == (1, 0)


def test_slot_chunk_binds_ids_persistently_and_ignores_a_third_object():
    objs = []
    for g in range(100):
        objs.append(obj(g, "c0:7", 20 + g * 0.5, 30))
        objs.append(obj(g, "c0:3", 100, 100 - g * 0.2))
        if 40 <= g < 60:
            objs.append(obj(g, "c0:9", 60, 60, area=40.0))        # sliver blob, much smaller than a fly
    slots, assigned = slot_chunk(objs, 0, 100, BL, 210.0)
    assert slots[0].pts[0][:2] == (20, 30) or slots[1].pts[0][:2] == (20, 30)
    s7 = 0 if slots[0].pts[0][:2] == (20, 30) else 1
    assert all(assigned[g][s7]["track"] == "c0:7" for g in range(100))
    assert all(assigned[g][1 - s7]["track"] == "c0:3" for g in range(100))


def test_slot_chunk_rebinds_a_fragmented_track_by_continuity():
    # fly 1 loses its SAM id at g=50 and comes back as a new id near where it was
    objs = []
    for g in range(100):
        objs.append(obj(g, "c0:0" if g < 50 else "c0:5", 20 + g * 0.5, 30))
        objs.append(obj(g, "c0:1", 100, 100))
    slots, assigned = slot_chunk(objs, 0, 100, BL, 210.0)
    s = next(k for k in (0, 1) if assigned[0][k]["track"] == "c0:0")
    assert assigned[75][s]["track"] == "c0:5" and assigned[75][1 - s]["track"] == "c0:1"


def test_unknown_when_within_one_body_length_never_when_absent():
    a = np.array([[0.0, 0.0], [0.0, 0.0], [np.nan, np.nan], [0.0, 0.0]])
    b = np.array([[10.0, 0.0], [40.0, 0.0], [5.0, 0.0], [26.9, 0.0]])
    assert unknown_mask(a, b, BL).tolist() == [True, False, False, True]


def test_suspect_swaps_flags_an_exchange_across_contact():
    n = 60
    a = np.c_[np.linspace(0, 100, n), np.full(n, 50.0)]
    b = np.c_[np.linspace(100, 0, n), np.full(n, 60.0)]   # the flies pass each other mid-way
    unk = unknown_mask(a, b, BL)
    runs, sus = suspect_swaps(a, b, unk)
    assert runs == 1 and sus == 0                          # straight through: kept pairing is right
    a2, b2 = a.copy(), b.copy()
    a2[30:], b2[30:] = b[30:].copy(), a[30:].copy()        # SAM swapped ids during the pass
    runs, sus = suspect_swaps(a2, b2, unknown_mask(a2, b2, BL))
    assert sus == 1
