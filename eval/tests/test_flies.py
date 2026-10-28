"""Tests for eval/flies_extract.py and eval/flies_score.py. Synthetic .mat files (scipy.io.savemat), no network.

The last tests use the real dataset and are skipped when data/flyvsfly is absent. They never parse a test
movie (the loaders' seal is what they check)."""

import json
from pathlib import Path

import numpy as np
import pytest
import scipy.io as sio

from eval import flies_extract as fx
from eval import flies_score as fs
from eval import score

T = fx.N_FRAMES


def _cell(items, shape):
    c = np.empty(shape, dtype=object)
    for i, x in enumerate(items):
        c.flat[i] = x
    return c


def write_actions(path: Path, bouts: dict, behs=fx.ACTION_BEHS):
    """bouts: {(fly, beh): [(s, e, flag), ...]} in MATLAB 1-based inclusive frames."""
    cells = np.empty((2, len(behs)), dtype=object)
    for f in range(2):
        for j, b in enumerate(behs):
            rows = bouts.get((f, b), [])
            cells[f, j] = np.array(rows, dtype=np.uint16).reshape(-1, 3) if rows else np.zeros((0, 3), np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    sio.savemat(str(path), {"behs": _cell(list(behs), (1, len(behs))), "bouts": cells})


def write_track(path: Path, xy: np.ndarray, major: float = 20.0):
    """xy: [2, T, 2] GT fly positions."""
    data = np.zeros((2, xy.shape[1], len(fx.TRACK_FIELDS)))
    data[:, :, 0], data[:, :, 1], data[:, :, 3] = xy[..., 0], xy[..., 1], major
    trk = {"flag_frames": np.zeros((1, xy.shape[1]), np.uint8),
           "names": _cell(list(fx.TRACK_FIELDS), (1, len(fx.TRACK_FIELDS))), "data": data}
    sio.savemat(str(path), {"trk": trk})


def write_traintest(root: Path, train=(1, 2, 3, 4, 5), test=(6, 7, 8, 9, 10)):
    root.mkdir(parents=True, exist_ok=True)
    names = [f"movie{i}" for i in range(1, 11)]
    sio.savemat(str(root / "traintest.mat"), {
        "behs": _cell(list(fx.ACTION_BEHS[:-1]), (1, 5)), "movienames": _cell(names, (1, 10)),
        "trainids": np.array([train], np.uint8), "testids": np.array([test], np.uint8),
        "n_flies": np.array([[2]], np.uint8), "movie_ext": np.array(["*.mp4"])})


def static_xy(n=T):
    xy = np.zeros((2, n, 2))
    xy[0] = (30.0, 30.0)
    xy[1] = (100.0, 100.0)
    return xy


# --------------------------------------------------------------------------- extract

def test_parse_actions_index_convention(tmp_path):
    p = tmp_path / "a.mat"
    write_actions(p, {(0, "lunge"): [(101, 104, 0)], (1, "charge"): [(50, 50, 2)],
                      (1, "wing_threat"): [(1, 3, 0), (T - 1, T, 0)]})
    a = fx.parse_actions(sio.loadmat(str(p)))
    assert a["behs"] == list(fx.ACTION_BEHS)
    assert a["bouts"]["lunge"][0].tolist() == [[100, 104]]  # MATLAB [101, 104] inclusive = 4 frames
    assert a["bouts"]["charge"][1].tolist() == [[49, 50]]  # start == end -> one frame
    assert a["flags"]["charge"][1].tolist() == [2]
    m = fx.masks_from_actions(a)
    assert m["lunge"][0].sum() == 4 and m["lunge"][0, 100] and not m["lunge"][0, 99] and not m["lunge"][0, 104]
    assert m["wing_threat"][1, 0] and m["wing_threat"][1, T - 1]


def test_parse_actions_rejects_bad_bouts(tmp_path):
    p = tmp_path / "a.mat"
    write_actions(p, {(0, "lunge"): [(10, 5, 0)]})
    with pytest.raises(ValueError, match="invalid"):
        fx.parse_actions(sio.loadmat(str(p)))
    write_actions(p, {(0, "lunge"): [(0, 5, 0)]})  # 0 is not a MATLAB frame number
    with pytest.raises(ValueError, match="invalid"):
        fx.parse_actions(sio.loadmat(str(p)))


def test_window_labels_pair_union_unknown_and_clipping(tmp_path):
    p = tmp_path / "a.mat"
    s0 = fx.WINDOWS["mid5"][0]
    write_actions(p, {(0, "wing_threat"): [(s0 - 9, s0 + 10, 0)],  # straddles the window start
                      (1, "wing_threat"): [(s0 + 101, s0 + 110, 0)],
                      (0, "lunge"): [(s0 + 201, s0 + 204, 0)], (1, "lunge"): [(s0 + 203, s0 + 206, 0)],
                      (1, "unknown"): [(s0 + 301, s0 + 320, 0)], (0, "unknown"): [(s0 + 311, s0 + 330, 0)]})
    L = fx.window_labels(fx.parse_actions(sio.loadmat(str(p))), fx.WINDOWS["mid5"])
    wt, lu = L["pair"]["wing_threat"], L["pair"]["lunge"]
    assert len(wt) == 9000 and wt[:10].all() and not wt[10]  # clipped at the window start
    assert wt[100:110].all() and wt.sum() == 20
    assert fx.runs(lu).tolist() == [[200, 206]]  # union of overlapping flies' bouts is one pair bout
    assert L["per_fly"]["lunge"][0].sum() == 4 and L["per_fly"]["lunge"][1].sum() == 4
    assert L["excluded"].sum() == 30 and L["excluded"][300] and L["excluded"][329] and not L["excluded"][330]


def test_seal_refuses_test_movies_before_touching_files(tmp_path):
    for m in fx.TEST_MOVIES:
        with pytest.raises(fx.TestSealed):
            fx.load_actions(m, tmp_path)
        with pytest.raises(fx.TestSealed):
            fx.load_gt_tracks(m, tmp_path)
        with pytest.raises(fx.TestSealed):
            fx.load_window_labels(m, "mid10", tmp_path)
    with pytest.raises(ValueError):
        fx.load_actions(11, tmp_path)


def test_verify_split(tmp_path):
    write_traintest(tmp_path)
    assert fx.verify_split(tmp_path)["test"] == [6, 7, 8, 9, 10]
    write_traintest(tmp_path, train=(1, 2, 3, 4, 6), test=(5, 7, 8, 9, 10))
    with pytest.raises(ValueError):
        fx.verify_split(tmp_path)


def test_track_parse(tmp_path):
    p = tmp_path / "t.mat"
    write_track(p, static_xy(100))
    tr = fx.parse_track(sio.loadmat(str(p)))
    assert tr["x"].shape == (2, 100) and tr["x"][1, 5] == 100.0 and tr["major_axis"][0, 0] == 20.0


def test_reference_iid_always_positive_and_bout_placement():
    g = {1: np.zeros(1000, bool), 2: np.zeros(1000, bool)}
    for s in range(100, 900, 100):
        g[1][s:s + 50] = True  # 8 bouts of 50 frames
    g[2][500:510] = True
    keep = {m: np.ones(1000, bool) for m in g}
    r = fx.random_reference(g, keep, reps=50, dev_prior=0.1)
    p = 410 / 2000
    assert r["prevalence"] == pytest.approx(p) == r["iid_expected_f1"]
    assert abs(r["iid_sim_mean_f1"] - p) < 0.03
    assert r["always_positive_f1"] == pytest.approx(2 * p / (1 + p))
    assert r["dev_prior_expected_f1"] == pytest.approx(2 * p * 0.1 / (p + 0.1))
    # placement preserves count, durations and prevalence exactly when nothing is excluded
    assert r["oracle_bout_placement_realized"]["mean_observable_bouts"] == 9
    assert r["oracle_bout_placement_realized"]["mean_kept_prevalence"] == pytest.approx(p)
    assert 0 <= r["oracle_bout_placement_mean_bout_f1"] <= 1


def test_place_bouts_never_merges():
    rng = np.random.default_rng(0)
    for _ in range(200):
        m = fx.place_bouts(np.array([4, 4, 4, 30, 1]), 60, rng)
        assert m.sum() == 43 and len(fx.runs(m)) == 5


# --------------------------------------------------------------------------- scorer


def test_hold_samples_nearest_rule():
    x = np.array([0, 1, 0], bool)
    assert fs.hold_samples(x, 9, 3).tolist() == [0, 0, 1, 1, 1, 0, 0, 0, 0]  # sample 1 covers frames 2..4
    with pytest.raises(ValueError):
        fs.hold_samples(np.zeros(4, bool), 9, 3)


def _pred_npz(path, window, fps=30, **arrays):
    W = window[1] - window[0]
    K = -(-W // (30 // fps))
    d = {"window": np.array(window), "sample_fps": np.array(fps)}
    for b in fx.SCORED:
        d[b] = arrays.pop(b, np.zeros(K, np.uint8))
    d.update(arrays)
    np.savez(path, **d)


def test_load_prediction_validation(tmp_path):
    w = fx.WINDOWS["mid10"]
    p = tmp_path / "movie1.npz"
    _pred_npz(p, w, fps=10)
    out = fs.load_prediction(p, w, fx.SCORED)
    assert out["n_samples"] == 6000 and len(out["pair"]["lunge"]) == 18000
    assert out["sample_frames"][1] == 18003
    _pred_npz(p, fx.WINDOWS["mid5"], fps=10)
    with pytest.raises(ValueError, match="window"):
        fs.load_prediction(p, w, fx.SCORED)
    _pred_npz(p, w, fps=10, lunge=np.zeros(5999, np.uint8))
    with pytest.raises(ValueError, match="exactly"):
        fs.load_prediction(p, w, fx.SCORED)
    _pred_npz(p, w, fps=10, lunge=np.full(6000, 2, np.uint8))
    with pytest.raises(ValueError, match="0/1"):
        fs.load_prediction(p, w, fx.SCORED)
    np.savez(p, window=np.array(w), sample_fps=np.array(10), wing_threat=np.zeros(6000, np.uint8))
    with pytest.raises(ValueError, match="lunge"):
        fs.load_prediction(p, w, fx.SCORED)
    assert fs.load_prediction(p, w, ("wing_threat",))["n_samples"] == 6000


def _gt(W, wt=(), lu=(), excl=(), fly=0):
    per_fly = {b: np.zeros((2, W), bool) for b in fx.SCORED}
    for s, e in wt:
        per_fly["wing_threat"][fly, s:e] = True
    for s, e in lu:
        per_fly["lunge"][fly, s:e] = True
    ex = np.zeros(W, bool)
    for s, e in excl:
        ex[s:e] = True
    return {"per_fly": per_fly, "pair": {b: per_fly[b].any(axis=0) for b in fx.SCORED}, "excluded": ex}


def _pred(W, pair=None, by_track=None, xy=None):
    p = {"pair": pair or {b: np.zeros(W, bool) for b in fx.SCORED}, "by_track": by_track or {},
         "sample_frames": np.arange(W)}
    if xy is not None:
        p["track_xy"] = xy
    return p




def test_score_movies_perfect_empty_and_exclusion():
    W = 1000
    g = {1: _gt(W, wt=[(100, 200)], lu=[(300, 304), (400, 404)], excl=[(500, 600)]),
         2: _gt(W, lu=[(10, 14)])}
    perfect = {m: _pred(W, {b: g[m]["pair"][b].copy() for b in fx.SCORED}) for m in g}
    perfect[1]["pair"]["lunge"][550:560] = True  # inside the excluded range: must not count
    r = fs.score_movies(g, perfect, fx.SCORED, reps=200, ref_reps=20, shift_reps=50)
    assert r["n_frames_excluded"] == 100
    lu = r["behaviors"]["lunge"]
    assert r["behaviors"]["wing_threat"]["frame"]["f1"] == 1.0 and lu["frame"]["f1"] == 1.0
    assert lu["bouts"]["any"]["recall"] == 1.0 and lu["bouts"]["any"]["n_pred"] == 3
    assert lu["false_positive_bouts_per_min"] == 0.0
    assert r["behaviors"]["wing_threat"]["movies_with_behavior"] == [1]
    assert r["behaviors"]["wing_threat"]["negative_movies"] == [2]
    assert lu["frame_ci95"]["f1"] == [1.0, 1.0] and lu["frame_ci95"]["informative"]
    assert lu["circular_shift_test"]["p_value"] < 0.1
    assert set(lu["leave_one_movie_out_f1"]) == {"movie1", "movie2"}
    empty = {m: _pred(W) for m in g}
    r0 = fs.score_movies(g, empty, fx.SCORED, reps=200, ref_reps=20, shift_reps=0)
    assert r0["behaviors"]["lunge"]["frame"]["f1"] == 0.0 and r0["identity"].get("skipped")
    assert r0["behaviors"]["lunge"]["random_reference"]["prevalence"] == pytest.approx(12 / 1900)


def test_one_positive_movie_ci_is_flagged_uninformative():
    # Codex: a perfect predictor on one positive + one empty movie gets CI [1, 1] after dropping ~25% of
    # resamples. The CI must be marked uninformative, so a gate cannot pass on it.
    W = 1000
    g = {1: _gt(W, wt=[(100, 200)]), 2: _gt(W)}
    perfect = {m: _pred(W, {b: g[m]["pair"][b].copy() for b in fx.SCORED}) for m in g}
    ci = fs.score_movies(g, perfect, ("wing_threat",), reps=2000, ref_reps=5, shift_reps=0)[
        "behaviors"]["wing_threat"]["frame_ci95"]
    assert ci["f1"] == [1.0, 1.0] and ci["f1_undefined_frac"] > 0.2 and not ci["informative"]
    chk = fs._rel_check(1.0, ci, 0.05, {"point_min": {"times_ref": 2, "plus_ref": 0.15}, "ci_lower_gt": "ref"})
    assert not chk["pass"]


def test_bout_matching_one_to_one_max_cardinality_and_censoring():
    keep = np.ones(100, bool)
    gt = np.zeros(100, bool)
    for s in (10, 30, 50, 70):
        gt[s:s + 4] = True
    giant = np.zeros(100, bool)
    giant[5:80] = True
    assert fs.fly_bout_counts(gt, giant, keep, "any").tolist() == [4, 1, 1]  # one pred bout -> one match
    touch = np.zeros(100, bool)
    touch[13] = True
    assert fs.fly_bout_counts(gt, touch, keep, "any")[2] == 1  # one shared frame is enough
    # G1 = [0,2), G2 = [3,10); P1 = [1,9) overlaps both (IoU 0.11 with G1, 0.67 with G2); P2 = [9,12) only G2.
    # Greedy-by-IoU (eval/score.py, CalMS21) takes P1-G2 first and matches 1; maximum cardinality matches 2.
    # (P1 and P2 touch, so as masks they would merge into one bout: test both matchers on explicit runs.)
    g_runs, p_runs = np.array([[0, 2], [3, 10]]), np.array([[1, 9], [9, 12]])
    assert score.match_bouts(g_runs, p_runs, 1e-12) == 1
    elig = np.array([[True, False], [True, True]])  # rows G1, G2; cols P1, P2
    iou = np.array([[1 / 9, 0.0], [6 / 9, 1 / 9]])
    assert fs.max_card_match(elig, iou) == 2
    # the same configuration with a 1-frame gap between P1 and P2, through the mask interface
    g5, p5 = np.zeros(20, bool), np.zeros(20, bool)
    g5[0:2] = g5[3:11] = True
    p5[1:9] = p5[10:13] = True
    assert score.match_bouts(fx.runs(g5), fx.runs(p5), 1e-12) == 1
    assert fs.fly_bout_counts(g5, p5, np.ones(20, bool), "any").tolist() == [2, 2, 2]
    # censoring: an unknown interval inside a GT bout does not split it into two events
    g3 = np.zeros(50, bool)
    g3[10:30] = True
    k3 = np.ones(50, bool)
    k3[18:22] = False
    assert fs.fly_bout_counts(g3, g3, k3, "any").tolist() == [1, 1, 1]
    assert fs.fly_bout_counts(g3, g3, k3, 0.5).tolist() == [1, 1, 1]
    k4 = np.ones(50, bool)
    k4[10:30] = False  # a bout entirely inside excluded frames is dropped
    assert fs.fly_bout_counts(g3, g3, k4, "any").tolist() == [0, 0, 0]


def test_by_track_must_agree_with_pair(tmp_path):
    w = fx.WINDOWS["mid5"]
    p = tmp_path / "movie1.npz"
    lu = np.zeros(9000, np.uint8)
    lu[10:14] = 1
    bt = np.zeros((2, 9000), np.uint8)
    bt[0, 9] = 1  # actor named where the pair label is 0
    _pred_npz(p, w, lunge=lu, lunge_by_track=bt)
    with pytest.raises(ValueError, match="actor"):
        fs.load_prediction(p, w, fx.SCORED)
    bt[0, 9] = 0
    bt[0, 10:12] = 1  # frames 12-13 positive with no actor: unassigned, allowed and counted
    _pred_npz(p, w, lunge=lu, lunge_by_track=bt)
    assert fs.load_prediction(p, w, fx.SCORED)["unassigned_samples"]["lunge"] == 2


def test_identity_swap_flip_localization_and_per_fly():
    W = 600
    xy_gt = static_xy(W)
    gt_tr = {"x": xy_gt[..., 0], "y": xy_gt[..., 1], "major_axis": np.full((2, W), 20.0)}
    e = np.zeros((W, 2, 2))
    e[:, 0] = (101.0, 99.0)  # Etho track 0 sits on GT fly 1: the window permutation is a swap
    e[:, 1] = (31.0, 29.0)
    e[300, 0], e[300, 1] = (31.0, 29.0), (101.0, 99.0)  # one-sample flip
    e[400:430, 1] = np.nan  # track 1 missing
    e[450:460, 1] = (101.0, 99.0)  # duplicate: both tracks on GT fly 1 -> present but not localized
    unk = np.zeros(W, bool)
    unk[500:530] = True
    idm = fs.identity_metrics(np.arange(W), e, unk, gt_tr, window_minutes=W / 30 / 60)
    assert idm["permutation"] == "swap"
    assert idm["n_switches"] == 4 and idm["n_error_episodes"] == 2  # the flip + the duplicate stretch
    assert idm["pct_unknown_or_missing"] == pytest.approx(60 / W)
    assert idm["both_tracks_present_frac"] == pytest.approx(1 - 30 / W)
    assert idm["both_localized_frac"] == pytest.approx(1 - 40 / W)
    ep = np.zeros(W)
    ep[300:] = 1  # if Etho had declared a new epoch at the flip, the per-epoch diagnostic would not count it
    ep[301:] = 2
    idm2 = fs.identity_metrics(np.arange(W), e, unk, gt_tr, W / 1800, id_epoch=ep)
    assert idm2["n_epochs"] == 3 and idm2["identity_accuracy_per_epoch_permutation"] > idm["identity_accuracy"]
    assert idm2["identity_accuracy"] == idm["identity_accuracy"]
    g = {1: _gt(W, wt=[(100, 200)], fly=1)}
    bt = {b: np.zeros((2, W), bool) for b in fx.SCORED}
    bt["wing_threat"][0, 100:200] = True
    pr = {1: _pred(W, {b: bt[b].any(axis=0) for b in fx.SCORED}, by_track=bt, xy=e)}
    pr[1]["identity_unknown"] = unk
    r = fs.score_movies(g, pr, fx.SCORED, reps=100, gt_tracks={1: gt_tr}, window_minutes=W / 1800, ref_reps=10,
                        shift_reps=0)
    wt = r["behaviors"]["wing_threat"]
    assert wt["per_fly_frame"]["f1"] == 1.0 and wt["per_fly_baseline_persistent_actor"]["f1"] == 1.0
    assert wt["per_fly_baseline_copy_to_both"]["precision"] == 0.5
    assert r["identity"]["pooled"]["both_localized_frac_during_wing_threat"] == 1.0


# --------------------------------------------------------------------------- decision tree, end to end

def make_dataset(root: Path, movies=tuple(range(1, 11))):
    write_traintest(root)
    s0 = fx.WINDOWS["mid5"][0]
    for m in movies:
        d = root / f"movie{m}"
        lunges = [(s0 + 101 + 40 * i, s0 + 104 + 40 * i, 0) for i in range(20)]
        wts = [(s0 + 2001 + 400 * i, s0 + 2100 + 400 * i, 0) for i in range(5)]
        write_actions(d / f"movie{m}_actions.mat", {(0, "lunge"): lunges, (1, "wing_threat"): wts})
        write_track(d / f"movie{m}_track.mat", static_xy())
        for ext in (".mp4", "_feat.mat"):
            (d / f"movie{m}{ext}").write_bytes(b"x")
    return root


def perfect_preds(root: Path, out: Path, movies, window, fps, frozen=None, behaviors=fx.SCORED):
    out.mkdir(parents=True, exist_ok=True)
    w = fx.WINDOWS[window]
    step = 30 // fps
    for m in movies:
        L = fx.load_window_labels(m, window, root, allow_test=True)  # synthetic data only
        K = -(-(w[1] - w[0]) // step)
        xy = np.zeros((K, 2, 2))
        xy[:, 0], xy[:, 1] = (30.0, 30.0), (100.0, 100.0)
        d = {"window": np.array(w), "sample_fps": np.array(fps), "track_xy": xy}
        for b in behaviors:
            d[b] = L["pair"][b][::step].astype(np.uint8)
            d[f"{b}_by_track"] = L["per_fly"][b][:, ::step].astype(np.uint8)
        np.savez(out / f"movie{m}.npz", **d)
    cfg = {"name": "synthetic", "run_commit": "deadbeef"}
    if frozen is not None:
        cfg["frozen_sha256"] = fx.common.sha256_file(frozen)
    (out / "manifest.json").write_text(json.dumps({"config": cfg, "cost": {"usd": 0}}))
    return out


def frozen_setup(tmp_path: Path):
    plan = tmp_path / "plan.md"
    plan.write_text("FLIES-PLAN-STATUS: FROZEN")
    asset = tmp_path / "asset.txt"
    asset.write_text("x")
    a = {"path": str(asset), "sha256": fx.common.sha256_file(asset)}
    defs = {"path": fs.DEFINITIONS_PATH, "sha256": fx.common.sha256_file(Path(fs.DEFINITIONS_PATH))}
    fc = tmp_path / "frozen.json"
    fc.write_text(json.dumps({"name": "x", "frozen_at_commit": "deadbeef", "branches": fs.BRANCHES,
                              "assets": {"definitions": [defs], "detectors": [a], "tracking": [a], "runner": [a]}}))
    return plan, fc


def test_decision_tree_end_to_end(tmp_path):
    root = make_dataset(tmp_path / "data")
    plan, fc = frozen_setup(tmp_path)
    log, dec, out = tmp_path / "runs.jsonl", tmp_path / "decision.json", tmp_path / "out"
    man = tmp_path / "flies_manifest.json"
    man.write_text(json.dumps(fx.file_manifest(root, tmp_path / "none.zip")))
    kw = dict(root=root, log_path=log, out_dir=out, check_git=False, plan_file=plan, frozen_config=fc,
              decision_path=dec, reps=200, ref_reps=5, shift_reps=20, data_manifest=man,
              criteria_path=Path("eval/flies_criteria_v1.json"))
    # wing gate before the lunge decision: refused, nothing logged
    p_train = perfect_preds(root, tmp_path / "p_train", fx.TRAIN_MOVIES, "mid5", 30, fc)
    with pytest.raises(SystemExit, match="lunge decision"):
        fs.run("train", "mid5", p_train, purpose="gate-wing", **kw)
    assert not log.exists()
    # lunge gate at the wrong fps: refused before anything is logged
    p_bad = perfect_preds(root, tmp_path / "p_bad", (1, 2), "mid5", 10, fc)
    with pytest.raises(SystemExit, match="fps"):
        fs.run("dev", "mid5", p_bad, purpose="gate-lunge", **kw)
    assert not log.exists()
    p_dev = perfect_preds(root, tmp_path / "p_dev", (1, 2), "mid5", 30, fc)
    rec = fs.run("dev", "mid5", p_dev, purpose="gate-lunge", **kw)
    assert rec["verdicts"]["lunge"]["decision"] == "GO", rec["verdicts"]
    with pytest.raises(SystemExit, match="already executed"):
        fs.run("dev", "mid5", p_dev, purpose="gate-lunge", **kw)
    doc = fs.decide("lunge", rec["run_id"], log, dec)
    assert doc["branch"] == "GO" and doc["window"] == "mid5" and doc["sample_fps"] == 30
    # GO branch: wing gate must be mid5 with both behaviors
    with pytest.raises(SystemExit, match="branch GO"):
        fs.run("train", "mid10", p_train, purpose="gate-wing", **kw)
    with pytest.raises(SystemExit, match="subset|movies"):
        fs.run("train", "mid5", p_train, purpose="gate-wing", movies=[2, 3, 4], **kw)
    rec_w = fs.run("train", "mid5", p_train, purpose="gate-wing", now_utc="2026-10-30T12:00:00+00:00", **kw)
    assert rec_w["verdicts"]["wing"]["decision"] == "GO", rec_w["verdicts"]
    doc = fs.decide("wing", rec_w["run_id"], log, dec)
    assert doc["test_allowed"] is True
    with pytest.raises(SystemExit, match="already recorded"):
        fs.decide("wing", rec_w["run_id"], log, dec)
    # test: allowed only with the branch's settings
    p_test = perfect_preds(root, tmp_path / "p_test", fx.TEST_MOVIES, "mid5", 30, fc)
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())
    tkw = {**kw, "confirm_test_run": True}
    with pytest.raises(SystemExit, match="branch GO"):
        fs.run("test", "mid5", p_test, behaviors=("wing_threat",), purpose="test-1", **tkw)
    with pytest.raises(SystemExit, match="frozen baseline"):  # F2: the claim needs the baseline next to it
        fs.run("test", "mid5", p_test, purpose="test-1", **tkw)
    bls = {}
    for name in ("wing_proxy", "lunge_speed"):
        bd = tmp_path / f"bl_{name}"
        bd.mkdir()
        for m in fx.TEST_MOVIES:
            d = dict(np.load(p_test / f"movie{m}.npz"))
            if name == "lunge_speed":  # a weaker baseline: half of the lunge samples
                d["lunge"][::2] = 0
                d["lunge_by_track"][:, ::2] = 0
            np.savez(bd / f"movie{m}.npz", **d)
        (bd / "manifest.json").write_text(json.dumps({"config": {"baseline": "wrong", "params_sha256": "x"}}))
        bls[name] = bd
    with pytest.raises(SystemExit, match="not produced with the frozen"):
        fs.run("test", "mid5", p_test, purpose="test-1", baselines=bls, **tkw)
    for name, bd in bls.items():
        (bd / "manifest.json").write_text(json.dumps({"config": {
            "baseline": name, "params_sha256": fx.common.sha256_file(fs.BASELINE_PARAMS)}}))
    assert not [x for x in log.read_text().splitlines() if '"flies-test"' in x]  # refusals logged nothing
    rec_t = fs.run("test", "mid5", p_test, purpose="test-1", baselines=bls, **tkw)
    assert rec_t["baselines"]["wing_proxy"]["files_sha256"]["movie6.npz"]
    assert rec_t["metrics"]["baselines"]["wing_proxy"]["lunge"]["paired_diff_frame_f1_main_minus_baseline"][
        "point"] == 0
    assert "CLAIM lunge: CLAIMABLE" in fs.format_report(rec_t)
    assert crit["status"] == "FROZEN"
    assert "per_movie" not in rec_t["metrics"] and Path(rec_t["sealed_per_movie"]["path"]).exists()
    assert "leave_one_movie_out_f1" not in rec_t["metrics"]["behaviors"]["lunge"]
    v = rec_t["verdicts"]
    assert v["lunge"]["verdict"] == "CLAIMABLE" and v["wing_threat"]["verdict"] == "CLAIMABLE", v
    assert v["lunge"]["baseline"]["name"] == "lunge_speed" and v["lunge"]["baseline"]["frame_f1"] < 1.0
    assert v["wing_threat"]["baseline"]["frame_f1"] == 1.0
    with pytest.raises(SystemExit, match="already executed"):
        fs.run("test", "mid5", p_test, purpose="test-1", **tkw)
    (root / "movie7" / "movie7_track.mat").write_bytes(b"tampered")
    n = len(log.read_text().splitlines())
    with pytest.raises(SystemExit, match="manifest"):
        fs.run("test", "mid5", p_test, purpose="test-2", **tkw)
    assert len(log.read_text().splitlines()) == n


def test_wing_no_go_blocks_the_test_split(tmp_path):
    root = make_dataset(tmp_path / "data")
    plan, fc = frozen_setup(tmp_path)
    log, dec = tmp_path / "runs.jsonl", tmp_path / "decision.json"
    kw = dict(root=root, log_path=log, out_dir=None, check_git=False, plan_file=plan, frozen_config=fc,
              decision_path=dec, reps=100, ref_reps=5, shift_reps=0, criteria_path=Path("eval/flies_criteria_v1.json"))
    empty = perfect_preds(root, tmp_path / "p_dev", (1, 2), "mid5", 30, fc)
    for m in (1, 2):  # an all-zero predictor: lunge NO-GO
        d = dict(np.load(empty / f"movie{m}.npz"))
        for b in fx.SCORED:
            d[b][:] = 0
            d[f"{b}_by_track"][:] = 0
        np.savez(empty / f"movie{m}.npz", **d)
    rec = fs.run("dev", "mid5", empty, purpose="gate-lunge", **kw)
    assert rec["verdicts"]["lunge"]["decision"] == "NO-GO"
    doc = fs.decide("lunge", rec["run_id"], log, dec)
    assert (doc["branch"], doc["window"], doc["sample_fps"], doc["behaviors"]) == ("NO-GO", "mid10", 10,
                                                                                  ["wing_threat"])
    p_train = perfect_preds(root, tmp_path / "p_train", fx.TRAIN_MOVIES, "mid10", 10, fc, ("wing_threat",))
    late = fs.run("train", "mid10", p_train, behaviors=("wing_threat",), purpose="gate-wing",
                  now_utc="2026-11-01T04:00:01+00:00", **kw)
    v = late["verdicts"]["wing"]
    assert v["frame_f1"]["pass"] and not v["before_deadline"] and v["decision"] == "NO-GO"
    doc = fs.decide("wing", late["run_id"], log, dec)
    assert doc["test_allowed"] is False
    p_test = perfect_preds(root, tmp_path / "p_test", fx.TEST_MOVIES, "mid10", 10, fc, ("wing_threat",))
    with pytest.raises(SystemExit, match="NO-GO"):
        fs.run("test", "mid10", p_test, behaviors=("wing_threat",), purpose="test-1", confirm_test_run=True, **kw)


def test_wing_gate_bars():
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())
    ctx = {"split": "train", "partial": False, "window": "mid5", "sample_fps": ["30.0"], "movies": [1, 2, 3, 4, 5]}
    early = "2026-10-30T12:00:00+00:00"

    def metrics(f1, lo, ref, n_b=20, movies=(2, 3, 4), und=0.0):
        return {"behaviors": {"wing_threat": {
            "frame": {"f1": f1}, "frame_ci95": {"f1": [lo, 1.0], "f1_undefined_frac": und,
                                                "informative": und <= fs.CI_UNDEFINED_MAX},
            "random_reference": {"iid_expected_f1": ref}, "bouts": {"any": {"n_gt": n_b}},
            "movies_with_behavior": list(movies)}}}

    def dec(*a, ctx=ctx, branch="GO", **k):
        return fs.evaluate_wing_gate(metrics(*a, **k), crit, ctx, branch, early)
    go = dec(0.40, 0.05, 0.043)  # amendment F2 (ADR-004): F1 >= 0.40 AND CI lower > r
    assert go["frame_f1"]["point_bar"] == pytest.approx(0.40) and go["decision"] == "GO"
    assert dec(0.399, 0.05, 0.043)["decision"] == "NO-GO"
    assert dec(0.20, 0.05, 0.043)["decision"] == "NO-GO"  # passed the superseded max(2r, r + 0.15) bar
    assert dec(0.60, 0.043, 0.043)["decision"] == "NO-GO"  # CI lower bound must be strictly above r
    assert dec(0.60, 0.05, 0.043, und=0.06)["decision"] == "NO-GO"  # uninformative CI
    assert dec(0.50, 0.30, 0.30)["frame_f1"]["point_bar"] == pytest.approx(0.40)  # absolute, not relative
    assert dec(0.5, 0.3, 0.04, movies=(2, 3))["decision"] == "NO-GO"
    assert dec(0.5, 0.3, 0.04, ctx={**ctx, "partial": True})["decision"] == "NO-GO"
    assert dec(0.5, 0.3, 0.04, ctx={**ctx, "window": "mid10"})["decision"] == "NO-GO"  # Codex: mid10@30 got GO
    assert dec(0.5, 0.3, 0.04, ctx={**ctx, "window": "mid10", "sample_fps": ["30.0"]},
               branch="NO-GO")["decision"] == "NO-GO"
    assert dec(0.5, 0.3, 0.04, ctx={**ctx, "window": "mid10", "sample_fps": ["10.0"]},
               branch="NO-GO")["decision"] == "GO"


def test_free_runs_and_refusals(tmp_path):
    root = make_dataset(tmp_path / "data", movies=(1, 2))
    plan, fc = frozen_setup(tmp_path)
    pred = perfect_preds(root, tmp_path / "pred", (1, 2), "mid5", 30)
    log = tmp_path / "runs.jsonl"
    rec = fs.run("dev", "mid5", pred, root=root, log_path=log, out_dir=tmp_path / "out", reps=200, ref_reps=5,
                 shift_reps=10, criteria_path=Path("eval/flies_criteria_v1.json"), check_git=False)
    assert [json.loads(x)["status"] for x in log.read_text().splitlines()] == ["started", "ok"]
    assert rec["split"] == "flies-dev" and rec["verdicts"]["note"].startswith("informational")
    assert rec["metrics"]["behaviors"]["lunge"]["frame"]["f1"] == 1.0
    with pytest.raises(SystemExit, match="controlled purpose"):
        fs.run("dev", "mid5", pred, purpose="gate-x", root=root, log_path=log, out_dir=None, check_git=False)
    with pytest.raises(SystemExit, match="movies"):
        fs.run("dev", "mid5", pred, purpose="gate-lunge", movies=[1], root=root, log_path=log, out_dir=None,
               check_git=False)
    # test split refusals with nothing logged
    tlog = tmp_path / "t.jsonl"
    crit_p = tmp_path / "crit.json"
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())
    kw = dict(root=root, log_path=tlog, out_dir=None, check_git=False, plan_file=plan, criteria_path=crit_p,
              decision_path=tmp_path / "none.json")
    crit_p.write_text(json.dumps({**crit, "status": "PROPOSED"}))
    with pytest.raises(SystemExit, match="confirm"):
        fs.run("test", "mid5", pred, purpose="test-1", **kw)
    with pytest.raises(SystemExit, match="purpose"):
        fs.run("test", "mid5", pred, purpose="tune", confirm_test_run=True, **kw)
    with pytest.raises(SystemExit, match="FROZEN"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, **kw)
    crit_p.write_text(json.dumps(crit))
    plan.write_text(f"... {fs.PROPOSED_MARKER} ...")
    with pytest.raises(SystemExit, match="not frozen"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, **kw)
    plan.write_text("FROZEN")
    with pytest.raises(SystemExit, match="test-2"):
        fs.run("test", "mid5", pred, purpose="test-2", confirm_test_run=True, **kw)
    with pytest.raises(SystemExit, match="lunge decision"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, **kw)
    assert not tlog.exists()


def test_frozen_config_schema(tmp_path):
    _, fc = frozen_setup(tmp_path)
    good = json.loads(fc.read_text())
    man = {"frozen_sha256": fx.common.sha256_file(fc), "run_commit": "deadbeef"}
    assert fs.verify_frozen_flies_config(fc, man, check_git=False)["sha256"] == man["frozen_sha256"]
    for broken, msg in ((dict(good, branches={"GO": {}}), "branches"),
                        (dict(good, assets={**good["assets"], "runner": []}), "runner"),
                        (dict(good, assets={**good["assets"], "definitions": good["assets"]["runner"]}),
                         "definitions")):
        fc.write_text(json.dumps(broken))
        with pytest.raises(SystemExit, match=msg):
            fs.verify_frozen_flies_config(fc, {**man, "frozen_sha256": fx.common.sha256_file(fc)}, check_git=False)
    fc.write_text(json.dumps(good))
    with pytest.raises(SystemExit, match="run_commit"):
        fs.verify_frozen_flies_config(fc, {"frozen_sha256": fx.common.sha256_file(fc)}, check_git=False)
    with pytest.raises(SystemExit, match="uncommitted"):
        fs.verify_frozen_flies_config(fc, {**man, "frozen_sha256": fx.common.sha256_file(fc),
                                           "run_dirty_paths": ["etho/tracking/fly_features.py"]}, check_git=False)


def test_prior_run_accounting(tmp_path):
    log = tmp_path / "runs.jsonl"
    rows = [{"run_id": "a", "split": "flies-test", "purpose": "test-1", "status": "started"},
            {"run_id": "a", "split": "flies-test", "purpose": "test-1", "status": "ok", "labels_loaded": True},
            {"run_id": "c", "split": "test", "purpose": "test-1", "status": "started"}]  # CalMS21: ignored
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    crit = {"status": "FROZEN"}
    plan = tmp_path / "p.md"
    plan.write_text("")
    with pytest.raises(SystemExit, match="already executed"):
        fs.guard_flies_test(True, "test-1", log, False, plan, crit)
    fs.guard_flies_test(True, "test-2", log, False, plan, crit)  # allowed: test-1 exists


# --------------------------------------------------------------------------- real data (skipped if absent)

REAL = fx.DATA_ROOT
needs_data = pytest.mark.skipif(not (REAL / "traintest.mat").exists() or not (REAL / "movie1").exists(),
                                reason="Fly vs. Fly Aggression data not present")


@needs_data
def test_real_split_and_train_movie1_format():
    tt = fx.verify_split(REAL)
    assert tt["n_flies"] == 2 and tt["behs"] == list(fx.ACTION_BEHS[:-1])
    a = fx.load_actions(1, REAL)
    assert a["behs"] == list(fx.ACTION_BEHS)
    assert a["bouts"]["lunge"][0][0].tolist() == [21016, 21020]  # MATLAB [21017, 21020]
    tr = fx.load_gt_tracks(1, REAL)
    assert tr["x"].shape == (2, fx.N_FRAMES)
    L = fx.load_window_labels(2, "mid5", REAL)
    assert len(L["pair"]["wing_threat"]) == 9000


@needs_data
def test_real_test_movies_stay_sealed():
    for m in fx.TEST_MOVIES:
        with pytest.raises(fx.TestSealed):
            fx.load_actions(m, REAL)


@needs_data
def test_real_manifest_matches_if_committed():
    if not fx.MANIFEST.exists():
        pytest.skip("no manifest")
    assert fx.verify_manifest(fx.MANIFEST) == []


def test_claim_verdict_rules():
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())

    def m(f1, lo, ref=0.05, und=0.0, n_b=40, movies=(6, 7, 8), baseline=True):
        blk = {"frame": {"f1": f1}, "frame_ci95": {"f1": [lo, 1.0], "f1_undefined_frac": und,
                                                    "informative": und <= fs.CI_UNDEFINED_MAX},
               "random_reference": {"iid_expected_f1": ref}, "bouts": {"any": {"n_gt": n_b, "f1": 0.5}},
               "movies_with_behavior": list(movies), "circular_shift_test": {"p_value": 0.01}}
        out = {"behaviors": {"wing_threat": blk}}
        if baseline:
            out["baselines"] = {"wing_proxy": {"wing_threat": {"frame": {"f1": 0.7}, "frame_ci95": {"f1": [0.5, 0.9]}}}}
        return fs.evaluate_test_targets(out, crit)["wing_threat"]
    assert m(0.40, 0.06)["verdict"] == "CLAIMABLE"
    assert m(0.40, 0.06)["baseline"]["frame_f1"] == 0.7
    assert m(0.39, 0.06)["verdict"] == "QUALITATIVE"
    assert m(0.60, 0.05)["verdict"] == "QUALITATIVE"  # CI lower bound must be strictly above r
    assert m(0.60, 0.30, und=0.10)["verdict"] == "QUALITATIVE"
    assert m(0.60, 0.30, n_b=9)["verdict"] == "QUALITATIVE"
    assert m(0.60, 0.30, baseline=False)["reasons"] == ["frozen baseline wing_proxy not scored on the same frames"]
