"""Gateway-cache entries are content-addressed, so two projects with the same inputs share them. Purging the older
project must not delete an entry (or its lock, or the Meta file it points at) that a live project still uses."""

import gzip
import json

from etho.app.config import AppConfig
from etho.app.jobgw import AppLedger
from etho.app.retention import purge_project
from tests.test_app_hosting import write_rows

SHARED, ONLY_OLD, FILE_SHARED = "ab" * 32, "cd" * 32, "ef" * 32


class FakeCache:
    def __init__(self, root):
        self.root = root

    def get_json(self, key):
        with gzip.open(self.root / key[:2] / f"{key}.json.gz", "rt") as f:
            return json.load(f)


class FakeGw:
    def __init__(self, root):
        self.cache, self.deleted = FakeCache(root), []

    def delete_file(self, file_id, *, tag, caller, request_key):
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
    assert not entry(root, ONLY_OLD).exists() and not lock(root, ONLY_OLD).exists()   # unshared: deleted


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
    assert not list(root.rglob("*.gz")) and not list((root / "locks").glob("*"))
    assert gw.deleted == [f"file-{FILE_SHARED[:6]}"]
