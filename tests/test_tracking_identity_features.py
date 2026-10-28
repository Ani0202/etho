import numpy as np

from etho.tracking import features as F
from etho.tracking.identity import assign_identities, role_at
from etho.tracking.pipeline import Chunk, assemble


def obj(frame, track, inten, area, box, cx=None, cy=None):
    cx = (box[0] + box[2]) / 2 if cx is None else cx
    cy = (box[1] + box[3]) / 2 if cy is None else cy
    return {"frame": frame, "track": track, "intensity": inten, "area": area, "box": box, "cx": cx, "cy": cy,
            "angle": 0.0, "elong": 2.0, "head_x": cx + 10, "head_y": cy}


def scene(n=100):
    objs = []
    for f in range(n):
        objs.append(obj(f, "c0:1", 48, 16000, (100, 200, 300, 400)))           # black resident
        objs.append(obj(f, "c0:0", 150, 9000, (500, 200, 650, 350)))           # white intruder
        if f >= 10:
            objs.append(obj(f, "c0:2", 68, 5000, (120, 180, 200, 260)))        # implant on resident's head
        if 40 <= f < 55:
            objs.append(obj(f, "c0:4", 64, 1500, (60, 10, 190, 70)))           # wall reflection
    return objs


def test_roles_resident_intruder_part_unknown():
    res = assign_identities(scene(), 100)
    assert res.identity_by == "intensity"
    assert res.roles == {"c0:1": "resident", "c0:0": "intruder", "c0:2": "resident:part", "c0:4": "unknown"}


def test_relabel_run_catches_a_silent_id_swap():
    objs = scene()
    for o in objs:  # SAM silently swaps: track c0:1 looks white for frames 60..69
        if o["track"] == "c0:1" and 60 <= o["frame"] < 70:
            o["intensity"] = 150
    res = assign_identities(objs, 100)
    assert role_at(res, "c0:1", 65) == "intruder" and role_at(res, "c0:1", 75) == "resident"


def test_short_flicker_is_not_relabeled():
    objs = scene()
    for o in objs:
        if o["track"] == "c0:1" and 60 <= o["frame"] < 63:
            o["intensity"] = 150
    assert role_at(assign_identities(objs, 100), "c0:1", 61) == "resident"


def test_lookalike_animals_fall_back_to_persistence():
    objs = [o for o in scene() if o["track"] in ("c0:0", "c0:1")]
    for o in objs:
        o["intensity"] = 60 if o["track"] == "c0:1" else 70
    res = assign_identities(objs, 100)
    assert res.identity_by == "persistence" and sorted(res.roles.values()) == ["intruder", "resident"]


def test_mask_gap_and_point_distance():
    a = np.ones((10, 10), np.uint8)
    b = np.ones((10, 10), np.uint8)
    assert F.mask_gap(a, 0, 0, b, 20, 0) == 11.0       # columns 0..9 vs 20..29
    assert F.mask_gap(a, 0, 0, b, 5, 5) == 0.0          # overlap
    assert F.point_to_mask(0, 0, b, 20, 0) == 20.0


def test_head_point_is_opposite_the_tail():
    m = np.zeros((40, 200), np.uint8)
    m[10:30, 0:80] = 1        # body
    m[19:21, 80:200] = 1      # long thin tail to the right
    ys, xs = np.nonzero(m)
    cx, cy = xs.mean(), ys.mean()
    hx, _ = F.head_point(m, 0, 0, cx, cy, 0.0)
    assert hx < 20            # head at the left end


def test_assemble_builds_pair_features():
    objs = scene(30)
    pairs = [{"frame": f, "a": "c0:1", "b": "c0:0", "gap": 200.0, "a_head_to_b": 190.0, "a_center_to_b": 250.0}
             for f in range(30)]
    pairs += [{"frame": f, "a": "c0:2", "b": "c0:0", "a_head_to_b": 290.0, "a_center_to_b": 300.0}
              for f in range(10, 30)]
    pairs += [{"frame": f, "a": "c0:0", "b": "c0:1", "a_head_to_b": 180.0, "a_center_to_b": 260.0}
              for f in range(30)]
    t, info = assemble([Chunk(0, None, 0, 30)], [{"objs": objs, "pairs": pairs}], 30, 10.0)
    assert t["r_present"].sum() == 30 and t["i_present"].sum() == 30
    assert t["dist"][5] == np.hypot(200 - 575, 300 - 275)
    assert t["gap"][5] == 200.0
    assert t["r_head_to_i"][5] == 190.0 and t["r_head_to_i"][15] == 300.0   # implant marks the head from f10
    assert t["r_head_x"][15] == 160.0                                        # implant centroid
    assert np.isfinite(t["r_speed"]).all() and info[0]["identity_by"] == "intensity"
