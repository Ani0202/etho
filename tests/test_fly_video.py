"""Fly video adapter: window, stride, upscale, overlapping chunks, exact sample<->source-frame mapping."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from etho.tracking.fly_video import _Writer, plan_chunks, prepare_fly_chunks, window_samples
from etho.tracking.pipeline import count_frames


def make_source(path, n=400, size=144):
    """Frame i carries its own index as a 4x4 white block at ((i % 36) * 4, (i // 36) * 4)."""
    w = _Writer(path, size, size, 30.0, crf=10)
    for i in range(n):
        f = np.zeros((size, size, 3), np.uint8)
        x, y = (i % 36) * 4, (i // 36) * 4
        f[y:y + 4, x:x + 4] = 255
        w.write(f)
    w.close()


def decode_index(frame, scale):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    ys, xs = np.nonzero(g > 128)
    x, y = xs.mean() / scale, ys.mean() / scale
    return int(x // 4) + 36 * int(y // 4)


def read_all(path):
    cap = cv2.VideoCapture(str(path))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            return out
        out.append(f)


def test_window_samples_and_plan():
    assert window_samples(22500, 31500, 1) == 9000
    assert window_samples(22500, 31500, 3) == 3000
    assert window_samples(0, 10, 3) == 4  # frames 0, 3, 6, 9
    # 5 min at 30 fps: five 1800-sample chunks, 30-sample lead-in on all but the first
    plan = plan_chunks(9000, 30.0, 60.0, 1.0)
    assert plan[0] == (0, 0, 1800) and plan[1] == (1770, 1800, 3600) and plan[-1] == (7170, 7200, 9000)
    # a short tail chunk keeps its own range
    assert plan_chunks(100, 10.0, 4.0, 1.0)[-1] == (70, 80, 100)


def test_chunks_cover_window_exactly_once_and_map_to_source_frames(tmp_path):
    src = tmp_path / "m.mp4"
    make_source(src, 400)
    start, stop, stride, scale = 50, 350, 3, 2          # 100 samples at 10 fps, upscaled 2x
    chunks = prepare_fly_chunks(src, tmp_path / "ch", start=start, stop=stop, stride=stride, scale=scale,
                                chunk_s=4, overlap_s=1, src_fps=30.0)
    assert [(c.first, c.own0, c.own1) for c in chunks] == [(0, 0, 40), (30, 40, 80), (70, 80, 100)]
    owned = [g for c in chunks for g in range(c.own0, c.own1)]
    assert owned == list(range(100))                     # every sample has exactly one owner
    for c in chunks:
        assert count_frames(Path(c.path)) == c.n_samples
        frames = read_all(c.path)
        assert len(frames) == c.n_samples and frames[0].shape[:2] == (288, 288)
        for local in (0, 1, c.n_samples // 2, c.n_samples - 1):
            assert decode_index(frames[local], scale) == c.src_frame(local) == start + stride * (c.first + local)
    # overlap samples are the same source frames in both chunks
    a, b = chunks[0], chunks[1]
    assert a.src_frame(35) == b.src_frame(5)


def test_native_scale_and_cache(tmp_path):
    src = tmp_path / "m.mp4"
    make_source(src, 200)
    kw = dict(start=10, stop=130, stride=1, scale=1, chunk_s=2, overlap_s=0.5, src_fps=30.0)
    c1 = prepare_fly_chunks(src, tmp_path / "ch", **kw)
    assert [(c.first, c.own0, c.own1) for c in c1] == [(0, 0, 60), (45, 60, 120)]
    assert read_all(c1[0].path)[0].shape[:2] == (144, 144)
    m = [Path(c.path).stat().st_mtime_ns for c in c1]
    c2 = prepare_fly_chunks(src, tmp_path / "ch", **kw)
    assert c2 == c1 and [Path(c.path).stat().st_mtime_ns for c in c2] == m
    # a different window is a different cache entry
    c3 = prepare_fly_chunks(src, tmp_path / "ch", **{**kw, "start": 20})
    assert c3[0].path != c1[0].path and decode_index(read_all(c3[0].path)[0], 1) == 20


def test_stale_chunks_json_is_not_trusted_when_the_video_files_are_gone(tmp_path):
    src = tmp_path / "m.mp4"
    make_source(src, 200)
    kw = dict(start=10, stop=130, stride=1, scale=1, chunk_s=2, overlap_s=0.5, src_fps=30.0)
    c1 = prepare_fly_chunks(src, tmp_path / "ch", **kw)
    Path(c1[1].path).unlink()                          # cache cleaned, chunks.json left behind
    c2 = prepare_fly_chunks(src, tmp_path / "ch", **kw)
    assert c2 == c1 and all(Path(c.path).stat().st_size > 0 for c in c2)
    assert decode_index(read_all(c2[1].path)[0], 1) == 10 + c2[1].first
    Path(c2[0].path).write_bytes(b"")                  # a truncated (empty) chunk is not trusted either
    c3 = prepare_fly_chunks(src, tmp_path / "ch", **kw)
    assert len(read_all(c3[0].path)) == c3[0].n_samples


def test_failure_mid_cut_raises_and_leaves_no_chunks_json_or_ffmpeg(tmp_path):
    import subprocess

    src = tmp_path / "m.mp4"
    make_source(src, 100)
    with pytest.raises(RuntimeError, match="ran out of frames"):   # the window runs past the end of the movie
        prepare_fly_chunks(src, tmp_path / "ch", start=10, stop=500, stride=1, scale=1, chunk_s=2, overlap_s=0.5)
    assert not list((tmp_path / "ch").glob("*/chunks.json"))
    with pytest.raises(RuntimeError, match="fewer than"):
        prepare_fly_chunks(src, tmp_path / "ch", start=400, stop=500, stride=1, scale=1)
    out = subprocess.run(["pgrep", "-f", f"ffmpeg.*{tmp_path}"], capture_output=True, text=True).stdout
    assert out.strip() == ""                                       # no orphaned ffmpeg writers
