"""Overlay rendering from a tiny synthetic RLE fixture (no SAM, no network)."""

import json
import subprocess

import cv2
import numpy as np
import pytest

from etho.app.overlay import ROLE_BGR, draw_frame, iter_frame_masks, render_overlay, track_roles
from tests.app_helpers import FPS, H, W, make_video, write_tracking

N = 30


@pytest.fixture
def fixture(tmp_path):
    video = make_video(tmp_path / "chunk.mp4", N / FPS)
    seq = tmp_path / "run" / "video"
    write_tracking(seq, video, N)
    return video, seq


def test_masks_stream_in_frame_order_and_only_for_the_two_animals(fixture):
    video, seq = fixture
    roles = track_roles(seq)
    assert roles["c0:1"] == "resident" and roles["c0:3"] == "unknown"
    frames = list(iter_frame_masks(seq))
    assert [f for f, _ in frames] == list(range(N))
    f0 = frames[0][1]
    assert sorted(r for r, _, _ in f0) == ["intruder", "resident"]            # the "unknown" track is dropped
    role, (left, top), m = f0[0]
    assert m.shape == (16, 24) and m.all() and (left, top) == (30, 50)


def test_draw_frame_outlines_roles_and_burns_in_the_label():
    img = np.zeros((H, W, 3), np.uint8)
    m = np.ones((16, 24), np.uint8)
    draw_frame(img, [("resident", (30, 50), m), ("intruder", (90, 50), m)], "attack", (200, 100, 20), 1.5)
    assert tuple(img[50, 40]) == ROLE_BGR["resident"]                         # top edge of the resident box
    assert tuple(img[50, 100]) == ROLE_BGR["intruder"]
    assert tuple(img[60, 40]) == (0, 0, 0)                                    # interior is not filled
    assert (img[8:30, 8:100] == (200, 100, 20)).all(axis=2).any()             # label chip color
    assert (img[:30, :110] != 0).any()


def test_render_overlay_writes_h264_with_one_frame_per_sample(fixture, tmp_path):
    video, seq = fixture
    labels = np.full(N, 2, np.int8)       # 2 = "other" for two behaviors
    labels[10:20] = 1
    out = tmp_path / "out" / "overlay.mp4"
    seen = []
    r = render_overlay([video], seq, labels, ["a", "b"], FPS, out, max_width=80, progress=lambda i, n: seen.append(i))
    assert r["frames"] == N and out.exists() and not list(out.parent.glob("*.part*"))
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=codec_name,width,height,nb_frames,r_frame_rate,pix_fmt", "-of", "json",
                                   str(out)], capture_output=True, text=True, check=True).stdout)["streams"][0]
    assert p["codec_name"] == "h264" and p["pix_fmt"] == "yuv420p" and int(p["nb_frames"]) == N
    assert p["r_frame_rate"] == "10/1" and p["width"] == 80 and p["height"] == 60   # downscaled to max_width
    # the label burned into the frame differs between a "b" frame and an "other" frame
    cap = cv2.VideoCapture(str(out))
    frames = [cap.read()[1] for _ in range(N)]
    chip = lambda f: f[2:14, 2:30].astype(int)       # noqa: E731
    assert np.abs(chip(frames[15]) - chip(frames[5])).mean() > 5


def test_overlay_without_masks_still_renders(fixture, tmp_path):
    video, seq = fixture
    for p in seq.glob("raw_c*.jsonl.gz"):
        p.unlink()
    r = render_overlay([video], seq, np.zeros(N, np.int8), ["a"], FPS, tmp_path / "o.mp4")
    assert r["frames"] == N


def test_overlay_fails_cleanly_when_frames_are_unreadable(tmp_path, fixture):
    _, seq = fixture
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"nope")
    with pytest.raises(RuntimeError, match="no video frames"):
        render_overlay([bad], seq, np.zeros(N, np.int8), ["a"], FPS, tmp_path / "o2.mp4")
    assert not (tmp_path / "o2.mp4").exists()
