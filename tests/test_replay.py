"""The offline replay: semantic keys, misses, the lockdown, and an end-to-end run of the real app pipeline on the
committed sample clip from the committed recording (no key, no network)."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
import socket
from pathlib import Path

import httpx
import numpy as np
import pytest

from etho.replay import LABEL, keys
from etho.replay.gateway import ReplayGateway
from etho.replay.index import ReplayIndex, ReplayMiss
from etho.replay.lockdown import RealGatewayBlocked, lockdown
from tests.app_helpers import make_video

SAMPLE = Path(__file__).resolve().parents[1] / "demo" / "sample"
INDEX = SAMPLE / "replay.jsonl.gz"
CLIP = SAMPLE / "calms21_dev_60s.mp4"


# ---- helpers -----------------------------------------------------------------------------------
def img(payload: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{payload}"}}


def msgs(text: str, *payloads: str) -> list[dict]:
    return [{"role": "user", "content": [{"type": "text", "text": text}, *[img(p) for p in payloads]]}]


def tiny_index(*entries: tuple[dict, dict]) -> ReplayIndex:
    out, seen = [], {}
    for desc, resp in entries:
        k = keys.key_of(desc)
        out.append({"key": k, "occ": seen.get(k, 0), "desc": desc, "response": resp})
        seen[k] = seen.get(k, 0) + 1
    return ReplayIndex({"kind": "header", "version": 1}, out)


CHAT_RESP = {"text": "hello", "usage": {"input_tokens": 3, "output_tokens": 2}, "cost_usd": 0.001,
             "reasoning_tokens": 0}


def chat_desc(text="judge t=1.0s to t=2.0s", payloads=("AAAA",), caller="job:j1|etho.adjudication.tier1", **kw):
    return keys.chat_desc("fireworks", "accounts/fireworks/models/muse-spark-open", msgs(text, *payloads), "low",
                          caller, {"max_tokens": 2048, **kw})


# ---- keys --------------------------------------------------------------------------------------
def test_key_ignores_image_bytes_job_prefix_and_tag():
    a = keys.key_of(chat_desc(payloads=("AAAA",)))
    assert keys.key_of(chat_desc(payloads=("QkJC" * 50,))) == a                      # other machine, other jpeg bytes
    assert keys.key_of(chat_desc(caller="pub:job:j9|etho.adjudication.tier1")) == a    # another job id and prefix
    assert keys.key_of(chat_desc(caller="etho.adjudication.tier1:judge")) == a         # function name is not part of it
    assert keys.key_of(chat_desc(tag="dev")) == keys.key_of(chat_desc(tag="eval-llm"))  # the app forces its tag


def test_key_changes_with_text_image_count_caller_model_and_parameters():
    base = keys.key_of(chat_desc())
    assert keys.key_of(chat_desc(text="judge t=1.1s to t=2.0s")) != base           # a drifted window
    assert keys.key_of(chat_desc(payloads=("AAAA", "BBBB"))) != base               # image COUNT
    assert keys.key_of(chat_desc(caller="job:j1|etho.rules.synthesize:attack")) != base
    assert keys.key_of(chat_desc(max_tokens=4096)) != base
    other = keys.chat_desc("fireworks", "other-model", msgs("judge t=1.0s to t=2.0s", "AAAA"), "low",
                           "etho.adjudication.tier1", {"max_tokens": 2048})
    assert keys.key_of(other) != base


def test_sam_key_keeps_phrase_and_frames_but_not_media_or_file_id():
    def d(phrase="mouse", **kw):
        return keys.key_of(keys.sam_desc(phrase, "job:j|etho.tracking.pipeline", kw))
    base = d(video=Path("/a/chunk.mp4"), n_frames=600, video_file_id="file-1", public_data=False, on_line=print)
    assert d(video=Path("/elsewhere/c000.mp4"), n_frames=600, video_file_id="file-2", public_data=False) == base
    assert d("mice", video=Path("x.mp4"), n_frames=600, public_data=False) != base
    assert d(video=Path("x.mp4"), n_frames=300, public_data=False) != base
    assert d(image=Path("f.jpg"), public_data=False) != base                      # an image call is another call


def test_methods_prompt_timings_are_masked_but_costs_are_not():
    def d(timings, cost):
        text = f'FACTS:\n{{"costs_usd": {{"plan": {cost}}}, "timings_s": {{"plan": {timings}, "track": 3.0}}}}'
        return keys.key_of(keys.chat_desc("meta", "muse-spark-1.3", [{"role": "user", "content": text}], "low",
                                          "etho.report.methods", {}))
    assert d(29.2, 0.0336) == d(2.7, 0.0336)
    assert d(29.2, 0.0336) != d(29.2, 0.0337)


def test_upload_key_is_purpose_duration_frames_not_bytes(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_video(a, 2.0, 64, 48)
    make_video(b, 2.0, 96, 64)                                  # different bytes, same duration and frame count
    entry = keys.upload_desc("job:j|etho.tracking.pipeline", "user_data", 2.0, 20)
    gw = ReplayGateway(tiny_index((entry, {"id": "file-recorded"})), tmp_path / "s")
    assert gw.upload_file(a, tag="dev", caller="job:j|etho.tracking.pipeline") == "file-recorded"
    gw2 = ReplayGateway(tiny_index((entry, {"id": "file-recorded"})), tmp_path / "s2")
    assert gw2.upload_file(b, tag="dev", caller="job:j|etho.tracking.pipeline") == "file-recorded"
    c = tmp_path / "c.mp4"
    make_video(c, 3.0, 64, 48)                                  # a longer clip is another upload
    with pytest.raises(ReplayMiss, match="upload_file"):
        gw2.upload_file(c, tag="dev", caller="job:j|etho.tracking.pipeline")


# ---- the index and the gateway -----------------------------------------------------------------
def test_index_roundtrip_is_deterministic(tmp_path):
    ix = tiny_index((chat_desc(), CHAT_RESP))
    ix.save(tmp_path / "a.jsonl.gz")
    ix.save(tmp_path / "b.jsonl.gz")
    assert (tmp_path / "a.jsonl.gz").read_bytes() == (tmp_path / "b.jsonl.gz").read_bytes()
    assert len(ReplayIndex.load(tmp_path / "a.jsonl.gz")) == 1


def test_replays_hits_with_recorded_cost_and_counts_occurrences(tmp_path):
    first, second = dict(CHAT_RESP, text="one"), dict(CHAT_RESP, text="two", cost_usd=0.002)
    gw = ReplayGateway(tiny_index((chat_desc(), first), (chat_desc(), second)), tmp_path)
    args = ("fireworks", "accounts/fireworks/models/muse-spark-open", msgs("judge t=1.0s to t=2.0s", "ZZZZ"))
    r1 = gw.chat(*args, effort="low", tag="dev", caller="job:j|etho.adjudication.tier1", max_tokens=2048)
    r2 = gw.chat(*args, effort="low", tag="dev", caller="job:j|etho.adjudication.tier1", max_tokens=2048)
    assert (r1.text, r1.cost_usd, r1.cache_hit, r1.latency_s) == ("one", 0.001, False, 0.0)
    assert (r2.text, r2.cost_usd) == ("two", 0.002)
    with pytest.raises(ReplayMiss, match="asked for more often"):                    # a third call: no recording left
        gw.chat(*args, effort="low", tag="dev", caller="job:j|etho.adjudication.tier1", max_tokens=2048)


def test_sam_replay_feeds_on_line_and_reads_back(tmp_path):
    desc = keys.sam_desc("mouse", "job:j|etho.tracking.pipeline", {"video": Path("c.mp4"), "n_frames": 3})
    gw = ReplayGateway(tiny_index((desc, {"text": "l0\nl1\nl2\n", "usage": {}, "frames_billed": 50,
                                          "cost_usd": 0.01})), tmp_path)
    seen = []
    res = gw.sam("mouse", tag="dev", video=tmp_path / "x.mp4", n_frames=3, on_line=seen.append,
                 caller="job:j|etho.tracking.pipeline")
    assert seen == ["l0", "l1", "l2"] and list(res.lines()) == seen and res.frames_billed == 50
    assert res.cost_usd == 0.01


def test_miss_names_key_caller_and_nearest_and_poisons_later_calls(tmp_path):
    gw = ReplayGateway(tiny_index((chat_desc("judge t=1.0s to t=2.0s"), CHAT_RESP),
                                  (chat_desc("judge t=5.0s to t=6.0s"), CHAT_RESP)), tmp_path)
    with pytest.raises(ReplayMiss) as e:
        gw.chat("fireworks", "accounts/fireworks/models/muse-spark-open", msgs("judge t=1.1s to t=2.0s", "AAAA"),
                effort="low", tag="dev", caller="job:j|etho.adjudication.tier1", max_tokens=2048)
    msg = str(e.value)
    assert "REPLAY MISS" in msg and "caller job:j|etho.adjudication.tier1" in msg
    assert keys.key_of(chat_desc("judge t=1.1s to t=2.0s"))[:16] in msg
    assert "nearest recorded keys" in msg and keys.key_of(chat_desc())[:16] in msg
    assert "-judge t=1.1s to t=2.0s" in msg and "+judge t=1.0s to t=2.0s" in msg      # the drifted window is shown
    assert gw.misses and "never falls back" in msg
    with pytest.raises(ReplayMiss, match="already failed"):                  # even a call that has a recording
        gw.chat("fireworks", "accounts/fireworks/models/muse-spark-open", msgs("judge t=1.0s to t=2.0s", "AAAA"),
                effort="low", tag="dev", caller="job:j|etho.adjudication.tier1", max_tokens=2048)


def test_recorded_tier1_window_drift_misses(tmp_path):
    ix = ReplayIndex.load(INDEX)
    t1 = [e for e in ix.entries if e["desc"]["caller"] == "etho.adjudication.tier1"]
    assert len(t1) == 46
    e = t1[3]
    req = e["desc"]["request"]
    ok = ReplayGateway(ix, tmp_path)
    kw = {k: v for k, v in req.items() if k not in ("messages", "effort", "n_images", "n_videos")}
    r = ok.chat(e["desc"]["provider"], e["desc"]["model"], copy.deepcopy(req["messages"]), effort=req["effort"],
                caller="job:x|etho.adjudication.tier1", **kw)          # the recorded request hits (media = placeholder)
    assert r.text == e["response"]["text"]
    drifted = copy.deepcopy(req["messages"])
    part = drifted[0]["content"][0]
    assert "from t=36.9s to t=38.4s" in part["text"]
    part["text"] = part["text"].replace("t=36.9s to t=38.4s", "t=37.0s to t=38.4s")
    with pytest.raises(ReplayMiss) as miss:
        ok.chat(e["desc"]["provider"], e["desc"]["model"], drifted, effort=req["effort"],
                caller="job:x|etho.adjudication.tier1", **kw)
    assert "t=37.0s" in str(miss.value) and "t=36.9s" in str(miss.value)


# ---- the lockdown ------------------------------------------------------------------------------
def test_lockdown_blocks_gateway_http_sockets_and_keys(monkeypatch):
    from etho.gateway import Gateway, GatewayConfig
    monkeypatch.setenv("META_MODEL_API_KEY", "should-vanish")
    monkeypatch.setenv("ETHO_SECRETS", "/somewhere/secrets.env")
    with lockdown():
        import os
        assert "META_MODEL_API_KEY" not in os.environ and not Path(os.environ["ETHO_SECRETS"]).exists()
        assert GatewayConfig().api_key("meta") is None
        with pytest.raises(RealGatewayBlocked, match="live gateway"):
            Gateway(GatewayConfig())
        with pytest.raises(RealGatewayBlocked, match="HTTP request"):
            httpx.Client().get("https://api.meta.ai/v1/models")
        s = socket.socket()
        try:
            with pytest.raises(RealGatewayBlocked, match="network connection"):
                s.connect(("93.184.216.34", 443))
        finally:
            s.close()
    assert os.environ["META_MODEL_API_KEY"] == "should-vanish"                 # restored
    assert callable(Gateway.__init__) and Gateway.__init__.__name__ == "__init__"


# ---- end to end: the real app pipeline on the committed clip and recording -----------------------
@pytest.fixture(scope="module")
def replayed(tmp_path_factory):
    import os

    from etho.replay.session import load_definitions, run_job
    here = os.getcwd()
    work = tmp_path_factory.mktemp("replay_run")
    gw = ReplayGateway(ReplayIndex.load(INDEX), work / "scratch")
    try:
        with lockdown():
            sess = run_job(gw, CLIP, load_definitions(), work, replay=True, say=None)
            yield sess, gw, work
            sess.close()
    finally:
        os.chdir(here)


def test_end_to_end_labels_equal_the_recorded_run(replayed):
    sess, gw, work = replayed
    exp = json.loads((SAMPLE / "expected.json").read_text())
    res = sess.result
    assert res["versions"][0]["bouts"] == exp["bouts"]
    assert res["versions"][0]["rule_bouts"] == exp["rule_bouts"]
    assert res["versions"][0]["metrics"] == exp["metrics"]
    for fname, want in (("labels.npz", "labels_sha256"), ("labels_rules.npz", "rule_labels_sha256")):
        labels = np.load(sess.out_dir / fname)["labels"]
        assert hashlib.sha256(np.ascontiguousarray(labels).tobytes()).hexdigest() == exp[want]
    assert len(gw.served) == len(gw.index) == 56 and not gw.misses            # every recorded call used, none missed
    assert res["warnings"] == []


def test_end_to_end_label_everywhere_and_no_spend(replayed):
    sess, gw, work = replayed
    assert LABEL in (sess.out_dir / "report.html").read_text()
    res = sess.result
    assert res["replay"]["label"] == LABEL
    assert not (work / "ops" / "spend.jsonl").exists() or not (work / "ops" / "spend.jsonl").read_text().strip()
    # the page the app serves carries the label too
    from fastapi.testclient import TestClient

    from etho.replay.serve import LabelMiddleware
    sess.cfg.readonly_path.write_text("x")
    c = TestClient(LabelMiddleware(sess.app))
    for path in ("/", f"/p/{sess.job_id}", f"/api/projects/{sess.job_id}/report"):
        body = c.get(path).text
        assert body.count("replay-label") == 1 and LABEL in body, path
    assert c.post("/api/jobs", data={"definitions": "{}"}, files={"video": ("a.mp4", b"x")}).status_code == 403


def test_edited_definition_misses_loudly_in_rule_synthesis(tmp_path, monkeypatch):
    from etho.replay.session import load_definitions, run_job
    monkeypatch.chdir(tmp_path)                       # run_job changes directory; the monkeypatch restores it
    d = load_definitions()
    d["definitions"]["attack"] = d["definitions"]["attack"].replace("biting", "nipping")
    gw = ReplayGateway(ReplayIndex.load(INDEX), tmp_path / "scratch")
    with lockdown(), pytest.raises(ReplayMiss) as e:
        run_job(gw, CLIP, d, tmp_path / "w", replay=True, say=None)
    msg = str(e.value)
    assert "REPLAY MISS" in msg and "caller job:" in msg and "etho.rules.synthesize" in msg
    assert "nearest recorded keys" in msg and gw.misses


def test_committed_package_is_small_dev_split_and_has_no_keys():
    assert sum(p.stat().st_size for p in SAMPLE.iterdir()) < 30_000_000
    text = gzip.open(INDEX, "rt").read()
    for needle in ("Bearer ", "api_key", "/home/", "secrets"):
        assert needle not in text, needle
    assert not re.search(r"\b(sk|fw|bt|pk)[-_][A-Za-z0-9]{16,}", text)
    clip = json.loads((SAMPLE / "clip.json").read_text())
    assert clip["split"] == "dev" and clip["license"] == "CC BY 4.0"
    sha = hashlib.sha256(CLIP.read_bytes()).hexdigest()
    assert sha == clip["sha256"] == ReplayIndex.load(INDEX).header["clip"]["sha256"]
    credits = (SAMPLE / "CREDITS.md").read_text()
    assert "Sun et al. 2021" in credits and "CC BY 4.0" in credits and "data.caltech.edu/records/s0vdx-0k302" in credits
