"""Speed changes in the app: detector cache and seeding, parallel synthesis, chunk length, overlapped stages,
tier-1 concurrency and honest timing. Mocked everywhere: no network, no model calls."""

import json
import threading
import time
from pathlib import Path

import pytest

from etho.adjudication import tier1 as T1
from etho.app import backend as BK
from etho.app import tuning
from etho.app.backend import Backend
from etho.app.config import AppConfig
from etho.app.detcache import DetectorCache, cache_key
from etho.app.jobs import Stage, stage_groups
from etho.rules.synthesize import Detector
from etho.tracking.features import FEATURE_DOC
from tests.app_env import DEFS, make_app, post_job, wait_terminal
from tests.app_helpers import CODE

REPO = Path(__file__).resolve().parents[1]
SEED_DEFS = json.loads((REPO / "configs" / "calms21_definitions.json").read_text())
SEED_DETS = {d["behavior"]: d for d in json.loads((REPO / "configs" / "detectors" / "R1_zero_shot.json").read_text())["detectors"]}


@pytest.fixture(autouse=True)
def restore_module_state(monkeypatch):
    """The app tunes tier 1 at start-up; give the other tests their module attributes back."""
    monkeypatch.setattr(T1, "FW_CONCURRENCY", T1.FW_CONCURRENCY)
    monkeypatch.setattr(T1, "_FW_SLOTS", T1._FW_SLOTS)
    monkeypatch.setattr(T1, "prepare_chunks", T1.prepare_chunks)


def run_job(client, c, **kw):
    r = post_job(client, c.video, **kw)
    assert r.status_code == 200, r.text
    j = wait_terminal(c.manager, r.json()["id"])
    assert j.status == "done", (j.status, j.error)
    return r.json()["id"]


def synth_calls(c, name=None):
    return [x for x in c.gw.calls if "synthesize" in str(x.get("caller")) and (name is None or str(x["caller"]).endswith(name))]


def det(name, code=None, model="m"):
    return Detector(name, code or CODE["investigation"], model, [{"cost_usd": 0.01}], "ph")


# ---- cache key, store, seeding -------------------------------------------------------------------------

def test_cache_key_depends_on_every_input_and_the_feature_doc(monkeypatch):
    base = cache_key("a", "def", "s", "p")
    assert base == cache_key("a", "def", "s", "p") and len(base) == 64
    assert len({base, cache_key("b", "def", "s", "p"), cache_key("a", "def2", "s", "p"),
                cache_key("a", "def", "s2", "p"), cache_key("a", "def", "s", "p2")}) == 5
    # fields cannot run into each other
    assert cache_key("ab", "c", "s", "p") != cache_key("a", "bc", "s", "p")
    monkeypatch.setitem(FEATURE_DOC, "new_feature", "a feature added later")
    assert cache_key("a", "def", "s", "p") != base


def test_cache_miss_then_hit_and_feature_doc_invalidates(tmp_path, monkeypatch):
    cache = DetectorCache(tmp_path, tmp_path / "dc")
    assert cache.get("sniff", "close and sniffing", "the resident", "the intruder") is None       # miss
    prov = cache.put(det("sniff"), "close and sniffing", "the resident", "the intruder", job="j1")
    assert prov.source == "fresh" and prov.key == cache_key("sniff", "close and sniffing", "the resident",
                                                            "the intruder")[:16]
    d, p = cache.get("sniff", "close and sniffing", "the resident", "the intruder")             # hit
    assert d.code == CODE["investigation"] and d.behavior == "sniff" and d.calls == []
    assert p.source == "cache" and "j1" in p.origin and "no model call" in p.origin
    # any change to the inputs is a miss
    assert cache.get("sniff", "close and sniffing!", "the resident", "the intruder") is None
    assert cache.get("sniff", "close and sniffing", "the resident", "another mouse") is None
    assert cache.get("other", "close and sniffing", "the resident", "the intruder") is None
    # a new DetectorCache on the same directory sees it (it is on disk), and a changed feature set does not
    assert DetectorCache(tmp_path, tmp_path / "dc").get("sniff", "close and sniffing", "the resident",
                                                        "the intruder")[1].source == "cache"
    monkeypatch.setitem(FEATURE_DOC, "new_feature", "a feature added later")
    assert cache.get("sniff", "close and sniffing", "the resident", "the intruder") is None


def test_seed_uses_the_benchmark_detectors_for_the_calms21_definitions(tmp_path):
    cache = DetectorCache(REPO, tmp_path / "dc")          # the real config files
    for name, text in SEED_DEFS["behaviors"].items():
        d, p = cache.get(name, text, SEED_DEFS["subject"], SEED_DEFS["partner"])
        assert p.source == "seed" and d.code == SEED_DETS[name]["code"] and d.behavior == name
        assert "R1_zero_shot.json" in p.origin and "CalMS21" in p.origin
    # the seed is matched on the definition text, so another name gets the same benchmark detector
    d, p = cache.get("sniffing", SEED_DEFS["behaviors"]["investigation"], SEED_DEFS["subject"], SEED_DEFS["partner"])
    assert p.source == "seed" and d.code == SEED_DETS["investigation"]["code"] and d.behavior == "sniffing"
    # any difference (one character, or another subject/partner) is not the benchmark definition
    text = SEED_DEFS["behaviors"]["attack"]
    assert cache.get("attack", text + " ", SEED_DEFS["subject"], SEED_DEFS["partner"]) is None
    assert cache.get("attack", text, "the black mouse", SEED_DEFS["partner"]) is None
    assert cache.get("attack", text, SEED_DEFS["subject"], "the other mouse") is None
    # the app's own default definitions are exactly these
    from etho.app.server import default_definitions
    dd = default_definitions(AppConfig(root=REPO))
    assert all(cache.get(n, t, dd["subject"], dd["partner"])[1].source == "seed" for n, t in dd["behaviors"].items())


# ---- pipeline: reuse, provenance, parallel synthesis ---------------------------------------------------

def test_second_upload_reuses_detectors_without_a_synthesis_call(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 9})
    j1 = run_job(client, c)
    assert len(synth_calls(c)) == 2                      # one call per behavior, nothing cached yet
    p1 = client.get(f"/api/projects/{j1}").json()
    assert {d["behavior"]: d["source"] for d in p1["detector_sources"]} == {"investigation": "fresh",
                                                                           "attack": "fresh"}
    assert all("written for this upload" in d["origin"] for d in p1["detector_sources"])
    n_before = len(c.gw.calls)

    j2 = run_job(client, c)
    assert len(synth_calls(c)) == 2                      # no new synthesis call
    assert not [x for x in c.gw.calls[n_before:] if "synthesize" in str(x.get("caller"))]
    p2 = client.get(f"/api/projects/{j2}").json()
    assert {d["behavior"]: d["source"] for d in p2["detector_sources"]} == {"investigation": "cache", "attack": "cache"}
    assert p2["versions"][0]["bouts"] == p1["versions"][0]["bouts"]       # same detectors, same bouts
    assert p2["cost"]["by_stage"].get("synthesize", 0.0) == 0.0
    # audit trail: per-detector provenance, and the detector code is the cached one
    a = client.get(f"/api/projects/{j2}/audit").json()
    assert {d["behavior"]: d["source"]["source"] for d in a["detectors"]} == {"investigation": "cache",
                                                                             "attack": "cache"}
    assert "def detect" in a["detectors"][0]["code"] and a["detectors"][0]["calls"] == []
    # the report's audit trail (embedded JSON) says it too
    html = client.get(f"/api/projects/{j2}/report").text
    assert "&quot;source&quot;: &quot;cache&quot;" in html
    # a different definition text is a miss: only that behavior is written again
    j3 = run_job(client, c, defs={"investigation": DEFS["investigation"], "attack": DEFS["attack"] + " Also chasing."})
    assert [str(x["caller"]).rsplit(":", 1)[-1] for x in synth_calls(c)][2:] == ["attack"]
    p3 = client.get(f"/api/projects/{j3}").json()
    assert {d["behavior"]: d["source"] for d in p3["detector_sources"]} == {"investigation": "cache", "attack": "fresh"}


def test_seeded_definitions_skip_synthesis_and_cache_failures_fall_back(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    # a test seed: the CalMS21 definition file plus a detector file whose code runs on the synthetic features
    seed_defs = json.loads((c.cfg.root / "configs" / "calms21_definitions.json").read_text())
    (c.cfg.root / "configs" / "detectors").mkdir()
    (c.cfg.root / "configs" / "detectors" / "R1_zero_shot.json").write_text(json.dumps({"detectors": [
        {"behavior": "attack", "code": CODE["attack"], "model": "muse-spark-1.3-contributor", "calls": [],
         "prompt_hash": "seedhash"},
        {"behavior": "investigation", "code": "def detect(t, fps):\n    return t['no_such_feature'] > 0\n",
         "model": "muse-spark-1.3-contributor", "calls": [], "prompt_hash": "badseed"}]}))
    defs = {"attack": seed_defs["behaviors"]["attack"], "investigation": seed_defs["behaviors"]["investigation"]}
    jid = run_job(client, c, defs=defs, subject=seed_defs["subject"], partner=seed_defs["partner"])
    # attack: the seed ran (no call); investigation: the seed does not run on this video, so it was written
    assert [str(x["caller"]).rsplit(":", 1)[-1] for x in synth_calls(c)] == ["investigation"]
    p = client.get(f"/api/projects/{jid}").json()
    src = {d["behavior"]: d for d in p["detector_sources"]}
    assert src["attack"]["source"] == "seed" and "R1_zero_shot.json" in src["attack"]["origin"]
    assert src["investigation"]["source"] == "fresh" and "failed on this video" in src["investigation"]["note"]
    a = client.get(f"/api/projects/{jid}/audit").json()
    code = {d["behavior"]: d["code"] for d in a["detectors"]}
    assert code["attack"] == CODE["attack"] and "no_such_feature" not in code["investigation"]
    # the freshly written replacement is cached next to the seed that did not run: the next upload skips the call
    cands = DetectorCache(c.cfg.root, c.cfg.detector_cache_dir).candidates(
        "investigation", defs["investigation"], seed_defs["subject"], seed_defs["partner"])
    assert [pv.source for _, pv in cands] == ["seed", "cache"]
    n = len(synth_calls(c))
    jid2 = run_job(client, c, defs=defs, subject=seed_defs["subject"], partner=seed_defs["partner"])
    assert len(synth_calls(c)) == n
    src2 = {d["behavior"]: d["source"] for d in client.get(f"/api/projects/{jid2}").json()["detector_sources"]}
    assert src2 == {"attack": "seed", "investigation": "cache"}


def test_uncached_behaviors_are_synthesized_in_parallel(tmp_path, monkeypatch):
    names = ["a", "b", "c"]
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"rate_limit_jobs": 9})
    barrier = threading.Barrier(len(names), timeout=10)     # passes only if all three calls are in flight at once
    inner = BK.synthesize

    def gated(name, definition, **kw):
        barrier.wait()
        return inner(name, definition, **kw)
    monkeypatch.setattr(BK, "synthesize", gated)
    jid = run_job(client, c, defs={n: f"definition of behavior {n}" for n in names})
    p = client.get(f"/api/projects/{jid}").json()
    assert [d["source"] for d in p["detector_sources"]] == ["fresh"] * 3 and not p["warnings"]
    # mixed: one cached, two written in parallel (the barrier now needs only two)
    barrier2 = threading.Barrier(2, timeout=10)
    monkeypatch.setattr(BK, "synthesize", lambda name, d, **kw: (barrier2.wait(), inner(name, d, **kw))[1])
    jid2 = run_job(client, c, defs={"a": "definition of behavior a", "x": "definition of behavior x", "y": "definition of behavior y"})
    src = {d["behavior"]: d["source"] for d in client.get(f"/api/projects/{jid2}").json()["detector_sources"]}
    assert src == {"a": "cache", "x": "fresh", "y": "fresh"}


def test_revision_keeps_provenance_and_marks_revised_detectors(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    jid = run_job(client, c)
    r = client.post(f"/api/projects/{jid}/revise", json={"corrections": [
        {"kind": "wrong", "behavior": "investigation", "start_s": 5.0, "end_s": 6.0, "correct_label": "none"}]})
    assert wait_terminal(c.manager, r.json()["job_id"]).status == "done"
    rev = client.get(f"/api/projects/{jid}").json()["versions"][1]
    src = {d["behavior"]: d for d in rev["detector_sources"]}
    assert src["investigation"]["source"] == "revised" and "was: fresh" in src["investigation"]["origin"]
    assert src["attack"]["source"] == "fresh"                    # untouched behavior keeps its provenance


# ---- chunk length and overlapped preparation -------------------------------------------------------------

@pytest.mark.parametrize("duration,expected", [(1, 10), (6, 10), (60, 10), (79, 10), (100, 12), (300, 38), (480, 60),
                                               (600, 60), (3600, 60)])
def test_chunk_s_is_duration_over_8_clamped_to_10_and_60(duration, expected):
    assert tuning.choose_chunk_s(duration) == expected
    assert isinstance(tuning.choose_chunk_s(duration), int)


def test_chunks_are_cut_while_the_planner_runs_and_track_uses_identical_arguments(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    seen = {"prep": [], "track": []}
    prep_started = threading.Event()
    planner_saw_prep = []
    orig_chunks, orig_plan, orig_track = BK.prepare_chunks, BK.run_planner, BK.track_video

    def chunks(src, out_dir, **kw):
        seen["prep"].append((str(src), str(out_dir), kw))
        prep_started.set()
        time.sleep(0.2)
        return orig_chunks(src, out_dir, **kw)

    def plan(video, **kw):
        planner_saw_prep.append(prep_started.wait(timeout=5))    # False if the cut had not started concurrently
        return orig_plan(video, **kw)

    def track(src, out_dir, **kw):
        seen["track"].append((str(src), kw))
        return orig_track(src, out_dir, **kw)
    monkeypatch.setattr(BK, "prepare_chunks", chunks)
    monkeypatch.setattr(BK, "run_planner", plan)
    monkeypatch.setattr(BK, "track_video", track)
    jid = run_job(client, c)
    assert planner_saw_prep == [True]
    prep = seen["prep"][0]
    (tsrc, tkw), = seen["track"]
    assert len(seen["prep"]) == 2                                # the cut, then the overlay's lookup
    # identical arguments, so track_video's own prepare_chunks call is a cache hit in the real pipeline
    assert prep[0] == tsrc and prep[2] == {"stride": tkw["stride"], "fps": tkw["fps"], "chunk_s": tkw["chunk_s"]}
    assert prep[1] == str(tkw["chunk_dir"]) and tkw["stride"] is None and tkw["fps"] == 10 and tkw["chunk_s"] == 10
    # the overlay asks for the same chunk length (it reads the cache the cut filled)
    assert seen["prep"][1][2]["chunk_s"] == 10 and seen["prep"][1][2]["stride"] is None
    info = json.loads((c.cfg.jobs_dir / jid / "run" / "video" / "tracking.json").read_text())
    assert info["app_chunk_s"] == 10
    p = client.get(f"/api/projects/{jid}").json()
    plan_row = next(s for s in p["stages"] if s["name"] == "plan")
    sub, = plan_row["substeps"]
    # it starts with the plan stage and runs alongside it (here it outlasts the stubbed planner, so track waited)
    assert sub["name"] == "cut chunks" and plan_row["start_s"] <= sub["start_s"] <= plan_row["end_s"]
    assert sub["end_s"] - sub["start_s"] >= 0.15


def test_tier1_finds_the_app_chunks_through_the_registered_chunk_length(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(tuning, "_original_prepare", lambda src, out, **kw: got.append(kw) or ["chunks"])
    monkeypatch.setattr(T1, "prepare_chunks", tuning._prepare_chunks_for_app)
    src = tmp_path / "clean.mp4"
    src.write_bytes(b"x")
    T1.prepare_chunks(src, Path("cache/chunks"), stride=None)            # tier 1's call: default chunk_s=60
    assert got[-1] == {"stride": None, "fps": 10, "chunk_s": 60}         # not registered: unchanged
    tuning.register_chunk_s(src, 12)
    T1.prepare_chunks(src, Path("cache/chunks"), stride=None)
    assert got[-1] == {"stride": None, "fps": 10, "chunk_s": 12}
    assert T1.prepare_chunks(tmp_path / "other.mp4", Path("c")) == ["chunks"] and got[-1]["chunk_s"] == 60


# ---- tier-1 concurrency ----------------------------------------------------------------------------------

def test_tier1_concurrency_is_raised_for_app_jobs_and_installing_twice_is_safe(tmp_path, monkeypatch):
    Backend(AppConfig(root=tmp_path, tier1_concurrency=9))
    assert T1.FW_CONCURRENCY == 9
    sem = T1._FW_SLOTS
    assert [sem.acquire(blocking=False) for _ in range(10)] == [True] * 9 + [False]
    for _ in range(9):
        sem.release()
    with pytest.raises(ValueError):
        sem.release()                                # a BoundedSemaphore: releasing more than acquired is a bug
    Backend(AppConfig(root=tmp_path))
    assert T1.FW_CONCURRENCY == AppConfig(root=tmp_path).tier1_concurrency == 8
    assert getattr(T1.prepare_chunks, "_etho_app_shim", False) is True
    assert T1.prepare_chunks is tuning._prepare_chunks_for_app


def test_adjudicate_is_called_with_the_app_concurrency(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, cfg_kw={"tier1_concurrency": 7})
    kws = []
    inner = BK.adjudicate
    monkeypatch.setattr(BK, "adjudicate", lambda *a, **kw: (kws.append(kw), inner(*a, **kw))[1])
    run_job(client, c)
    assert kws and kws[0]["concurrency"] == 7 and T1.FW_CONCURRENCY == 7


# ---- overlapped stages and honest timing -----------------------------------------------------------------

def test_stage_groups_put_report_and_overlay_together():
    st = [Stage(n) for n in ("validate", "plan", "track", "synthesize", "predict", "tier1", "report", "overlay")]
    assert [[s.name for s in g] for g in stage_groups(st)] == [["validate"], ["plan"], ["track"], ["synthesize"],
                                                              ["predict"], ["tier1"], ["report", "overlay"]]
    rv = [Stage(n) for n in ("revise", "predict", "tier1", "report", "overlay")]
    assert [len(g) for g in stage_groups(rv)] == [1, 1, 1, 2]
    assert [[s.name for s in g] for g in stage_groups([Stage("overlay"), Stage("report")])] == [["overlay"], ["report"]]


def test_report_and_overlay_run_concurrently_and_timing_is_job_end_minus_start(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)
    gate = threading.Barrier(2, timeout=10)                    # both must be running at once to get through
    orig_report, orig_overlay = Backend.stage_report, Backend.stage_overlay

    def report(self, ctx):
        gate.wait()
        time.sleep(0.4)
        orig_report(self, ctx)

    def overlay(self, ctx):
        gate.wait()
        time.sleep(0.4)
        orig_overlay(self, ctx)
    monkeypatch.setattr(Backend, "stage_report", report)
    monkeypatch.setattr(Backend, "stage_overlay", overlay)
    jid = run_job(client, c)
    p = client.get(f"/api/projects/{jid}").json()
    rows = {s["name"]: s for s in p["stages"]}
    rep, ov = rows["report"], rows["overlay"]
    assert rep["elapsed_s"] >= 0.4 and ov["elapsed_s"] >= 0.4
    # windows intersect for most of their length, and each stage lists the other as overlapping
    shared = min(rep["end_s"], ov["end_s"]) - max(rep["start_s"], ov["start_s"])
    assert shared >= 0.35 and rep["overlaps"] == ["overlay"] and ov["overlaps"] == ["report"]
    # everything before them ran one after another: no overlap among the sequential stages
    seq = ["validate", "plan", "track", "synthesize", "predict", "tier1"]
    assert all(rows[n]["overlaps"] == [] for n in seq)
    ends = [rows[n]["end_s"] for n in seq]
    starts = [rows[n]["start_s"] for n in seq[1:]] + [min(rep["start_s"], ov["start_s"])]
    assert all(s >= e - 0.05 for s, e in zip(starts, ends, strict=True))
    # total = job end minus job start (not the sum of the stages), and the x-real-time tile uses it
    j = c.manager.jobs[jid]
    t = p["timing"]
    assert abs(t["wall_s"] - j.run_s) < 0.5 and abs(t["wall_s"] - (j.finished_mono - j.started_mono)) < 0.5
    assert t["stage_sum_s"] > t["wall_s"] + 0.3                # the overlap saved about 0.4 s, which a sum would hide
    assert t["wall_s"] >= max(s["end_s"] for s in p["stages"]) - 0.01
    assert t["x_realtime"] == pytest.approx(t["wall_s"] / t["video_s"], abs=0.002) and t["video_s"] == 6.0
    # live snapshots carry the windows too
    snap = client.get(f"/api/jobs/{jid}").json()
    assert snap["stages"][-1]["start_s"] is not None and snap["stages"][-1]["end_s"] is not None


def test_a_failure_in_the_overlapped_stage_fails_the_job_and_the_other_stage_still_finishes(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch)

    def boom(self, ctx):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(Backend, "stage_overlay", boom)
    jid = post_job(client, c.video).json()["id"]
    j = wait_terminal(c.manager, jid)
    assert j.status == "failed" and "overlay: RuntimeError: ffmpeg exploded" in j.error
    status = {s.name: s.status for s in j.stages}
    assert status["report"] == "done" and status["overlay"] == "failed"
    assert (c.cfg.jobs_dir / jid / "report.html").exists()
    assert json.loads((c.cfg.jobs_dir / jid / "job.json").read_text())["stages"][-1]["end_s"] is not None


def test_queue_wait_is_not_part_of_the_total(tmp_path, monkeypatch):
    client, c = make_app(tmp_path, monkeypatch, gw_kw={"delay": 0.15}, cfg_kw={"rate_limit_jobs": 9})
    a = post_job(client, c.video).json()["id"]
    b = post_job(client, c.video).json()["id"]
    ja, jb = wait_terminal(c.manager, a), wait_terminal(c.manager, b)
    assert jb.started_mono >= ja.finished_mono                     # b waited for a
    tb = client.get(f"/api/projects/{b}").json()["timing"]
    assert abs(tb["wall_s"] - jb.run_s) < 0.5
    assert tb["wall_s"] < jb.finished_mono - ja.started_mono - 0.5   # strictly less than "since a started"


def test_overlap_needs_both_windows():
    """Stages without a recorded window (old results) never count as overlapping."""
    from etho.app.backend import overlap_s
    a, b = Stage("a"), Stage("b")
    assert overlap_s(a, b) == 0.0
    a.start_s, a.end_s, b.start_s, b.end_s = 1.0, 3.0, 2.0, 5.0
    assert overlap_s(a, b) == 1.0 and overlap_s(b, a) == 1.0
    b.start_s = 3.0
    assert overlap_s(a, b) == 0.0
