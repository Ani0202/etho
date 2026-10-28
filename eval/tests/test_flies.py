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


def test_random_reference_iid_and_shuffle():
    g = {1: np.zeros(1000, bool), 2: np.zeros(1000, bool)}
    g[1][100:150] = True
    g[2][500:510] = True
    keep = {m: np.ones(1000, bool) for m in g}
    r = fx.random_reference(g, keep, reps=50)
    assert r["prevalence"] == pytest.approx(60 / 2000) == r["iid_expected_f1"]
    assert abs(r["iid_sim_mean_f1"] - 0.03) < 0.02
    assert 0 <= r["bout_shuffle_mean_bout_f1"] <= 1


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
    r = fs.score_movies(g, perfect, fx.SCORED, reps=200, ref_reps=20)
    assert r["n_frames_excluded"] == 100
    assert r["behaviors"]["wing_threat"]["frame"]["f1"] == 1.0
    assert r["behaviors"]["lunge"]["frame"]["f1"] == 1.0
    assert r["behaviors"]["lunge"]["bouts"]["any"]["recall"] == 1.0
    assert r["behaviors"]["lunge"]["bouts"]["any"]["n_pred"] == 3
    assert r["behaviors"]["wing_threat"]["movies_with_behavior"] == [1]
    assert r["behaviors"]["lunge"]["frame_ci95"]["f1"] == [1.0, 1.0]
    empty = {m: _pred(W) for m in g}
    r0 = fs.score_movies(g, empty, fx.SCORED, reps=200, ref_reps=20)
    assert r0["behaviors"]["lunge"]["frame"]["f1"] == 0.0 and r0["identity"].get("skipped")
    assert r0["behaviors"]["lunge"]["random_reference"]["prevalence"] == pytest.approx(12 / 1900)


def test_bout_any_overlap_is_one_to_one():
    gt = np.zeros(100, bool)
    for s in (10, 30, 50, 70):
        gt[s:s + 4] = True
    giant = np.zeros(100, bool)
    giant[5:80] = True
    n_gt, n_pr, m = fs.bout_counts_any_overlap(gt, giant)
    assert (n_gt, n_pr, m) == (4, 1, 1)  # one predicted bout matches at most one GT bout
    touch = np.zeros(100, bool)
    touch[13:14] = True  # one shared frame is enough
    assert fs.bout_counts_any_overlap(gt, touch)[2] == 1


def test_identity_swap_flip_and_per_fly_mapping():
    W = 600
    xy_gt = static_xy(W)
    gt_tr = {"x": xy_gt[..., 0], "y": xy_gt[..., 1], "major_axis": np.full((2, W), 20.0)}
    e = np.zeros((W, 2, 2))
    e[:, 0] = (101.0, 99.0)  # Etho track 0 sits on GT fly 1: the window permutation is a swap
    e[:, 1] = (31.0, 29.0)
    e[300, 0], e[300, 1] = (31.0, 29.0), (101.0, 99.0)  # one-sample flip
    e[400:430, 1] = np.nan  # track 1 missing
    unk = np.zeros(W, bool)
    unk[500:530] = True
    idm = fs.identity_metrics(np.arange(W), e, unk, gt_tr, window_minutes=W / 30 / 60)
    assert idm["permutation"] == "swap"
    assert idm["n_evaluable"] == W - 60
    assert idm["identity_accuracy"] == pytest.approx(1 - 1 / (W - 60))
    assert idm["n_switches"] == 2 and idm["n_error_episodes"] == 1
    assert idm["pct_unknown_or_missing"] == pytest.approx(60 / W)
    assert idm["median_matched_centroid_dist_px"] == pytest.approx(np.sqrt(2))
    # per-fly: GT fly 1 wing-threatens; Etho reports it on track 0, which the swap maps to fly 1
    g = {1: _gt(W, wt=[(100, 200)], fly=1)}
    bt = {b: np.zeros((2, W), bool) for b in fx.SCORED}
    bt["wing_threat"][0, 100:200] = True
    pr = {1: _pred(W, {b: bt[b].any(axis=0) for b in fx.SCORED}, by_track=bt, xy=e)}
    pr[1]["identity_unknown"] = unk
    r = fs.score_movies(g, pr, fx.SCORED, reps=100, gt_tracks={1: gt_tr}, window_minutes=W / 1800, ref_reps=10)
    assert r["behaviors"]["wing_threat"]["per_fly_frame"]["f1"] == 1.0
    assert r["behaviors"]["wing_threat"]["pair_vs_by_track_mismatch_frames"] == 0
    assert r["identity"]["per_movie"]["movie1"]["permutation"] == "swap"


# --------------------------------------------------------------------------- end-to-end on a synthetic dataset

def make_dataset(root: Path, movies=(1, 2)):
    write_traintest(root)
    s0 = fx.WINDOWS["mid5"][0]
    for m in movies:
        d = root / f"movie{m}"
        lunges = [(s0 + 101 + 40 * i, s0 + 104 + 40 * i, 0) for i in range(20)]
        write_actions(d / f"movie{m}_actions.mat", {(0, "lunge"): lunges,
                                                    (1, "wing_threat"): [(s0 + 2001, s0 + 2300, 0)]})
        write_track(d / f"movie{m}_track.mat", static_xy())
    return root


def perfect_preds(root: Path, out: Path, movies=(1, 2), window="mid5"):
    out.mkdir(parents=True, exist_ok=True)
    w = fx.WINDOWS[window]
    for m in movies:
        L = fx.load_window_labels(m, window, root, allow_test=True)  # synthetic data only
        K = w[1] - w[0]
        xy = np.zeros((K, 2, 2))
        xy[:, 0], xy[:, 1] = (30.0, 30.0), (100.0, 100.0)
        np.savez(out / f"movie{m}.npz", window=np.array(w), sample_fps=np.array(30),
                 wing_threat=L["pair"]["wing_threat"].astype(np.uint8), lunge=L["pair"]["lunge"].astype(np.uint8),
                 wing_threat_by_track=L["per_fly"]["wing_threat"].astype(np.uint8),
                 lunge_by_track=L["per_fly"]["lunge"].astype(np.uint8), track_xy=xy)
    (out / "manifest.json").write_text(json.dumps({"config": {"name": "synthetic"}, "cost": {"usd": 0}}))
    return out


def test_run_dev_end_to_end_logs_and_go_no_go(tmp_path):
    root = make_dataset(tmp_path / "data")
    pred = perfect_preds(root, tmp_path / "pred")
    log = tmp_path / "runs.jsonl"
    crit = Path("eval/flies_criteria_v1.json")
    rec = fs.run("dev", "mid5", pred, root=root, log_path=log, out_dir=tmp_path / "out", reps=200, ref_reps=20,
                 criteria_path=crit)
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["started", "ok"]
    assert lines[0]["split"] == "flies-dev" and lines[0]["predictions_sha256"] == lines[1]["predictions_sha256"]
    m = rec["metrics"]
    assert m["behaviors"]["lunge"]["frame"]["f1"] == 1.0
    assert m["behaviors"]["wing_threat"]["per_fly_frame"]["f1"] == 1.0
    assert m["identity"]["pooled"]["identity_accuracy"] == 1.0
    v = rec["verdicts"]
    assert v["lunge"]["decision"].startswith("GO")  # perfect, 40 bouts, both tracks present, mid5 @ 30 fps
    # wing_threat: only allowed on the train split and needs >= 3 movies with the behavior
    assert v["wing_threat"]["decision"].startswith("NO-GO") and not v["wing_threat"]["split_ok"]
    assert v["wing_threat"]["evidence"]["sufficient"] is False
    # no committed-file reads: a missing prediction refuses before anything is logged
    (pred / "movie2.npz").unlink()
    with pytest.raises(SystemExit):
        fs.run("dev", "mid5", pred, root=root, log_path=log, out_dir=None, reps=50, ref_reps=5)
    assert len(log.read_text().splitlines()) == 2


def test_go_no_go_wing_threat_bar():
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())

    def metrics(f1, lo, ref, n_b=20, movies=(2, 3, 4)):
        return {"behaviors": {"wing_threat": {"frame": {"f1": f1}, "frame_ci95": {"f1": [lo, 1.0]},
                                              "random_reference": {"iid_expected_f1": ref},
                                              "bouts": {"any": {"n_gt": n_b}},
                                              "movies_with_behavior": list(movies)}}}
    early = "2026-10-30T12:00:00+00:00"
    go = fs.evaluate_go_no_go(metrics(0.20, 0.05, 0.043), crit, "train", "mid5", ["30.0"],
                              now_utc=early)["wing_threat"]
    assert go["frame_f1"]["point_bar"] == pytest.approx(0.193) and go["decision"].startswith("GO")
    assert fs.evaluate_go_no_go(metrics(0.19, 0.05, 0.043), crit, "train", "mid5", ["30.0"], now_utc=early)[
        "wing_threat"]["decision"].startswith("NO-GO")
    assert fs.evaluate_go_no_go(metrics(0.30, 0.04, 0.043), crit, "train", "mid5", ["30.0"], now_utc=early)[
        "wing_threat"]["decision"].startswith("NO-GO")  # CI lower bound not above the reference
    assert fs.evaluate_go_no_go(metrics(0.50, 0.30, 0.10), crit, "train", "mid5", ["30.0"], now_utc=early)[
        "wing_threat"]["frame_f1"]["point_bar"] == pytest.approx(0.25)  # max(2 x 0.10, 0.10 + 0.15)
    assert fs.evaluate_go_no_go(metrics(0.5, 0.3, 0.04, movies=(2, 3)), crit, "train", "mid5", ["30.0"], now_utc=early)[
        "wing_threat"]["decision"].startswith("NO-GO")  # too few movies with wing threat
    assert fs.evaluate_go_no_go(metrics(0.5, 0.3, 0.04), crit, "train", "mid5", ["30.0"], partial=True, now_utc=early)[
        "wing_threat"]["decision"].startswith("NO-GO")  # a subset of the train movies is not the training split
    late = fs.evaluate_go_no_go(metrics(0.5, 0.3, 0.04), crit, "train", "mid5", ["30.0"],
                                now_utc="2026-11-01T04:00:01+00:00")["wing_threat"]  # 00:00:01 ET Nov 1
    assert not late["before_deadline"] and late["decision"].startswith("NO-GO")
    assert fs.evaluate_go_no_go(metrics(0.5, 0.3, 0.04), crit, "train", "mid5", ["30.0"],
                                now_utc="2026-11-01T03:59:00+00:00")["wing_threat"]["decision"].startswith("GO")


def test_test_split_refusals_log_nothing(tmp_path):
    root = make_dataset(tmp_path / "data", movies=())  # no test files exist at all
    pred = tmp_path / "pred"
    pred.mkdir()
    log = tmp_path / "runs.jsonl"
    plan = tmp_path / "plan.md"
    plan.write_text(f"... {fs.PROPOSED_MARKER} ...")
    crit_p = tmp_path / "crit.json"
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())
    crit_p.write_text(json.dumps(crit))
    kw = dict(root=root, log_path=log, out_dir=None, check_git=False, plan_file=plan, criteria_path=crit_p)
    with pytest.raises(SystemExit, match="confirm"):
        fs.run("test", "mid5", pred, purpose="test-1", **kw)
    with pytest.raises(SystemExit, match="purpose"):
        fs.run("test", "mid5", pred, purpose="tune", confirm_test_run=True, **kw)
    with pytest.raises(SystemExit, match="not frozen"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, **kw)
    plan.write_text("FLIES-PLAN-STATUS: FROZEN")
    with pytest.raises(SystemExit, match="FROZEN"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, **kw)
    crit["status"] = "FROZEN"
    crit_p.write_text(json.dumps(crit))
    with pytest.raises(SystemExit, match="test-2"):
        fs.run("test", "mid5", pred, purpose="test-2", confirm_test_run=True, **kw)
    with pytest.raises(SystemExit, match="frozen-config"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, **kw)
    with pytest.raises(SystemExit, match="--movies"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, movies=[6], **kw)
    fc = tmp_path / "frozen.json"
    fc.write_text(json.dumps({"name": "x", "window": "mid10", "asset": {"path": str(plan), "sha256": "0" * 64}}))
    with pytest.raises(SystemExit, match="mismatch"):
        fs.run("test", "mid5", pred, purpose="test-1", confirm_test_run=True, frozen_config=fc, **kw)
    assert not log.exists()  # nothing was logged as a test execution


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


def test_test_split_full_path_on_synthetic_data(tmp_path):
    root = make_dataset(tmp_path / "data", movies=tuple(range(1, 11)))
    for m in range(1, 11):
        for ext in (".mp4", "_feat.mat"):
            (root / f"movie{m}" / f"movie{m}{ext}").write_bytes(b"x")
    man = tmp_path / "flies_manifest.json"
    man.write_text(json.dumps(fx.file_manifest(root, tmp_path / "none.zip")))
    plan = tmp_path / "plan.md"
    plan.write_text("FLIES-PLAN-STATUS: FROZEN")
    crit = json.loads(Path("eval/flies_criteria_v1.json").read_text())
    crit["status"] = "FROZEN"
    crit_p = tmp_path / "crit.json"
    crit_p.write_text(json.dumps(crit))
    fc = tmp_path / "frozen.json"
    fc.write_text(json.dumps({"name": "x", "window": "mid5", "plan": {"path": str(plan),
                                                                     "sha256": fx.common.sha256_file(plan)}}))
    pred = perfect_preds(root, tmp_path / "pred", movies=fx.TEST_MOVIES)
    (pred / "manifest.json").write_text(json.dumps({"config": {"frozen_sha256": fx.common.sha256_file(fc)}}))
    log = tmp_path / "runs.jsonl"
    kw = dict(root=root, log_path=log, out_dir=tmp_path / "out", check_git=False, plan_file=plan,
              criteria_path=crit_p, frozen_config=fc, confirm_test_run=True, data_manifest=man, reps=100, ref_reps=5)
    rec = fs.run("test", "mid5", pred, purpose="test-1", **kw)
    assert "per_movie" not in rec["metrics"] and Path(rec["sealed_per_movie"]["path"]).exists()
    assert "per_movie" not in rec["metrics"]["identity"]
    assert rec["verdicts"]["wing_threat"]["frame_f1"]["verdict"].startswith("NOT EVALUABLE")  # 5 bouts < 10
    assert rec["verdicts"]["lunge"]["bout_any_f1"]["verdict"] == "GOOD MET"
    assert rec["metrics"]["inter_annotator"] == {}
    with pytest.raises(SystemExit, match="already executed"):
        fs.run("test", "mid5", pred, purpose="test-1", **kw)
    # a tampered data file refuses test-2 before anything is logged
    (root / "movie7" / "movie7_track.mat").write_bytes(b"tampered")
    n = len(log.read_text().splitlines())
    with pytest.raises(SystemExit, match="manifest"):
        fs.run("test", "mid5", pred, purpose="test-2", **kw)
    assert len(log.read_text().splitlines()) == n
