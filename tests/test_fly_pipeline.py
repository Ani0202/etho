"""Fly pipeline end to end with a fake gateway: chunking, per-chunk SAM, stitching, table, outputs.

The fake SAM returns two ellipse "flies" on known trajectories. SAM object ids are deliberately
reshuffled per chunk (as they would be: each parallel request numbers its own tracks), and the
first samples of each chunk are missed (acquisition lag), so the table is right only if the
Hungarian stitching over the overlap works.
"""

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from etho.tracking import fly_pipeline as fp
from etho.tracking.fly_video import FlyChunk
from etho.tracking.sam_parse import ObjectFrame, mask_moments
from tests.test_fly_video import decode_index, make_source, read_all

START, N = 30, 270                    # window: source frames [30, 300) at 30 fps, stride 1
CHUNK_S, OVERLAP_S = 3.0, 0.5         # 90-sample chunks, 15-sample overlap -> 3 chunks
LAG = 3


def default_positions(g):
    """(fly1, fly2) centroids at global sample g (source px). They come within a body length from about g=175."""
    p1 = np.array([20.0 + 0.4 * g, 40.0])
    p2 = np.array([120.0 - 0.1 * g, 100.0 - 0.6 * min(max(g - 120, 0), 65)])
    return p1, p2


def ellipse_mask(cx, cy):
    box = (int(cx) - 20, int(cy) - 20, int(cx) + 20, int(cy) + 20)
    m = np.zeros((40, 40), np.uint8)
    cv2.ellipse(m, (20, 20), (13, 5), 0, 0, 360, 1, -1)
    return m, box


def fake_parse_line(line, media="video", frame_shape=None, keep_rle=False, want_mask=False):
    d = json.loads(line)
    out = []
    for oid, (cx, cy) in d["objs"].items():
        m, box = ellipse_mask(cx, cy)
        area, mcx, mcy, ang, el = mask_moments(m, box[0], box[1])
        out.append((ObjectFrame(d["frame"], oid, box, area, mcx, mcy, ang, el), m if want_mask else None))
    return out


class FakeGateway:
    """Stands in for etho.gateway.Gateway.sam: emits one JSON line per chunk-local sample.

    firsts: global sample of each chunk's first sample. swap_odd: odd chunks number their tracks the other
    way round. sam_swap: global sample at which SAM silently exchanges its two ids inside chunk 0.
    check_frames: also decode the chunk video's first frame and check it is the right source frame."""

    def __init__(self, firsts, positions=default_positions, swap_odd=True, sam_swap=None, start=START, stride=1,
                 check_frames=False, scale=1):
        self.calls, self.firsts, self.positions = [], firsts, positions
        self.swap_odd, self.sam_swap, self.start, self.stride = swap_odd, sam_swap, start, stride
        self.check_frames, self.scale = check_frames, scale

    def sam(self, phrase, *, tag, video, n_frames, public_data, metadata, on_line, caller):
        self.calls.append({"phrase": phrase, "tag": tag, "public_data": public_data, "n_frames": n_frames})
        ci = int(re.search(r"c(\d+)\.mp4", str(video)).group(1))
        first = self.firsts[ci]
        if self.check_frames:
            frames = read_all(video)
            assert len(frames) == n_frames
            for local in (0, n_frames - 1):
                assert decode_index(frames[local], self.scale) == self.start + self.stride * (first + local)
        swap = self.swap_odd and ci % 2 == 1
        for local in range(n_frames):
            if local < LAG:
                continue
            g = first + local
            p1, p2 = self.positions(g)
            flip = swap != (ci == 0 and self.sam_swap is not None and g >= self.sam_swap)
            ids = ("1", "0") if flip else ("0", "1")
            objs = sorted([(ids[0], tuple(p1)), (ids[1], tuple(p2))])   # emitted in id order, like SAM
            on_line(json.dumps({"frame": local, "objs": dict(objs)}))
        return SimpleNamespace(cost_usd=n_frames * 0.0002, cache_hit=False, frames_billed=max(n_frames, 50))


FIRSTS = [0, 75, 165]


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "movie.mp4"
    make_source(p, 330)
    return p


def run(source, tmp_path, monkeypatch, gw=None, **kw):
    monkeypatch.setattr(fp, "parse_line", fake_parse_line)
    gw = gw or FakeGateway(FIRSTS)
    args = dict(start=START, stop=START + N, stride=1, scale=1, chunk_s=CHUNK_S, overlap_s=OVERLAP_S, workers=2,
                chunk_dir=tmp_path / "chunks", public_data=True)
    args.update(kw)
    r = fp.track_fly_movie(source, tmp_path / "out", gw=gw, **args)
    return r, gw


def test_stitching_keeps_one_fly_in_slot_a_across_chunks_with_shuffled_sam_ids(source, tmp_path, monkeypatch):
    r, gw = run(source, tmp_path, monkeypatch)
    t = r["table"]
    assert r["n_samples"] == N and sorted(c["n_frames"] for c in gw.calls) == [90, 105, 105]
    assert {c["phrase"] for c in gw.calls} == {"fruit fly"} and {c["tag"] for c in gw.calls} == {"ablation"}
    assert all(c["public_data"] for c in gw.calls)
    pres = (t["a_present"] > 0) & (t["b_present"] > 0)
    assert pres[LAG:].all() and not pres[:LAG].any()      # lag samples of chunk 0 are the only misses
    g = np.arange(N)
    p1 = np.array([default_positions(i)[0] for i in g])
    p2 = np.array([default_positions(i)[1] for i in g])
    np.testing.assert_allclose(t["a_cx"][pres], p1[pres, 0], atol=1.01)
    np.testing.assert_allclose(t["b_cy"][pres], p2[pres, 1], atol=1.01)
    assert r["info"]["n_epochs"] == 1 and all(not s["ambiguous"] for s in r["info"]["stitches"])
    # chunk 1 numbers its tracks the other way round (stitching undoes that), chunk 2 like chunk 0
    assert [s["perm"] for s in r["info"]["stitches"]] == [[1, 0], [0, 1]]


def test_sample_to_source_frame_mapping_is_recorded(source, tmp_path, monkeypatch):
    r, _ = run(source, tmp_path, monkeypatch)
    t = r["table"]
    assert t["src_frame"][0] == START and t["src_frame"][-1] == START + N - 1
    meta = json.loads((tmp_path / "out" / "tracking.json").read_text())
    assert meta["window"] == {"start": START, "stop": START + N, "stride": 1, "scale": 1}
    assert meta["config"] == {"chunk_s": CHUNK_S, "overlap_s": OVERLAP_S, "tag": "ablation", "public_data": True}
    assert [(c["first"], c["own0"], c["own1"]) for c in meta["chunk_files"]] == [(0, 0, 90), (75, 90, 180),
                                                                                  (165, 180, 270)]
    assert meta["phrase"] == "fruit fly" and meta["frames_billed"] >= N


def test_stride_3_end_to_end_mapping_from_source_frames_to_table_rows(source, tmp_path, monkeypatch):
    """10 fps: sample g is source frame 30 + 3 g. The chunk videos the fake SAM receives are decoded and
    checked frame by frame; the table, tracks.npz and tracking.json carry the same mapping."""
    n3, firsts = 90, [0, 20, 50]                       # 30-sample chunks at 10 fps, 10-sample overlap
    gw = FakeGateway(firsts, stride=3, check_frames=True)
    r, _ = run(source, tmp_path, monkeypatch, gw=gw, stop=START + 3 * n3, stride=3, chunk_s=3.0, overlap_s=1.0)
    t = r["table"]
    assert r["n_samples"] == n3 and r["sample_fps"] == 10.0
    assert sorted(c["n_frames"] for c in gw.calls) == [30, 40, 40]
    np.testing.assert_array_equal(t["src_frame"], START + 3 * np.arange(n3))
    tr = np.load(tmp_path / "out" / "tracks.npz")
    assert set(tr["frame"] % 3) == {START % 3} and tr["frame"].max() <= START + 3 * (n3 - 1)
    meta = json.loads((tmp_path / "out" / "tracking.json").read_text())
    assert meta["sample_fps"] == 10.0 and meta["window"]["stride"] == 3
    assert [c["stride"] for c in meta["chunk_files"]] == [3, 3, 3]
    pres = (t["a_present"] > 0) & (t["b_present"] > 0)
    p1 = np.array([default_positions(i)[0] for i in range(n3)])
    np.testing.assert_allclose(t["a_cx"][pres], p1[pres, 0], atol=1.01)


def test_ambiguous_stitch_starts_a_new_epoch_and_invalidates_velocity_around_it(source, tmp_path, monkeypatch):
    """The flies touch (3 px apart) over the whole overlap of chunk 1: position cannot decide the stitch."""
    def close_then_apart(g):
        d = float(np.interp(g, [0, 60, 70, 110, 120, 270], [40, 40, 3, 3, 40, 40]))
        return np.array([60 - d / 2, 60.0]), np.array([60 + d / 2, 60.0])

    r, _ = run(source, tmp_path, monkeypatch, gw=FakeGateway(FIRSTS, positions=close_then_apart))
    t, info = r["table"], r["info"]
    amb = [s for s in info["stitches"] if s["ambiguous"]]
    assert [s["chunk"] for s in amb] == [1] and "margin" in amb[0]["reason"]
    assert not info["stitches"][1]["ambiguous"]                      # chunk 2's overlap is far apart: decisive
    ep = t["id_epoch"]
    assert ep[89] == 0 and ep[90] == 1 and ep[-1] == 1 and info["n_epochs"] == 2
    assert info["relabels_at"] == []                                  # nothing was relabeled
    # velocity columns are NaN either side of the epoch change, and valid well away from every edge
    for c in ("a_speed", "approach", "max_toward_speed", "a_speed_s", "a_heading"):
        assert np.isnan(t[c][90]) and np.isnan(t[c][88]) and np.isnan(t[c][92]), c
    assert t["vel_ok"][90] == 0 and t["vel_ok"][250] == 1 and np.isfinite(t["a_speed"][250])
    # the flies are within a body length from ~g=64 to ~g=116: identity unknown there, edges invalid
    assert t["id_unknown"][90] == 1 and t["id_unknown"][20] == 0 and t["vel_ok"][63] == 0


def test_silent_sam_swap_inside_a_chunk_is_relabeled_and_starts_a_new_epoch(source, tmp_path, monkeypatch):
    def crossing(g):
        return np.array([20.0 + g, 60.0]), np.array([100.0 - g, 70.0])    # pass at g=40, 10 px apart

    gw = FakeGateway(FIRSTS, positions=crossing, swap_odd=False, sam_swap=40)   # SAM exchanges its ids at g=40
    r, _ = run(source, tmp_path, monkeypatch, gw=gw)
    t, info = r["table"], r["info"]
    g = np.arange(N)
    assert len(info["relabels_at"]) == 1 and 50 <= info["relabels_at"][0] <= 56
    ok = (t["a_present"] > 0) & (t["b_present"] > 0) & (t["id_unknown"] == 0)
    assert ok[60:].all()
    # outside the contact run, slot a is fly 1 and slot b is fly 2 all the way through (positions 20+g)
    expect_a = np.array([crossing(i)[0][0] for i in g])
    np.testing.assert_allclose(t["a_cx"][ok], expect_a[ok], atol=1.01)
    assert t["id_epoch"][info["relabels_at"][0]] == t["id_epoch"][0] + 1
    assert info["n_epochs"] == 2
    # without the relabel the labels would have followed SAM's swap
    monkeypatch.setattr(fp, "relabel_swaps", lambda *a, **k: [])
    r2, _ = run(source, tmp_path, monkeypatch, gw=FakeGateway(FIRSTS, positions=crossing, swap_odd=False, sam_swap=40))
    bad = np.abs(r2["table"]["a_cx"][ok] - expect_a[ok]) > 5
    assert bad[40:].any()


def test_rederive_from_raw_rle_reproduces_the_tracking_run_exactly(source, tmp_path, monkeypatch):
    r, _ = run(source, tmp_path, monkeypatch)
    out = tmp_path / "out"
    before = {k: v.copy() for k, v in np.load(out / "features.npz").items()}
    meta0 = json.loads((out / "tracking.json").read_text())
    r2 = fp.rederive_fly_run(out)
    after = np.load(out / "features.npz")
    assert set(before) == set(after.files)
    for k, v in before.items():
        np.testing.assert_allclose(after[k], v, equal_nan=True, err_msg=k)
    meta = json.loads((out / "tracking.json").read_text())
    assert meta["rederived_from_raw"] and meta["cost_usd"] == meta0["cost_usd"] and meta["timings"] == meta0["timings"]
    assert r2["info"]["n_epochs"] == r["info"]["n_epochs"]


def test_outputs_and_features_written_without_masks(source, tmp_path, monkeypatch):
    r, _ = run(source, tmp_path, monkeypatch)
    out = tmp_path / "out"
    assert {p.name for p in out.iterdir()} == {"features.npz", "tracking.json", "tracks.npz", "raw_c000.jsonl.gz",
                                               "raw_c001.jsonl.gz", "raw_c002.jsonl.gz"}
    f = np.load(out / "features.npz")
    assert f["a_cx"].shape == (N,) and "max_toward_speed" in f.files and "id_epoch" in f.files
    assert "a_bx" in f.files and "vel_ok" in f.files
    tr = np.load(out / "tracks.npz")
    assert set(np.unique(tr["label"])) == {"fly_a", "fly_b"} and tr["frame"].min() >= START
    assert r["cost_usd"] > 0 and r["timings"]["x_realtime"] >= 0


def test_a_failing_sam_call_closes_the_raw_file(source, tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "parse_line", fake_parse_line)

    class Boom(FakeGateway):
        def sam(self, *a, **k):
            raise RuntimeError("SAM down")

    chunk = FlyChunk(0, str(tmp_path / "c.mp4"), 0, 10, 0, 10, START, 1, 1)
    with pytest.raises(RuntimeError, match="SAM down"):
        fp.track_fly_chunk(Boom(FIRSTS), chunk, "fruit fly", "ablation", True, tmp_path / "raw.jsonl.gz")
    import gzip
    assert gzip.open(tmp_path / "raw.jsonl.gz", "rt").read() == ""    # file is complete and closed, not truncated


# ---- shape features on real small masks ------------------------------------------------------------------

def test_solidity_thin_features_and_body_centroid_on_real_masks():
    m = np.zeros((40, 60), np.uint8)
    cv2.ellipse(m, (30, 20), (13, 5), 0, 0, 360, 1, -1)
    rect = np.ones((10, 20), np.uint8)
    assert fp.solidity(rect) == pytest.approx(1.0) and fp.thin_features(rect, 9.5, 4.5, 20.0)[0] == 0.0
    assert fp.solidity(m) > 0.95
    thin0, reach0 = fp.thin_features(m, 30.0, 20.0, 26.0)
    assert thin0 < 0.02 and reach0 == pytest.approx(1.0, abs=0.1)
    # add a 1-px-wide spur (an extended wing seen edge-on): solidity falls, thin_frac and reach rise
    spur = m.copy()
    spur[20, 43:58] = 1
    assert fp.solidity(spur) < fp.solidity(m) - 0.1
    thin1, reach1 = fp.thin_features(spur, 30.0, 20.0, 26.0)
    assert thin1 > 0.05 and reach1 > 1.8
    # the body centroid ignores the spur while the plain centroid is dragged toward it
    full_x = np.nonzero(spur)[1].mean()
    bx, by = fp.body_centroid(spur, 100, 200)
    assert abs((bx - 100) - 30.0) < 0.3 and abs((by - 200) - 20.0) < 0.3
    assert full_x - 30.0 > 0.8
    # a mask the opening erases falls back to the whole mask
    tiny = np.zeros((6, 6), np.uint8)
    tiny[2, 1:5] = 1
    assert fp.body_centroid(tiny, 0, 0) == pytest.approx((2.5, 2.0))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_gateway_uses_the_given_root_ledger(tmp_path):
    with pytest.raises(RuntimeError, match="ETHO_ROOT"):
        fp.make_gateway(tmp_path)                                      # no ops/ here: not the shared checkout
    (tmp_path / "ops").mkdir()
    gw = fp.make_gateway(tmp_path)
    assert gw.cfg.root == tmp_path.resolve() and gw.cfg.ledger_path == tmp_path.resolve() / "ops" / "spend.jsonl"


def test_estimate_matches_the_dev_runs_and_needs_approval_over_five_dollars(tmp_path, monkeypatch):
    frames, usd = fp.estimate_sam_cost(22500, 31500, 1, overlap_s=1.0)
    assert frames == 9120 and usd == pytest.approx(1.824)           # exactly what the two dev movies were billed
    assert fp.estimate_sam_cost(22500, 31500, 3, overlap_s=1.0)[0] < 3200
    rf = load_script("run_fly")
    monkeypatch.setenv("ETHO_ROOT", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["run_fly", "track", "--movies", "1", "2", "3", "--run", "X"])  # 3 x $1.82
    with pytest.raises(SystemExit, match="needs approval"):
        rf.main()
    monkeypatch.setattr("sys.argv", ["run_fly", "track", "--movies", "6", "--run", "X"])
    with pytest.raises(SystemExit, match="test split"):
        rf.main()
    monkeypatch.delenv("ETHO_ROOT")
    monkeypatch.setattr("sys.argv", ["run_fly", "track", "--movies", "1", "--run", "X"])
    with pytest.raises(SystemExit, match="ETHO_ROOT"):
        rf.main()


def test_public_data_and_gateway_are_required_arguments(source, tmp_path):
    with pytest.raises(TypeError):
        fp.track_fly_movie(source, tmp_path / "o", gw=object())          # public_data missing
    with pytest.raises(TypeError):
        fp.track_fly_movie(source, tmp_path / "o", public_data=True)     # gw missing


def test_product_code_never_touches_ground_truth_files():
    """ADR-002: *_actions.mat / *_track.mat / *_feat.mat are eval-only. No fly module may load .mat files."""
    root = Path(__file__).resolve().parents[1]
    for f in list((root / "etho").glob("tracking/fly_*.py")) + [root / "scripts/run_fly.py",
                                                                 root / "scripts/fly_probe.py"]:
        text = f.read_text()
        assert not re.search(r"loadmat|scipy\.io|h5py|mat73|\.mat['\"]", text), f
        assert not re.search(r"_actions|_feat\.|_track\.", text.replace("*_actions/_track/_feat.mat", "")), f
