"""scripts/make_showcase.py packaging, and the app serving a showcase with zero spend."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from tests.app_env import make_app, wait_terminal
from tests.app_helpers import CODE, write_tracking

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("make_showcase", ROOT / "scripts" / "make_showcase.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_showcase(c, monkeypatch, name="calms21-x", extra=()):
    """A fake 'existing pipeline output' dir, packaged by the real script."""
    run = c.cfg.root / "runs" / "R1"
    seq = "seqA"
    write_tracking(run / seq, c.video, c.n)
    for d, lab in (("pred_rules", np.full(c.n, 3, np.int8)), ("pred_final", np.full(c.n, 3, np.int8))):
        lab[20:60] = 1
        if d == "pred_final":
            lab[20:30] = 3                       # tier 1 voided the first window
        (run / d).mkdir(parents=True, exist_ok=True)
        np.savez_compressed(run / d / f"{seq}.npz", labels=lab)
    dets = [{"behavior": b, "code": CODE.get(b, "def detect(t, fps):\n    return np.zeros(len(t['gap']), bool)\n"),
             "model": "muse-spark-1.3-contributor", "prompt_hash": "h",
             "calls": [{"attempt": 0, "cost_usd": 0.0, "cache_hit": True, "latency_s": 0.0,
                        "usage": {"input_tokens": 2000, "billed_output_tokens": 10000, "output_tokens": 10000}}]}
            for b in ("attack", "investigation", "mount")]
    (run / "detectors.json").write_text(json.dumps({"model": "muse-spark-1.3-contributor", "effort": "high",
                                                    "detectors": dets}))
    (run / "verdicts.jsonl").write_text(json.dumps({
        "seq": seq, "s0": 20, "s1": 30, "rule_label": "investigation", "source": "rule", "label": "none",
        "confidence": 0.9, "reason": "r", "cost_usd": 0.0, "in_tok": 1500, "out_tok": 200}) + "\n")
    (run / "strips").mkdir(exist_ok=True)
    (run / "strips" / f"{seq}_20_30.jpg").write_bytes(b"\xff\xd8strip")
    monkeypatch.setattr(sys, "argv", [
        "make_showcase", "--name", name, "--run-dir", str(run), "--seq", seq, "--final-pred", "pred_final",
        "--rules-pred", "pred_rules", "--detectors", str(run / "detectors.json"),
        "--verdicts", str(run / "verdicts.jsonl"), "--definitions", str(ROOT / "configs" / "calms21_definitions.json"),
        "--out", str(c.cfg.showcase_dir), "--title", "T", "--description", "D",
        "--strips", str(run / "strips"), *extra])
    load_script().main()
    return c.cfg.showcase_dir / name


def test_make_showcase_packages_a_run_and_the_app_serves_it_with_zero_spend(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    d = build_showcase(c, monkeypatch)
    for f in ("result.json", "labels.npz", "labels_rules.npz", "overlay.mp4", "report.html", "detectors.json",
              "verdicts.jsonl", "source.mp4", "run/video/features.npz", "run/video/tracking.json"):
        assert (d / f).exists(), f
    assert json.loads((d / "run/video/tracking.json").read_text())["src"].endswith("calms21-x/source.mp4")

    lst = client.get("/api/showcases").json()["showcases"]
    assert [s["id"] for s in lst] == ["sc-calms21-x"] and lst[0]["title"] == "T" and lst[0]["dataset"]["license"] == "CC BY 4.0"
    p = client.get("/api/projects/sc-calms21-x").json()
    assert p["status"] == "done" and p["kind"] == "showcase" and p["public_data"] is True
    v0 = p["versions"][0]
    assert v0["bouts"]["investigation"] == [[30, 60]] and v0["rule_bouts"]["investigation"] == [[20, 60]]
    assert p["timing"]["x_realtime"] is None and p["timing"]["complete"] is False   # not invented
    assert p["cost"]["estimated"] is True and p["cost"]["total_usd"] > 0
    assert client.get("/api/projects/sc-calms21-x/overlay").status_code == 200
    assert "Ethogram" in client.get("/api/projects/sc-calms21-x/report").text
    a = client.get("/api/projects/sc-calms21-x/audit").json()
    assert {x["behavior"] for x in a["detectors"]} == {"attack", "investigation", "mount"} and "Precomputed" in a["note"]
    assert a["detectors"][0]["calls"][0]["usage"]["input_tokens"] == 2000
    assert a["verdicts"][0]["label"] == "none"
    assert not c.cfg.ledger_path.exists()                      # showcase viewing spends nothing
    assert client.get("/api/projects/sc-nope").status_code == 404
    assert client.get("/api/projects/sc-..%2Fx").status_code == 404


BODY = {"corrections": [{"kind": "wrong", "behavior": "investigation", "start_s": 4.0, "end_s": 6.0,
                         "correct_label": "none"}]}


def test_showcase_strips_are_served(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    r = client.get("/api/projects/sc-calms21-x/strip/video_20_30.jpg")
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    assert client.get("/api/projects/sc-calms21-x/strip/video_1_2.jpg").status_code == 404


def test_default_view_is_the_original_version(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    build_showcase(c, monkeypatch)
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    pid = r.json()["project_id"]
    assert wait_terminal(c.manager, r.json()["job_id"]).status == "done"
    v0, v1 = (client.get(f"/api/projects/{pid}/overlay{q}").content for q in ("?v=0", "?v=1"))
    assert client.get(f"/api/projects/{pid}/overlay").content == v0 and v0 != v1
    assert client.get(f"/api/projects/{pid}/report").text == client.get(f"/api/projects/{pid}/report?v=0").text
    assert client.get(f"/api/projects/{pid}/audit").json()["version"] == 0


def test_revising_a_showcase_forks_a_private_copy_and_leaves_the_package_untouched(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    d = build_showcase(c, monkeypatch)
    before = sorted(str(p.relative_to(d)) for p in d.rglob("*"))
    r1 = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    r2 = client.post("/api/projects/sc-calms21-x/revise", json=BODY)       # another visitor: its own copy
    assert r1.status_code == r2.status_code == 200, (r1.text, r2.text)
    a, b = r1.json(), r2.json()
    assert a["forked"] and b["forked"] and a["project_id"] != b["project_id"]
    assert a["project_id"].startswith("j") and a["job_id"] == f"{a['project_id']}-r1"
    for x in (a, b):
        assert wait_terminal(c.manager, x["job_id"]).status == "done"
    assert sorted(str(p.relative_to(d)) for p in d.rglob("*")) == before   # showcase untouched
    sc = client.get("/api/projects/sc-calms21-x").json()
    assert sc["latest_version"] == 0 and sc["kind"] == "showcase"
    p = client.get(f"/api/projects/{a['project_id']}").json()
    assert p["latest_version"] == 1 and p["kind"] == "showcase_copy" and p["forked_from"] == "sc-calms21-x"
    assert (c.cfg.jobs_dir / a["project_id"] / "overlay.mp4").is_symlink()
    assert not (c.cfg.jobs_dir / a["project_id"] / "strips").is_symlink()   # revising writes strips: a real copy
    assert client.get(f"/api/projects/{a['project_id']}/overlay?v=1").status_code == 200
    assert client.get(f"/api/projects/{a['project_id']}/strip/video_20_30.jpg").status_code == 200
    assert client.get(f"/api/projects/{b['project_id']}").json()["latest_version"] == 1
    assert not (c.cfg.jobs_dir / a["project_id"] / "revisions" / "2").exists()   # independent copies


def test_showcase_revision_models_follow_the_showcase_public_data_flag(tmp_path, monkeypatch):
    client, c = make_app(tmp_path / "pub", monkeypatch)
    build_showcase(c, monkeypatch)
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert wait_terminal(c.manager, r.json()["job_id"]).status == "done"
    chats = [x for x in c.gw.calls if "model" in x]
    assert chats and all(x["model"].endswith("-contributor") and x["public_data"] is True for x in chats)
    assert all(x["tag"] == "dev" and x["caller"].startswith(f"job:{r.json()['job_id']}|") for x in c.gw.calls)
    # a showcase not marked public must use Standard models, never -contributor
    client2, c2 = make_app(tmp_path / "priv", monkeypatch)
    build_showcase(c2, monkeypatch, extra=("--no-public-data",))
    assert client2.get("/api/projects/sc-calms21-x").json()["public_data"] is False
    r = client2.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert wait_terminal(c2.manager, r.json()["job_id"]).status == "done"
    chats = [x for x in c2.gw.calls if "model" in x]
    assert chats and all(not x["model"].endswith("-contributor") and x["public_data"] is False for x in chats)


def test_showcase_revise_is_subject_to_the_rate_limit(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 1})
    build_showcase(c, monkeypatch)
    assert client.post("/api/projects/sc-calms21-x/revise", json=BODY).status_code == 200
    r = client.post("/api/projects/sc-calms21-x/revise", json=BODY)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert len(list(c.cfg.jobs_dir.iterdir())) == 1                         # no second copy was made
