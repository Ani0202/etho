"""Fly rule synthesis, extras, post-processing and prediction writer (no network, gateway mocked)."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from etho.flies import predict as P
from etho.flies import synth as S
from etho.flies.extras import EXTRA_DOC, add_extras
from etho.rules.sandbox import SandboxError

FPS = 30.0
N = 9000
GOOD = "def detect(t, fps):\n    return np.nan_to_num(t['max_minor_ratio'], nan=0.0) >= 1.25\n"


def table(n=N, start=22500, step=1) -> dict[str, np.ndarray]:
    """A synthetic two-fly feature table with every column the product code touches."""
    rng = np.random.default_rng(0)
    t = {"src_frame": start + step * np.arange(n, dtype=np.int64),
         "id_unknown": np.zeros(n), "id_epoch": np.zeros(n), "both_present": np.ones(n), "dist_bl": np.full(n, 1.3),
         "max_minor_ratio": np.full(n, 1.0)}
    for p, x0 in (("a", 30.0), ("b", 90.0)):
        t[f"{p}_present"] = np.ones(n)
        t[f"{p}_cx"] = np.full(n, x0) + rng.normal(0, 0.1, n)
        t[f"{p}_cy"] = np.full(n, 70.0)
        t[f"{p}_bx"], t[f"{p}_by"] = t[f"{p}_cx"].copy(), t[f"{p}_cy"].copy()
        t[f"{p}_minor_ratio"] = np.ones(n)
        t[f"{p}_toward_speed"] = np.zeros(n)
    return t


class FakeGW:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def chat(self, provider, model, msgs, **kw):
        self.calls.append((provider, model, kw, msgs))

        class R:
            text = self.answers.pop(0)
            cost_usd, usage, cache_hit, latency_s = 0.002, {"output_tokens": 10}, False, 0.1
        return R()


def small_table():
    t = table(60)
    t["max_minor_ratio"][10:30] = 1.4
    return t


# ---------------------------------------------------------------- synthesis adapter

def test_synthesize_candidate_prompt_and_gateway_policy():
    gw = FakeGW([f"```python\n{GOOD}```"])
    c = S.synthesize_candidate("wing_threat", "One fly quickly raises both wings to a 45 degree angle",
                               subject="one fly", partner="the other fly", tables=[small_table()], fps=FPS, gw=gw,
                               model="muse-spark-1.3-contributor", public_data=True, tag="ablation")
    prov, model, kw, msgs = gw.calls[0]
    assert (prov, model) == ("meta", "muse-spark-1.3-contributor")
    assert kw["effort"] == "high" and kw["public_data"] is True and kw["tag"] == "ablation"
    user = msgs[1]["content"]
    assert "one fly" in user and "the other fly" in user and "45 degree" in user
    assert "max_minor_ratio" in user and "either fly performs the behavior" in user
    assert "ground truth" not in user.lower() and "annotation" not in user.lower()  # no label content in the prompt
    assert c.code.strip() == GOOD.strip() and len(c.prompt_hash) == 16 and len(c.calls) == 1


def test_candidates_get_different_prompts_and_extras_change_the_doc():
    base = S.build_prompt("lunge", "d", "one fly", "the other fly", "stats", FPS)
    ext = S.build_prompt("lunge", "d", "one fly", "the other fly", "stats", FPS, extras=True)
    assert "max_toward_speed_raw" not in base and all(k in ext for k in EXTRA_DOC)
    phys = S.build_prompt("lunge", "d", "one fly", "the other fly", "stats", FPS, variant="physics")
    assert phys != base and "silhouette" in phys
    gw = FakeGW([f"```python\n{GOOD}```"] * 2)
    kw = dict(subject="a", partner="b", tables=[small_table()], fps=FPS, gw=gw, model="m", public_data=True)
    c0 = S.synthesize_candidate("wing_threat", "x", **kw)
    c1 = S.synthesize_candidate("wing_threat", "x", candidate=1, **kw)
    assert c0.prompt_hash != c1.prompt_hash and "Independent attempt #1" in gw.calls[1][3][1]["content"]


def test_synthesize_repairs_then_gives_up():
    bad = "```python\ndef detect(t, fps):\n    return t['missing'] > 0\n```"
    gw = FakeGW([bad, f"```python\n{GOOD}```"])
    c = S.synthesize_candidate("w", "x", subject="a", partner="b", tables=[small_table()], fps=FPS, gw=gw,
                               model="m", public_data=True)
    assert len(c.calls) == 2 and "KeyError" in gw.calls[1][3][-1]["content"]
    with pytest.raises(SandboxError, match="still failing"):
        S.synthesize_candidate("w", "x", subject="a", partner="b", tables=[small_table()], fps=FPS,
                               gw=FakeGW([bad] * 3), model="m", public_data=True)


def test_feature_stats_are_label_free_and_skip_bookkeeping():
    s = S.feature_stats([small_table()])
    assert "max_minor_ratio" in s and "src_frame" not in s and "id_epoch" not in s


def test_medoid_prefers_agreement_and_ignores_empty_candidates():
    a = [np.array([1, 1, 0, 0, 0, 0], bool)]
    b = [np.array([1, 1, 1, 0, 0, 0], bool)]
    far = [np.array([0, 0, 0, 0, 1, 1], bool)]
    empty = [np.zeros(6, bool)]
    best, agree = S.select_medoid([far, a, b])
    assert best in (1, 2) and agree[0] < min(agree[1], agree[2])
    assert S.select_medoid([empty, a, b])[0] in (1, 2)
    assert S.select_medoid([a])[0] == 0
    assert S.pooled_f1(a, a) == 1.0 and S.pooled_f1(empty, empty) == 0.0


# ---------------------------------------------------------------- extras

def test_extras_are_not_masked_and_swap_robust():
    n = 40
    t = table(n)
    # fly A rushes at B for 4 samples (a 6 px/sample dart = 180 px/s), with vel_ok = 0 and an A/B label swap
    t["a_bx"] = np.concatenate([np.full(20, 30.0), 30 + 6.0 * np.arange(1, 5), np.full(16, 54.0)])
    t["a_cx"] = t["a_bx"]
    swap_from = 30
    ax, bx = t["a_bx"].copy(), t["b_bx"].copy()
    t["a_bx"][swap_from:], t["b_bx"][swap_from:] = bx[swap_from:], ax[swap_from:]  # labels exchanged, no motion
    t["a_cx"], t["b_cx"] = t["a_bx"].copy(), t["b_bx"].copy()
    t["vel_ok"] = np.zeros(n)
    e = add_extras(t, FPS)
    assert set(EXTRA_DOC) <= set(e) and len(e["max_toward_speed_raw"]) == n
    assert np.nanmax(e["max_toward_speed_raw"][18:26]) > 60  # the dart is visible although vel_ok = 0
    assert np.nanmax(e["max_speed_raw"][28:36]) < 40         # the label swap at 30 is not a 1800 px/s jump
    assert "max_toward_speed_raw" not in t                    # input table is not modified


def test_extras_nan_behaviour_matches_the_documentation():
    t = table(40)
    rng = np.random.default_rng(0)
    for p in ("a", "b"):
        for c in ("cx", "cy"):
            t[f"{p}_{c}"] = t[f"{p}_{c}"] + rng.normal(0, 1.0, 40)
        t[f"{p}_bx"], t[f"{p}_by"] = t[f"{p}_cx"].copy(), t[f"{p}_cy"].copy()
    for sl in (slice(10, 11), slice(20, 25)):  # fly a missing: 1 sample, then 5 samples
        for c in ("cx", "cy", "bx", "by"):
            t[f"a_{c}"][sl] = np.nan
    for c in ("cx", "cy", "bx", "by"):  # both missing for 2 samples
        t[f"a_{c}"][30:32] = np.nan
        t[f"b_{c}"][30:32] = np.nan
    e = add_extras(t, FPS)
    nan = {k: set(np.nonzero(~np.isfinite(e[k]))[0].tolist()) for k in EXTRA_DOC}

    assert not nan["max_speed_raw"] & set(range(10, 30))     # one fly present: it is the present fly's speed
    assert 31 in nan["max_speed_raw"] and 31 in nan["max_toward_speed_raw"]  # neither fly, several samples
    assert 10 not in nan["max_toward_speed_raw"]             # a 1-sample gap is bridged by the smoothing
    assert {21, 22, 23} <= nan["max_toward_speed_raw"]       # needs both positions
    assert set(range(20, 25)) <= nan["closing_speed_raw"]
    assert not nan["peak_toward_raw"]                        # the 0.3 s look-ahead always finds a value here
    assert np.allclose(e["min_speed_raw"][21:24], e["max_speed_raw"][21:24])  # one fly: min equals max


# ---------------------------------------------------------------- post-processing, actor

def test_postprocess_bridge_min_peak_pad():
    m = np.zeros(100, bool)
    m[10:13] = True
    m[15:30] = True  # 2-sample gap to bridge
    m[60:62] = True  # short blip
    m[80:90] = True
    out = P.postprocess(m, 30.0, {"bridge_s": 0.1, "min_s": 0.2})
    assert out[10:30].all() and not out[60:62].any() and out[80:90].all()
    pk = P.postprocess(m, 30.0, {"peak_len_s": 4 / 30})
    assert [e - s for s, e in P.runs(pk)] == [4, 4, 4, 4]
    assert P.runs(P.postprocess(np.eye(1, 50, 20, dtype=bool)[0], 30.0, {"pad_s": 0.1})) == [(17, 24)]
    assert np.array_equal(P.postprocess(m, 30.0, None), m)
    # durations are seconds: the same spec at 10 fps keeps/drops the same events
    assert P.postprocess(np.r_[np.zeros(5, bool), np.ones(2, bool), np.zeros(5, bool)], 10.0, {"min_s": 0.3}).sum() == 0


def test_assign_actor_picks_the_wider_or_faster_fly():
    t = table(50)
    t["b_minor_ratio"][10:20] = 1.5
    t["a_toward_speed"][30:34] = 90.0
    pair_w = np.zeros(50, np.uint8)
    pair_w[10:20] = 1
    pair_l = np.zeros(50, np.uint8)
    pair_l[30:34] = 1
    w, l_ = P.assign_actor("wing_threat", pair_w, t), P.assign_actor("lunge", pair_l, t)
    assert w[1, 10:20].all() and not w[0].any() and l_[0, 30:34].all() and not l_[1].any()
    assert not (w.any(axis=0) & ~pair_w.astype(bool)).any()  # never names an actor where the pair label is 0


# ---------------------------------------------------------------- prediction writer

def spec(code=GOOD, post=None, extras=False):
    return {"extras": extras, "behaviors": {
        "wing_threat": {"candidates": [{"code": code}], "use": [0], "min_votes": 1, "post": post or {}},
        "lunge": {"candidates": [{"code": "def detect(t, fps):\n    return np.zeros(len(t['dist_bl']), bool)\n"}],
                  "use": [0], "min_votes": 1, "post": {}}}}


def test_predict_movie_format_matches_the_scorer(tmp_path):
    t = table()
    t["max_minor_ratio"][100:200] = 1.4
    t["b_minor_ratio"][100:200] = 1.4
    t["a_present"][500:520] = 0
    t["a_cx"][500:520] = np.nan
    t["id_unknown"][300:310] = 1
    out = P.predict_movie(t, spec(), "mid5", FPS)
    assert out["window"].tolist() == [22500, 31500] and float(out["sample_fps"]) == 30.0
    assert out["wing_threat"].shape == (N,) and out["wing_threat"].sum() == 100 and out["lunge"].sum() == 0
    assert out["wing_threat_by_track"].shape == (2, N) and out["wing_threat_by_track"][1, 100:200].all()
    assert out["track_xy"].shape == (N, 2, 2) and np.isnan(out["track_xy"][505, 0]).all()
    assert out["identity_unknown"].sum() == 10 and out["id_epoch"].shape == (N,)
    from eval.flies_score import load_prediction  # validates length, window, binary arrays, actor consistency
    p = P.WINDOWS["mid5"]
    np.savez(tmp_path / "movie1.npz", **out)
    got = load_prediction(tmp_path / "movie1.npz", p, ("wing_threat", "lunge"))
    assert got["pair"]["wing_threat"].sum() == 100


def test_predict_movie_rejects_the_wrong_window_and_applies_overrides():
    t = table()
    with pytest.raises(ValueError, match="do not cover window"):
        P.predict_movie(t, spec(), "mid10", FPS)
    t["max_minor_ratio"][100:110] = 1.4
    kept = P.predict_movie(t, spec(), "mid5", FPS)
    dropped = P.predict_movie(t, spec(), "mid5", FPS, {"wing_threat": {"min_s": 1.0}})
    assert kept["wing_threat"].sum() == 10 and dropped["wing_threat"].sum() == 0


def test_vote_needs_min_votes():
    t = table(30)
    t["max_minor_ratio"][5:15] = 1.4
    other = "def detect(t, fps):\n    return np.nan_to_num(t['max_minor_ratio'], nan=0.0) >= 1.45\n"
    sp = {"candidates": [{"code": GOOD}, {"code": other}, {"code": GOOD}], "use": [0, 1, 2], "min_votes": 2}
    assert P.detect_pair(sp, t, FPS).sum() == 10
    assert P.detect_pair({**sp, "min_votes": 3}, t, FPS).sum() == 0


def test_write_predictions_manifest(tmp_path):
    fz = tmp_path / "frozen.json"
    fz.write_text("{}")
    out = P.predict_movie(table(), spec(), "mid5", FPS)
    P.write_predictions(tmp_path / "pred", {1: out}, config={"pipeline": "etho.flies"}, frozen_config=fz,
                        notes="x")
    man = json.loads((tmp_path / "pred/manifest.json").read_text())
    assert (tmp_path / "pred/movie1.npz").exists() and man["config"]["frozen_sha256"] == P.sha256_file(fz)
    assert "run_commit" in man["config"] and isinstance(man["config"]["run_dirty_paths"], list)
    assert man["cost"] == {"usd": 0.0}


# ---------------------------------------------------------------- CLI guards (no model call)

def test_cli_refuses_non_dev_movies_without_frozen_config(tmp_path):
    det = tmp_path / "d.json"
    det.write_text(json.dumps(spec()))
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "scripts/run_fly_etho.py", "predict", "--movies", "3", "--detectors", str(det),
                        "--out", str(tmp_path / "o")], cwd=root, capture_output=True, text=True,
                       env={"PATH": "/usr/bin", "PYTHONPATH": str(root), "ETHO_ROOT": str(tmp_path)})
    assert r.returncode != 0 and "--frozen-config" in r.stderr
    r = subprocess.run([sys.executable, "scripts/run_fly_etho.py", "synth", "--movies", "6", "--out",
                        str(tmp_path / "o.json")], cwd=root, capture_output=True, text=True,
                       env={"PATH": "/usr/bin", "PYTHONPATH": str(root), "ETHO_ROOT": str(tmp_path)})
    assert r.returncode != 0 and "dev movies 1-2" in r.stderr


# ---------------------------------------------------------------- frozen tracking config

def test_tracking_config_matches_the_frozen_tree_and_the_dev_tracks():
    root = Path(__file__).resolve().parents[1]
    tc = json.loads((root / "configs/flies_tracking.json").read_text())
    assert tc["phrase"] == "fruit fly" and tc["scale"] == 1 and tc["overlap_s"] == 1.0
    go, nogo = tc["branches"]["GO"], tc["branches"]["NO-GO"]
    assert (go["start"], go["stop"], go["stride"]) == (*P.WINDOWS["mid5"], 1)
    assert (nogo["start"], nogo["stop"], nogo["stride"]) == (*P.WINDOWS["mid10"], 3)
    from etho.tracking.fly_pipeline import PHRASE
    assert tc["phrase"] == PHRASE


def test_lunge_actor_uses_the_unmasked_toward_speed():
    t = table(60)
    t["a_toward_speed"][:] = np.nan  # the guarded column is NaN during strikes
    t["b_toward_speed"][:] = np.nan
    t["b_bx"][20:24] = t["b_bx"][19] - 6.0 * np.arange(1, 5)  # fly b darts toward a (a is at x = 30, b at x = 90)
    t["b_bx"][24:] = t["b_bx"][23]
    t["b_cx"] = t["b_bx"].copy()
    pair = np.zeros(60, np.uint8)
    pair[20:25] = 1
    by = P.assign_actor("lunge", pair, t, FPS)
    assert by[1, 20:25].all() and not by[0].any()
