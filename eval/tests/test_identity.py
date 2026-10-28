"""Tests for eval/identity.py on synthetic tracks and keypoints. No network, no real labels."""

import json

import numpy as np
import pytest

from eval import identity
from eval.tests.synth import (
    A,
    I,
    M,
    O,
    keypoints_from_paths,
    mouse_paths,
    save_tracks,
    tracks_from_paths,
    write_dataset,
)

T = 900


def seq_data(T=T):
    paths = mouse_paths(T)
    kp, sc = keypoints_from_paths(paths)
    labels = np.array(([A] * 150 + [I] * 300 + [M] * 150 + [O] * 300)[:T], np.int8)
    return paths, kp, sc, labels


def test_keypoint_centroid_definitions():
    paths, kp, sc, _ = seq_data(30)
    cen, ok = identity.keypoint_centroids(kp, sc, "all7")
    assert np.allclose(cen, paths) and ok.all()
    cen6, _ = identity.keypoint_centroids(kp, sc, "no_tail")
    assert not np.allclose(cen6, paths)
    sc2 = sc.copy()
    sc2[:, 1] = 0.3
    _, ok2 = identity.keypoint_centroids(kp, sc2, "all7", 0.5)
    assert ok2[:, 0].all() and not ok2[:, 1].any()


def test_match_frame_gate_and_ambiguity():
    kp = np.array([[100.0, 100.0], [400.0, 100.0]])
    ok = np.array([True, True])
    a, d, amb = identity.match_frame(np.array([[402.0, 100.0], [101.0, 100.0]]), kp, ok, 60, 10)
    assert list(a) == [1, 0] and not amb.any() and d[0] == pytest.approx(1.0)
    a, _, _ = identity.match_frame(np.array([[250.0, 100.0]]), kp, ok, 60, 10)  # outside both gates
    assert list(a) == [-1, -1]
    a, _, _ = identity.match_frame(np.array([[110.0, 100.0], [120.0, 100.0], [395.0, 100.0]]), kp, ok, 60, 10)
    assert list(a) == [0, 2]  # spurious 3rd track left unmatched
    close = np.array([[100.0, 100.0], [104.0, 100.0]])  # mice nearly coincide (mount)
    a, _, amb = identity.match_frame(np.array([[101.0, 100.0], [103.0, 100.0]]), close, ok, 60, 10)
    assert list(a) == [0, 1] and amb.all()
    a, _, amb = identity.match_frame(np.array([[102.0, 100.0]]), close, ok, 60, 10)  # one blob on both
    assert (a >= 0).sum() == 1 and amb.any()
    a, _, _ = identity.match_frame(np.array([[101.0, 100.0]]), kp, np.array([False, True]), 60, 10)
    assert list(a) == [-1, -1]  # ineligible keypoints are never matched


def summarize_one(tr, kp, sc, labels, **kw):
    return identity.summarize([identity.evaluate_sequence(tr, kp, sc, labels, **kw)])


def test_perfect_tracks():
    paths, kp, sc, labels = seq_data()
    s = summarize_one(tracks_from_paths(paths), kp, sc, labels)
    o = s["overall"]
    assert o["identity_error_rate"] == 0 and o["coverage_both_labeled"] == 1.0
    assert o["coverage_both_matched_correct"] == 1.0 and o["swap_events"] == 0
    assert o["unknown_rate_tracks"] == 0 and o["identity_eval_samples"] == 2 * (T // 3)
    assert o["kp_both_eligible_rate"] == 1.0
    assert o["detections"]["2"] == 1.0
    assert set(s["by_behavior"]) == {"attack", "investigation", "mount", "other"}
    assert s["by_behavior"]["mount"]["n_samples"] == 50


def test_swapped_segment_counts_errors_and_swap_events():
    paths, kp, sc, labels = seq_data()
    tr = tracks_from_paths(paths)
    seg = (tr["frame"] >= 300) & (tr["frame"] < 450)
    tr["label"] = tr["label"].astype("<U8")
    tr["label"][seg] = np.where(tr["label"][seg] == "resident", "intruder", "resident")
    o = summarize_one(tr, kp, sc, labels)["overall"]
    assert o["identity_errors"] == 2 * 50
    assert o["identity_error_rate"] == pytest.approx(100 / 600)
    assert o["swap_events"] == 2  # swap at 300 and swap back at 450, each counted once for both mice
    assert o["swap_events_per_min"] == pytest.approx(2 / (T / 30 / 60))
    assert o["coverage_both_labeled"] == 1.0 and o["coverage_both_matched_correct"] == pytest.approx(250 / 300)


def test_unknown_labels_are_not_errors_and_skip_in_swaps():
    paths, kp, sc, labels = seq_data()
    tr = tracks_from_paths(paths)
    tr["label"] = tr["label"].astype("<U8")
    sel = (tr["track_id"] == 1) & (tr["frame"] < 450)
    tr["label"][sel] = "unknown"
    o = summarize_one(tr, kp, sc, labels)["overall"]
    assert o["identity_errors"] == 0 and o["matched_unknown"] == 150
    assert o["unknown_rate_tracks"] == pytest.approx(150 / 600)
    assert o["coverage_both_labeled"] == pytest.approx(0.5)
    assert o["swap_events"] == 0


def test_missing_frames_lower_coverage_and_per_behavior():
    paths, kp, sc, labels = seq_data()
    tr = tracks_from_paths(paths)
    drop = (tr["track_id"] == 1) & (tr["frame"] >= 450) & (tr["frame"] < 600)  # intruder gone during mount
    tr = {k: v[~drop] for k, v in tr.items()}
    s = summarize_one(tr, kp, sc, labels)
    assert s["by_behavior"]["mount"]["coverage_both_labeled"] == 0.0
    assert s["by_behavior"]["attack"]["coverage_both_labeled"] == 1.0
    assert s["overall"]["coverage_both_labeled"] == pytest.approx(250 / 300)
    assert s["by_behavior"]["mount"]["detections"]["1"] == 1.0
    # whole grid frames absent from the table count as zero detections
    tr2 = tracks_from_paths(paths)
    keep = tr2["frame"] >= 30
    s2 = summarize_one({k: v[keep] for k, v in tr2.items()}, kp, sc, labels)
    assert s2["overall"]["detections"]["0"] == pytest.approx(10 / 300)


def test_coverage_correct_excludes_ineligible_keypoints():
    paths, kp, sc, labels = seq_data()
    sc = sc.copy()
    sc[:300, 0] = 0.2  # resident keypoints unreliable on the first 300 frames (100 samples)
    o = summarize_one(tracks_from_paths(paths), kp, sc, labels)["overall"]
    assert o["kp_both_eligible_rate"] == pytest.approx(200 / 300)
    assert o["coverage_both_matched_correct"] == 1.0
    assert o["identity_eval_samples"] == 300 + 200


def test_alignment_recovers_lag():
    paths, kp, sc, _ = seq_data()
    for lag in (-4, 0, 5):
        a = identity.alignment_check(tracks_from_paths(paths, lag=lag), kp, sc)
        for role in ("resident", "intruder"):
            assert a[role]["best_lag"] == lag
            assert a[role]["best_median_px"] == pytest.approx(0, abs=1e-6)
            assert a[role]["margin_px"] > 0


def test_run_end_to_end(tmp_path):
    paths, _, _, labels = seq_data()
    sf = write_dataset(tmp_path, {"mouse900_task1_annotator1": labels})
    td = tmp_path / "tracks"
    td.mkdir()
    save_tracks(td / "mouse900.npz", tracks_from_paths(mouse_paths(T, 0)))
    log = tmp_path / "runs.jsonl"
    rec = identity.run("dev", td, labels_dir=tmp_path / "labels", split_file=sf, log_path=log,
                       out_dir=tmp_path / "o")
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in lines] == ["started", "ok"] and lines[0]["kind"] == "identity"
    o = rec["metrics"]["overall"]
    # float16 storage of keypoints in the synthetic labels file costs < 1 px
    assert o["identity_error_rate"] == 0 and o["coverage_both_labeled"] == 1.0
    assert rec["metrics"]["alignment"]["mouse900_task1_annotator1"]["resident"]["best_lag"] == 0
    assert "all7" in rec["metrics"]["centroid_definition_median_px"]["mouse900_task1_annotator1"]
    assert "id err" in identity.format_report(rec)


def test_bad_label_rejected(tmp_path):
    tr = tracks_from_paths(mouse_paths(30))
    tr["label"] = np.array(["black"] * len(tr["frame"]))
    save_tracks(tmp_path / "x.npz", tr)
    with pytest.raises(ValueError):
        identity.read_tracks(tmp_path / "x.npz")
