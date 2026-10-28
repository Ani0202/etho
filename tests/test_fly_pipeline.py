"""Fly pipeline end to end with a fake gateway: chunking, per-chunk SAM, stitching, table, outputs.

The fake SAM returns two ellipse "flies" on known trajectories. SAM object ids are deliberately
reshuffled per chunk (as they would be: each parallel request numbers its own tracks), and the
first samples of each chunk are missed (acquisition lag), so the table is right only if the
Hungarian stitching over the overlap works.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from etho.tracking import fly_pipeline as fp
from etho.tracking.fly_video import _Writer
from etho.tracking.sam_parse import ObjectFrame, mask_moments

START, N = 30, 270                    # window: source frames [30, 300) at 30 fps, stride 1
CHUNK_S, OVERLAP_S = 3.0, 0.5         # 90-sample chunks, 15-sample overlap -> 3 chunks
LAG = 3


def positions(g):
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
    """Stands in for etho.gateway.Gateway.sam: emits one JSON line per chunk-local sample."""

    def __init__(self):
        self.calls = []

    def sam(self, phrase, *, tag, video, n_frames, public_data, metadata, on_line, caller):
        self.calls.append({"phrase": phrase, "tag": tag, "public_data": public_data, "n_frames": n_frames})
        ci = int(re.search(r"c(\d+)\.mp4", str(video)).group(1))
        first = 0 if ci == 0 else ci * 90 - 15
        swap = ci % 2 == 1                                   # ids reversed in odd chunks
        for local in range(n_frames):
            if local < LAG:
                continue
            p1, p2 = positions(first + local)
            ids = ("1", "0") if swap else ("0", "1")
            objs = sorted([(ids[0], tuple(p1)), (ids[1], tuple(p2))])   # emitted in id order, like SAM
            on_line(json.dumps({"frame": local, "objs": dict(objs)}))
        return SimpleNamespace(cost_usd=n_frames * 0.0002, cache_hit=False, frames_billed=max(n_frames, 50))


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "movie.mp4"
    w = _Writer(p, 144, 144, 30.0, crf=20)
    for _ in range(330):
        w.write(np.full((144, 144, 3), 200, np.uint8))
    w.close()
    return p


def run(source, tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "parse_line", fake_parse_line)
    gw = FakeGateway()
    r = fp.track_fly_movie(source, tmp_path / "out", gw=gw, start=START, stop=START + N, stride=1, scale=1,
                           chunk_s=CHUNK_S, overlap_s=OVERLAP_S, chunk_dir=tmp_path / "chunks", workers=2)
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
    p1 = np.array([positions(i)[0] for i in g])
    p2 = np.array([positions(i)[1] for i in g])
    # slot a is always the same physical fly (fly 1 here, since chunk 0 listed it first)
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
    assert [(c["first"], c["own0"], c["own1"]) for c in meta["chunk_files"]] == [(0, 0, 90), (75, 90, 180),
                                                                                  (165, 180, 270)]
    assert meta["phrase"] == "fruit fly" and meta["frames_billed"] >= N


def test_identity_unknown_where_flies_are_within_a_body_length(source, tmp_path, monkeypatch):
    r, _ = run(source, tmp_path, monkeypatch)
    t, bl = r["table"], r["info"]["body_len_px"]
    assert 24 < bl < 28
    pres = (t["a_present"] > 0) & (t["b_present"] > 0)
    d = np.hypot(t["a_cx"] - t["b_cx"], t["a_cy"] - t["b_cy"])
    expect = pres & (d < bl)
    assert expect.any() and (~expect[pres]).any()          # the scenario has both close and far samples
    assert (t["id_unknown"][pres] == expect[pres]).all()
    assert r["info"]["frac_id_unknown_of_both"] == pytest.approx(expect[pres].mean())


def test_outputs_and_features_written_without_masks(source, tmp_path, monkeypatch):
    r, _ = run(source, tmp_path, monkeypatch)
    out = tmp_path / "out"
    assert {p.name for p in out.iterdir()} == {"features.npz", "tracking.json", "tracks.npz", "raw_c000.jsonl.gz",
                                               "raw_c001.jsonl.gz", "raw_c002.jsonl.gz"}
    f = np.load(out / "features.npz")
    assert f["a_cx"].shape == (N,) and "max_toward_speed" in f.files and "id_epoch" in f.files
    tr = np.load(out / "tracks.npz")
    assert set(np.unique(tr["label"])) == {"fly_a", "fly_b"} and tr["frame"].min() >= START
    assert r["cost_usd"] > 0 and r["timings"]["x_realtime"] >= 0


def test_product_code_never_touches_ground_truth_files():
    """ADR-002: *_actions.mat / *_track.mat / *_feat.mat are eval-only. No fly module may load .mat files."""
    root = Path(__file__).resolve().parents[1] / "etho"
    for f in list(root.glob("tracking/fly_*.py")) + [Path(__file__).resolve().parents[1] / "scripts/run_fly.py",
                                                      Path(__file__).resolve().parents[1] / "scripts/fly_probe.py"]:
        text = f.read_text()
        assert not re.search(r"loadmat|scipy\.io|h5py|mat73|\.mat['\"]", text), f
        assert not re.search(r"_actions|_feat\.|_track\.", text.replace("*_actions/_track/_feat.mat", "")), f
