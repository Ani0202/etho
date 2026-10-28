"""Identity for identical flies: slotting inside a chunk, Hungarian stitching across overlaps, unknown when close."""

import numpy as np

from etho.tracking.fly_identity import (
    MIN_SAMPLES,
    NEUTRAL_COST,
    Traj,
    major_len,
    relabel_swaps,
    slot_chunk,
    stitch,
    suspect_swaps,
    traj_from_rows,
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


def test_stitch_slot_with_no_data_gets_a_neutral_cost_and_is_ambiguous():
    ov = list(range(30, 40))
    A = traj({g: (20 + g, 20, 210) for g in range(40)})
    B = Traj()                                              # this global fly was never observed
    nA = traj({g: (20 + g, 20, 210) for g in ov})
    nB = traj({g: (100, 100, 210) for g in ov})
    st = stitch((A, B), (nB, nA), ov, BL)
    assert st.perm == (1, 0)                                # the observed fly still finds its track
    assert st.ambiguous and "no data" in st.reason
    assert st.alt_cost >= NEUTRAL_COST                      # an empty slot is never a free match
    # both global slots empty: nothing can be decided
    st = stitch((Traj(), Traj()), (nA, nB), ov, BL)
    assert st.ambiguous and st.n_compared == 0


def test_slot_chunk_keeps_short_tracks_but_drops_slivers_below_five_samples():
    objs = []
    for g in range(100):
        objs.append(obj(g, "c0:0", 20 + g * 0.5, 30))
        objs.append(obj(g, "c0:1", 100, 100))
    for g in range(60, 60 + MIN_SAMPLES - 1):
        objs.append(obj(g, "c0:8", 60, 60))                  # 4 samples: a sliver
    _, assigned = slot_chunk(objs, 0, 100, BL, 210.0)
    assert all(o["track"] != "c0:8" for r in assigned.values() for o in r if o)
    # a fly that is only found for 6 samples of a 1800-sample chunk (SAM lost it) still counts
    objs2 = [obj(g, "c0:0", 20, 30) for g in range(1800)] + [obj(g, "c0:1", 100, 100) for g in range(1700, 1706)]
    _, assigned2 = slot_chunk(objs2, 0, 1800, BL, 210.0)
    assert any(o and o["track"] == "c0:1" for o in assigned2[1703])


def test_spurious_object_in_a_free_slot_does_not_displace_the_real_fly_when_it_returns():
    objs = []
    for g in range(100):
        if not 40 <= g < 60:                                 # the real fly 0 is lost for 20 samples
            objs.append(obj(g, "c0:0", 20 + g * 0.5, 30))
        objs.append(obj(g, "c0:1", 100, 100))
        if g >= 45:
            objs.append(obj(g, "c0:9", 60, 35))              # a spurious object appears near where fly 0 was
    slots, assigned = slot_chunk(objs, 0, 100, BL, 210.0)
    s0 = next(k for k in (0, 1) if assigned[10][k]["track"] == "c0:0")
    assert assigned[50][s0]["track"] == "c0:9"               # it may hold the free slot while fly 0 is gone
    assert assigned[70][s0]["track"] == "c0:0"               # but the longer-lived track wins the slot back
    assert assigned[70][1 - s0]["track"] == "c0:1"
    assert traj_from_rows(assigned, s0).pts[70][0] == 20 + 70 * 0.5


def test_relabel_swaps_exchanges_the_rest_of_the_chunk_after_a_silent_sam_swap():
    n = 100
    rows = {}
    for g in range(n):
        f1 = {"track": "c0:0", "cx": 20 + g, "cy": 60.0, "area": 210.0}       # fly 1 moves right
        f2 = {"track": "c0:1", "cx": 100 - g, "cy": 70.0, "area": 210.0}      # fly 2 moves left, 10 px lower
        rows[g] = [f1, f2] if g < 40 else [dict(f2, track="c0:0"), dict(f1, track="c0:1")]   # SAM swaps ids at 40
    bumps = relabel_swaps(rows, 0, n, BL)
    assert len(bumps) == 1 and 50 <= bumps[0] <= 56
    for g in list(range(0, 27)) + list(range(bumps[0], n)):                   # everywhere outside the contact run
        assert rows[g][0]["cx"] == 20 + g and rows[g][1]["cx"] == 100 - g, g


def test_relabel_swaps_leaves_a_clean_pass_and_an_undecidable_one_alone():
    n = 100
    clean = {g: [{"track": "0", "cx": 20.0 + g, "cy": 60.0, "area": 210.0},
                 {"track": "1", "cx": 100.0 - g, "cy": 70.0, "area": 210.0}] for g in range(n)}
    assert relabel_swaps(clean, 0, n, BL) == []                               # passed through: kept pairing fits
    # flies 3 px apart for a while and then part: position cannot say which is which -> no relabel
    near = {g: [{"track": "0", "cx": 50.0, "cy": 60.0, "area": 210.0},
                {"track": "1", "cx": 53.0, "cy": 60.0, "area": 210.0}] for g in range(n)}
    assert relabel_swaps(near, 0, n, BL) == []
