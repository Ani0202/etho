"""Retention must never delete a directory another project, a showcase or the benchmark relies on, and must not run
shared-directory sweeps or purge a project while a job could be using it."""

import json
import os
import time

import pytest

from etho.app.jobs import Job, QueueFull
from etho.app.retention import CHUNK_REFS, note_chunk_dirs
from tests.app_env import make_app, post_job, wait_for, wait_terminal
from tests.test_app_hosting import BODY, REVISE, hosted, unlock, wait_done
from tests.test_app_showcase import build_showcase


def test_a_showcase_revision_does_not_record_or_delete_the_showcases_chunks(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    unlock(client)
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert wait_terminal(c.manager, r.json()["job_id"]).status == "done"
    fork = c.cfg.jobs_dir / r.json()["project_id"]
    shared = [d for d in c.cfg.chunk_cache_dir.iterdir()]                   # cut from the showcase's source video
    assert shared
    refs = json.loads((fork / CHUNK_REFS).read_text()) if (fork / CHUNK_REFS).exists() else {}
    assert not refs.get("chunk_dirs")                                       # nothing recorded for the fork
    assert c.manager.cleanup(time.time() + 25 * 3600) == [fork.name]          # the purge itself (no orphan sweep)
    assert not fork.exists() and all(d.exists() for d in shared)             # the shared chunk dir is still there


def test_only_chunks_cut_from_the_projects_own_video_are_recorded(tmp_path):
    from etho.app.config import AppConfig
    cfg = AppConfig(root=tmp_path)
    base = tmp_path / "jobs" / "j1"
    base.mkdir(parents=True)
    own, other = base / "clean.mp4", tmp_path / "showcase" / "source.mp4"
    other.parent.mkdir()
    own.write_bytes(b"x")
    other.write_bytes(b"x")
    cdir = cfg.chunk_cache_dir / "abc"
    cdir.mkdir(parents=True)
    (cdir / "c000.mp4").write_bytes(b"x")
    note_chunk_dirs(cfg, base, [cdir / "c000.mp4"], src=other)
    assert not (base / CHUNK_REFS).exists()
    link = base / "linked.mp4"
    os.symlink(other, link)
    note_chunk_dirs(cfg, base, [cdir / "c000.mp4"], src=link)                # a symlink out of the project: refused
    assert not (base / CHUNK_REFS).exists()
    note_chunk_dirs(cfg, base, [cdir / "c000.mp4"], src=own)
    assert json.loads((base / CHUNK_REFS).read_text())["chunk_dirs"] == [str(cdir.resolve())]


def test_purge_deletes_only_direct_children_of_the_apps_own_chunk_dir(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    root = c.cfg.chunk_cache_dir
    mine = next(root.iterdir())
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    deep = root / "other" / "nested"
    deep.mkdir(parents=True)
    link = root / "link"
    os.symlink(outside, link)
    (c.cfg.jobs_dir / jid / CHUNK_REFS).write_text(json.dumps({"chunk_dirs": [
        str(root), str(root.parent), str(deep), str(link), str(outside), str(mine)]}))
    c.manager.cleanup(time.time() + 25 * 3600)
    assert not mine.exists()                                                  # its own: deleted
    assert root.exists() and root.parent.exists() and deep.exists() and (outside / "keep.txt").exists() and link.is_symlink()


def test_the_benchmarks_cache_chunks_is_never_used_or_touched_in_either_mode(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)                              # localhost mode
    bench = tmp_path / "cache" / "chunks" / "calms21"
    bench.mkdir(parents=True)
    (bench / "c000.mp4").write_bytes(b"benchmark")
    old = time.time() - 90 * 24 * 3600
    os.utime(bench / "c000.mp4", (old, old))
    os.utime(bench, (old, old))
    assert c.cfg.chunk_cache_dir == c.cfg.data_dir / "work" / "cache" / "chunks" != tmp_path / "cache" / "chunks"
    jid = post_job(client, c.video).json()["id"]
    assert wait_terminal(c.manager, jid).status == "done"
    assert any(c.cfg.chunk_cache_dir.iterdir())                              # the app cut its chunks in its own dir
    assert [p.name for p in (tmp_path / "cache" / "chunks").iterdir()] == ["calms21"]
    c.app.state.retention.run_once(time.time() + 400 * 24 * 3600)             # everything is "old" now
    assert not (c.cfg.jobs_dir / jid).exists()
    assert (bench / "c000.mp4").read_bytes() == b"benchmark"                  # the benchmark's chunk survived


def test_tier1s_hardcoded_chunk_path_is_redirected_to_the_apps_own_dir(tmp_path, monkeypatch):
    from pathlib import Path

    from etho.app import tuning
    from etho.app.config import AppConfig
    got = []
    monkeypatch.setattr(tuning, "_original_prepare", lambda src, out, **kw: got.append((out, kw)) or ["chunks"])
    cfg = AppConfig(root=tmp_path)
    monkeypatch.setattr(tuning, "_CHUNK_ROOT", cfg.chunk_cache_dir)
    src = tmp_path / "jobs" / "j1" / "clean.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    tuning._prepare_chunks_for_app(src, Path("cache/chunks"), stride=None)    # unregistered: left alone
    assert got[-1][0] == Path("cache/chunks")
    tuning.register_chunk_s(src, 20)
    tuning._prepare_chunks_for_app(src, Path("cache/chunks"), stride=None)
    assert got[-1][0] == cfg.chunk_cache_dir and got[-1][1]["chunk_s"] == 20


def test_shared_sweeps_are_skipped_while_a_job_is_queued_or_running(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, gw_kw={"delay": 0.4})
    unlock(client)
    orphan_cache = c.cfg.gateway_cache_root / "zz" / "orphan.json.gz"
    orphan_cache.parent.mkdir(parents=True)
    orphan_cache.write_bytes(b"x")
    orphan_chunks = c.cfg.chunk_cache_dir / "dead"
    orphan_chunks.mkdir(parents=True)
    (orphan_chunks / "c000.mp4").write_bytes(b"x")
    c.cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    (c.cfg.tmp_dir / "frames").write_bytes(b"x")
    jid = post_job(client, c.video).json()["id"]
    wait_for(lambda: c.manager.jobs[jid].status == "running", what="job to start")
    rep = c.app.state.retention.run_once(time.time() + 25 * 3600)
    assert rep["sweeps_skipped"] is True and rep["removed_projects"] == 0
    assert orphan_cache.exists() and orphan_chunks.exists() and (c.cfg.tmp_dir / "frames").exists()
    wait_done(c, jid)
    wait_for(lambda: not c.manager.busy(), what="idle")
    rep = c.app.state.retention.run_once(time.time() + 25 * 3600)
    assert rep["sweeps_skipped"] is False and not orphan_cache.exists() and not orphan_chunks.exists()


def test_a_revision_cannot_be_queued_while_its_project_is_being_purged(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    from etho.app import jobs as J
    seen = {}
    real = J.purge_project

    def racing(cfg, ledger, d, ids, gw=None, **kw):
        try:                                        # a revise arriving at exactly this moment
            c.manager.submit(Job(f"{jid}-r9", jid, "revise", d, rev=9, est_cost_usd=0.1))
        except QueueFull as e:
            seen["refused"] = str(e)
        return real(cfg, ledger, d, ids, gw, **kw)
    monkeypatch.setattr(J, "purge_project", racing)
    c.manager.cleanup(time.time() + 25 * 3600)
    assert "being deleted" in seen["refused"] and f"{jid}-r9" not in c.manager.jobs
    assert c.app.state.guard.outstanding() == 0                               # nothing was reserved for it


def test_a_project_with_a_queued_revision_is_skipped_at_the_moment_of_purging(tmp_path, monkeypatch):
    client, c = hosted(tmp_path, monkeypatch, gw_kw={"delay": 0.3})
    unlock(client)
    jid = wait_done(c, post_job(client, c.video).json()["id"])
    r = client.post(f"/api/projects/{jid}/revise", json=REVISE)
    assert r.status_code == 200
    assert c.manager.cleanup(time.time() + 25 * 3600) == []                   # the revision is queued or running
    assert wait_terminal(c.manager, r.json()["job_id"]).status in ("done", "failed")
    assert c.manager.cleanup(time.time() + 25 * 3600) == [jid]


@pytest.mark.parametrize("n", [0])
def test_retention_report_names_whether_sweeps_ran(tmp_path, monkeypatch, n):
    client, c = hosted(tmp_path, monkeypatch)
    assert c.app.state.retention.run_once()["sweeps_skipped"] is False
