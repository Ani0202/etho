"""Gateway-cache entries are content-addressed, so two projects with the same inputs share them. Purging the older
project must not delete an entry (or its lock, or the Meta file it points at) that a live project still uses."""

import gzip
import json
from contextlib import contextmanager

from etho.app.config import AppConfig
from etho.app.jobgw import AppLedger
from etho.app.retention import purge_project
from etho.gateway.cache import DiskCache
from tests.markers import needs_bwrap
from tests.test_app_hosting import write_rows

SHARED, ONLY_OLD, FILE_SHARED = "ab" * 32, "cd" * 32, "ef" * 32


class RegisteringCache(DiskCache):
    """A real cache whose lock, once acquired for a key, first runs `on_lock(key)`: a job that hit the cache just
    before we got the lock has written its ledger row by then (Gateway.upload_file does so under this lock)."""

    on_lock = None

    @contextmanager
    def lock(self, key):
        with super().lock(key):
            if self.on_lock:
                self.on_lock(key)
            yield


class FakeGw:
    def __init__(self, root):
        self.cache, self.deleted = RegisteringCache(root), []

    def delete_file(self, file_id, *, tag, caller, request_key):
        assert request_key is None           # the caller holds the (non-reentrant) key lock and forgets the entry
        self.deleted.append(file_id)
        return True


def setup(tmp_path):
    cfg = AppConfig(root=tmp_path, gateway_cache_root=tmp_path / "gwc")
    root = cfg.gateway_cache_root
    for k in (SHARED, ONLY_OLD, FILE_SHARED):
        (root / k[:2]).mkdir(parents=True, exist_ok=True)
        (root / "locks").mkdir(exist_ok=True)
        (root / k[:2] / f"{k}.json.gz").write_bytes(gzip.compress(json.dumps({"id": f"file-{k[:6]}"}).encode()))
        (root / "locks" / f"{k}.lock").write_bytes(b"")
    for name in ("old", "new"):
        (cfg.jobs_dir / name).mkdir(parents=True)
    pre = cfg.caller_prefix
    write_rows(cfg.ledger_path, [
        {"caller": f"{pre}old|x", "cost_usd": 0.0, "endpoint": "/chat/completions", "request_key": SHARED},
        {"caller": f"{pre}old|x", "cost_usd": 0.0, "endpoint": "/chat/completions", "request_key": ONLY_OLD},
        {"caller": f"{pre}old|x", "cost_usd": 0.0, "endpoint": "/files", "request_key": FILE_SHARED},
        {"caller": f"{pre}new|x", "cost_usd": 0.0, "endpoint": "/chat/completions", "request_key": SHARED},
        {"caller": f"{pre}new|x", "cost_usd": 0.0, "endpoint": "/files", "request_key": FILE_SHARED},
    ])
    return cfg, AppLedger(cfg.ledger_path, cfg.caller_prefix), root


def entry(root, k):
    return root / k[:2] / f"{k}.json.gz"


def lock(root, k):
    return root / "locks" / f"{k}.lock"


def test_purging_the_older_project_keeps_entries_the_newer_one_still_references(tmp_path):
    cfg, ledger, root = setup(tmp_path)
    gw = FakeGw(root)
    purge_project(cfg, ledger, cfg.jobs_dir / "old", ["old"], gw=gw)
    assert not (cfg.jobs_dir / "old").exists() and (cfg.jobs_dir / "new").exists()
    assert entry(root, SHARED).exists() and lock(root, SHARED).exists()          # the newer job's entry survives
    assert entry(root, FILE_SHARED).exists() and lock(root, FILE_SHARED).exists()
    assert gw.deleted == []                                    # the Meta file a live job uses is kept
    assert not entry(root, ONLY_OLD).exists()                  # unshared: deleted (lock files are left to the sweep)


def test_a_job_the_manager_knows_in_another_project_also_protects_its_keys(tmp_path):
    cfg, ledger, root = setup(tmp_path)
    (cfg.jobs_dir / "new").rmdir()                             # no directory, but a live job still references it
    purge_project(cfg, ledger, cfg.jobs_dir / "old", ["old"], gw=FakeGw(root), other_job_ids=["new"])
    assert entry(root, SHARED).exists() and entry(root, FILE_SHARED).exists()


def test_unshared_entries_and_meta_files_are_still_deleted_once_no_other_project_remains(tmp_path):
    cfg, ledger, root = setup(tmp_path)
    gw = FakeGw(root)
    purge_project(cfg, ledger, cfg.jobs_dir / "old", ["old"], gw=gw)
    purge_project(cfg, ledger, cfg.jobs_dir / "new", ["new"], gw=gw)          # the newer one expires later
    assert not list(root.rglob("*.gz"))
    assert gw.deleted == [f"file-{FILE_SHARED[:6]}"]


def test_a_job_that_registers_the_key_between_the_scan_and_the_delete_keeps_its_file_and_entry(tmp_path):
    cfg, ledger, root = setup(tmp_path)
    pre = cfg.caller_prefix
    gw = FakeGw(root)
    late = "9a" * 32                                   # only "old" has a row for it when the purge starts
    entry(root, late).parent.mkdir(parents=True, exist_ok=True)
    entry(root, late).write_bytes(gzip.compress(json.dumps({"id": "file-late"}).encode()))
    write_rows(cfg.ledger_path, [{"caller": f"{pre}old|x", "cost_usd": 0.0, "endpoint": "/files", "request_key": late}])

    def new_job_hits_the_cache(key):                   # what Gateway.upload_file does under the key's lock
        if key == late and not getattr(new_job_hits_the_cache, "done", False):
            new_job_hits_the_cache.done = True
            write_rows(cfg.ledger_path, [{"caller": f"{pre}new|x", "cost_usd": 0.0, "endpoint": "/files",
                                          "request_key": late, "cache_hit": True}])
    gw.cache.on_lock = new_job_hits_the_cache
    purge_project(cfg, ledger, cfg.jobs_dir / "old", ["old"], gw=gw)
    assert entry(root, late).exists() and "file-late" not in gw.deleted


class FakeJob:
    def __init__(self, id):
        self.id = id


def early_setup(tmp_path, monkeypatch):
    from tests.test_app_hosting import hosted
    client, c = hosted(tmp_path, monkeypatch)
    root = c.cfg.gateway_cache_root
    key = "7c" * 32
    entry(root, key).parent.mkdir(parents=True, exist_ok=True)
    entry(root, key).write_bytes(gzip.compress(json.dumps({"id": "file-early"}).encode()))
    pre = c.cfg.caller_prefix
    write_rows(c.cfg.ledger_path, [{"caller": f"{pre}{j}|x", "cost_usd": 0.0, "endpoint": "/files", "request_key": key}
                                   for j in ("A", "B")])
    c.manager._needs_upload.update({"A", "B"})
    return c, root, key


def test_early_delete_keeps_a_file_another_running_job_still_needs_in_both_orders(tmp_path, monkeypatch):
    for first, second in (("A", "B"), ("B", "A")):
        c, root, key = early_setup(tmp_path / first, monkeypatch)
        c.manager.delete_uploaded_files(FakeJob(first))
        assert c.gw.deleted == [] and entry(root, key).exists()        # the other job has not finished tracking
        c.manager.delete_uploaded_files(FakeJob(second))
        assert c.gw.deleted == ["file-early"] and not entry(root, key).exists()   # the last one out deletes it
        c.manager.delete_uploaded_files(FakeJob(second))                           # idempotent
        assert c.gw.deleted == ["file-early"]


def test_early_delete_deletes_at_once_when_no_other_job_needs_the_file(tmp_path, monkeypatch):
    c, root, key = early_setup(tmp_path, monkeypatch)
    c.manager._needs_upload.discard("B")                                          # B finished tracking earlier
    c.manager.delete_uploaded_files(FakeJob("A"))
    assert c.gw.deleted == ["file-early"] and not entry(root, key).exists()


@needs_bwrap
def test_two_projects_expiring_in_the_same_pass_both_release_a_shared_key(tmp_path, monkeypatch):
    import time

    from tests.app_env import post_job
    from tests.test_app_hosting import hosted, unlock, wait_done
    client, c = hosted(tmp_path, monkeypatch)
    unlock(client)
    j1 = wait_done(c, post_job(client, c.video).json()["id"])
    j2 = wait_done(c, post_job(client, c.video).json()["id"])
    root = c.cfg.gateway_cache_root
    key = "5d" * 32                                       # both uploads were the same bytes; an early delete had failed
    entry(root, key).parent.mkdir(parents=True, exist_ok=True)
    entry(root, key).write_bytes(gzip.compress(json.dumps({"id": "file-shared"}).encode()))
    pre = c.cfg.caller_prefix
    write_rows(c.cfg.ledger_path, [{"caller": f"{pre}{j}|x", "cost_usd": 0.0, "endpoint": "/files", "request_key": key}
                                   for j in (j1, j2)])
    assert set(c.manager.jobs) >= {j1, j2}
    assert sorted(c.manager.cleanup(time.time() + 25 * 3600)) == sorted([j1, j2])   # one call, both expired
    assert not entry(root, key).exists()                  # the second purge saw no live project and released it
    assert "file-shared" in c.gw.deleted                  # and deleted the Meta file (not left unrecorded)


def test_a_file_key_kept_by_the_meta_pass_is_not_forgotten_by_the_unlink_pass(tmp_path):
    cfg, ledger, root = setup(tmp_path)
    gw, seen = FakeGw(root), []

    def on_lock(k):                      # the other project disappears between the Meta pass and the unlink pass
        if k == FILE_SHARED:
            seen.append(k)
            if len(seen) == 2:
                (cfg.jobs_dir / "new").rmdir()

    gw.cache.on_lock = on_lock
    purge_project(cfg, ledger, cfg.jobs_dir / "old", ["old"], gw=gw)
    assert gw.deleted == [] and entry(root, FILE_SHARED).exists()   # the id of the undeleted file is still on disk


def test_a_cache_unlink_failure_after_a_confirmed_delete_is_not_recorded_as_undeleted(tmp_path):
    cfg, ledger, root = setup(tmp_path)
    (cfg.jobs_dir / "new").rmdir()
    gw = FakeGw(root)

    def boom(key):
        raise OSError("disk")

    gw.cache.delete_json = boom
    rep = purge_project(cfg, ledger, cfg.jobs_dir / "old", ["old"], gw=gw)
    assert gw.deleted == [f"file-{FILE_SHARED[:6]}"]
    assert rep["meta_files_deleted"] == 1 and rep["meta_files_recorded"] == 0


class OneShotManager:
    """Just enough manager for Retention.run_once: the expired project is purged (as Manager.cleanup does), the newer
    one stays, and nothing is busy so the shared-dir sweeps run."""

    shared_keys_kept = 0

    def __init__(self, cfg, ledger, gw):
        self.cfg, self.ledger, self.gw = cfg, ledger, gw

    def cleanup(self, now):
        rep = purge_project(self.cfg, self.ledger, self.cfg.jobs_dir / "old", ["old"], gw=self.gw)
        self.shared_keys_kept = rep["shared_keys_kept"]
        return ["old"]

    def busy(self):
        return False


def test_the_mtime_sweep_after_a_purge_keeps_entries_the_newer_project_still_uses(tmp_path):
    import os
    import time

    from etho.app.retention import Retention
    cfg, ledger, root = setup(tmp_path)
    assert cfg.private_gateway_cache
    orphan = "12" * 32                       # nobody references it: an old orphan the sweep must still remove
    entry(root, orphan).parent.mkdir(parents=True, exist_ok=True)
    entry(root, orphan).write_bytes(gzip.compress(json.dumps({"id": "file-orphan"}).encode()))
    lock(root, orphan).write_bytes(b"")
    old = time.time() - 48 * 3600            # all older than the 24 h window: cache hits never refresh mtimes
    for p in root.rglob("*"):
        os.utime(p, (old, old))
    gw = FakeGw(root)
    out = Retention(cfg, OneShotManager(cfg, ledger, gw), ledger).run_once()
    assert out["removed_projects"] == 1 and not (cfg.jobs_dir / "old").exists() and (cfg.jobs_dir / "new").exists()
    for k in (SHARED, FILE_SHARED):          # the newer project's entries, their locks and the Meta file record
        assert entry(root, k).exists() and lock(root, k).exists(), k
    assert gw.deleted == []
    assert not entry(root, ONLY_OLD).exists() and not lock(root, ONLY_OLD).exists()      # only the purged project used it
    assert not entry(root, orphan).exists() and not lock(root, orphan).exists()          # unreferenced and old: swept
    assert out["gateway_cache_files"] >= 3
