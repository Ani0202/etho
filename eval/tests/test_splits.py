"""Integrity checks for eval/splits/calms21_v1.json. No network; regeneration check skips without data/."""

import json
from pathlib import Path

import pytest

SPLIT = Path("eval/splits/calms21_v1.json")
IDX = Path("data/calms21/seq/index_calms21_task1_train.json")


@pytest.fixture(scope="module")
def split():
    return json.loads(SPLIT.read_text())


def test_disjoint_and_complete(split):
    dev, ho, test = set(split["dev"]), set(split["dev_holdout"]), set(split["test"])
    unused, short = set(split["unused_train"]), set(split["excluded_too_short"])
    assert not (dev & ho) and not (dev & test) and not (ho & test)
    train_all = dev | ho | unused | short
    assert len(train_all) == len(dev) + len(ho) + len(unused) + len(short) == 70
    assert len(test) == 19 and not (train_all & test)
    assert set(split["test_subset_option_b"]) <= test


def test_seen_spike_clips_not_in_holdout(split):
    for name in split["seen_forced_into_dev"]:
        assert name in split["dev"] and name not in split["dev_holdout"]


def test_all_behaviors_present(split):
    for part in ("dev", "dev_holdout", "test"):
        s = split["summary"][part]
        for b in ("attack", "investigation", "mount"):
            assert s[f"{b}_frames"] > 0, (part, b)


@pytest.mark.skipif(not IDX.exists(), reason="CalMS21 index not downloaded")
def test_regeneration_is_deterministic(tmp_path, monkeypatch, split):
    from eval import make_splits

    monkeypatch.setattr(make_splits, "OUT", tmp_path / "s.json")
    make_splits.main()
    regen = json.loads((tmp_path / "s.json").read_text())
    for k in ("dev", "dev_holdout", "test", "test_subset_option_b", "seed"):
        assert regen[k] == split[k]
