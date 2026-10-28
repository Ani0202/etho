import json

import pytest

from etho.app.config import AppConfig
from etho.app.validate import (
    ValidationError,
    check_definitions,
    check_extension,
    check_text,
    check_video,
    normalize_name,
    probe,
)
from tests.app_env import DEFS, make_app, no_jobs, post_job
from tests.app_helpers import make_video


def test_extension_whitelist():
    for ok in ("a.mp4", "A.MOV", "x.avi"):
        check_extension(ok)
    for bad in ("a.mkv", "a.txt", "a", "a.mp4.exe", ""):
        with pytest.raises(ValidationError) as e:
            check_extension(bad)
        assert e.value.status == 415


def test_ffprobe_duration_limit(tmp_path):
    v = make_video(tmp_path / "v.mp4", 8)
    info = probe(v)
    assert info.duration_s == pytest.approx(8, abs=0.3) and info.width == 160 and info.height == 120
    check_video(v, AppConfig(root=tmp_path, max_duration_s=10))
    with pytest.raises(ValidationError) as e:
        check_video(v, AppConfig(root=tmp_path, max_duration_s=5))
    assert e.value.status == 413 and e.value.code == "too_long" and "limit is 5 s" in e.value.message


def test_unreadable_file_is_rejected(tmp_path):
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"this is not a video" * 100)
    with pytest.raises(ValidationError) as e:
        probe(p)
    assert e.value.code == "unreadable"


def test_definitions_rules():
    cfg = AppConfig()
    out = check_definitions({"Self Grooming": "The animal licks its own fur for a while."}, cfg)
    assert list(out) == ["self_grooming"]
    assert check_definitions([{"name": "a1", "definition": "x" * 20}], cfg) == {"a1": "x" * 20}
    for bad in ({}, {"none": "a long enough definition"}, {"1x": "a long enough definition"},
                {"a": "short"}, {"a": "x" * 2001}, {f"b{i}": "a long enough definition" for i in range(9)},
                {"a b": "a long enough definition", "a_b": "a long enough definition"}):
        with pytest.raises(ValidationError):
            check_definitions(bad, cfg)
    assert normalize_name("  Mount!  ") == "mount"
    assert check_text("", "subject", "dflt") == "dflt"
    with pytest.raises(ValidationError):
        check_text("x" * 201, "subject", "d")


def test_api_rejects_oversize_before_any_spend(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"max_upload_mb": 0.1})
    big = b"x" * 1_500_000  # declared length is over limit + multipart slack: refused before parsing
    r = client.post("/api/jobs", data={"definitions": json.dumps(DEFS)}, files={"video": ("a.mp4", big)})
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    mid = b"x" * 300_000   # passes the declared-length gate; the streamed byte count catches it
    r = client.post("/api/jobs", data={"definitions": json.dumps(DEFS)}, files={"video": ("a.mp4", mid)})
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    assert not c.cfg.ledger_path.exists() and no_jobs(c)  # nothing spent, nothing kept


def test_api_rejects_type_duration_and_garbage_before_any_spend(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"max_duration_s": 5.0})
    r = post_job(client, c.video, filename="clip.mkv")
    assert r.status_code == 415 and r.json()["error"] == "bad_type"
    r = post_job(client, c.video)  # 6 s > 5 s
    assert r.status_code == 413 and r.json()["error"] == "too_long"
    assert post_job(client, c.video, definitions="not json").status_code == 400
    r = post_job(client, c.video, defs={})
    assert r.status_code == 400 and "behavior" in r.json()["message"]
    r = client.post("/api/jobs", data={"definitions": json.dumps(DEFS)},
                    files={"video": ("x.mp4", b"not a video", "video/mp4")})
    assert r.status_code == 400 and r.json()["error"] == "unreadable"
    assert not c.cfg.ledger_path.exists() and no_jobs(c)  # nothing spent, nothing kept
