"""ffprobe and ffmpeg on an upload run under bubblewrap on a public deployment: no network, one directory."""

import shutil
import subprocess

import pytest

from etho.app.config import AppConfig
from etho.app.validate import ValidationError, check_video, confined, probe, remux_clean
from etho.rules.sandbox import _probe_bwrap
from tests.app_helpers import make_video


def test_localhost_commands_are_unchanged(tmp_path):
    assert confined(["ffprobe", "x"], None) == ["ffprobe", "x"]


def test_the_confined_command_shape(tmp_path):
    if not shutil.which("bwrap"):
        pytest.skip("bwrap not installed")
    cmd = confined(["ffprobe", "x.mp4"], tmp_path)
    assert cmd[-2:] == ["ffprobe", "x.mp4"] and "--unshare-all" in cmd and "--die-with-parent" in cmd
    assert "--clearenv" in cmd and "--share-net" not in cmd
    binds = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--bind"]
    assert binds == [str(tmp_path.resolve())]                      # the only writable path is the job's directory
    assert "/home" not in " ".join(cmd) and "/etc/passwd" not in cmd


def test_a_missing_bwrap_refuses_instead_of_running_unconfined(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValidationError) as e:
        confined(["ffprobe"], tmp_path)
    assert e.value.status == 503


needs_bwrap = pytest.mark.skipif(not _probe_bwrap()[0], reason="bubblewrap is not usable here")


@needs_bwrap
def test_probe_and_remux_work_under_bwrap(tmp_path):
    v = make_video(tmp_path / "up.mp4", 3)
    assert probe(v, confine=True).frames == 30
    assert check_video(v, AppConfig(root=tmp_path), confine=True).frames == 30
    remux_clean(v, tmp_path / "clean.mp4", AppConfig(root=tmp_path), confine=True)
    assert (tmp_path / "clean.mp4").stat().st_size > 0
    (tmp_path / "junk.mp4").write_text("not a video")
    with pytest.raises(ValidationError):
        probe(tmp_path / "junk.mp4", confine=True)


@needs_bwrap
def test_the_confined_process_sees_only_its_directory_and_no_network(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    outside.write_text("secret")
    job = tmp_path / "job"
    job.mkdir()
    (job / "mine.txt").write_text("mine")
    run = lambda cmd: subprocess.run(confined(cmd, job), capture_output=True, text=True)    # noqa: E731
    assert run(["cat", str(job / "mine.txt")]).stdout == "mine"
    assert run(["cat", str(outside)]).returncode != 0                                   # not visible
    assert run(["ls", "/home"]).returncode != 0 and run(["ls", "/etc/passwd"]).returncode != 0
    assert run(["sh", "-c", f"echo x > {job}/new.txt"]).returncode == 0 and (job / "new.txt").exists()
    net = run(["sh", "-c", "cat /proc/net/dev | tail -n +3 | grep -v '^ *lo:' | wc -l"])
    assert net.stdout.strip() == "0"                                                     # no network interface but lo
