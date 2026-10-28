"""Gateway.delete_file: Meta Files API `DELETE /v1/files/{file_id}` (documented at dev.meta.ai/docs/file-handling).
Mocked transport only."""

import json

import httpx
import pytest

from etho.gateway import PolicyError
from etho.gateway.cache import request_key

FID = "file-842549258569145"
KEY = request_key("meta", "/files", {"sha256": "abc", "purpose": "user_data"})


def rows(cfg):
    return [json.loads(x) for x in cfg.ledger_path.read_text().splitlines()]


def test_delete_sends_delete_to_the_file_url_free_and_logged(make_gw, cfg):
    seen = []

    def handler(req):
        seen.append((req.method, str(req.url), req.headers["authorization"]))
        return httpx.Response(200, json={"id": FID, "object": "file", "deleted": True})
    gw = make_gw(handler)
    assert gw.delete_file(FID, tag="dev", caller="job:j1|etho.app.test") is True
    assert seen == [("DELETE", f"https://api.meta.ai/v1/files/{FID}", "Bearer test-meta")]
    r = rows(cfg)[-1]
    assert r["cost_usd"] == 0.0 and r["deleted"] is True and r["file_id"] == FID and r["method"] == "DELETE"
    assert r["caller"] == "job:j1|etho.app.test" and r["tag"] == "dev" and "Bearer" not in json.dumps(r)


def test_success_forgets_the_upload_cache_entry_so_a_dead_id_is_not_reused(make_gw, cfg):
    gw = make_gw(lambda req: httpx.Response(200, json={"id": FID, "object": "file", "deleted": True}))
    gw.cache.put_json(KEY, {"id": FID})
    other = request_key("meta", "/files", {"sha256": "other", "purpose": "user_data"})
    gw.cache.put_json(other, {"id": "file-1"})
    assert gw.delete_file(FID, tag="dev", request_key=KEY)
    assert gw.cache.get_json(KEY) is None and gw.cache.get_json(other) == {"id": "file-1"}


def test_404_means_already_gone(make_gw, cfg):
    gw = make_gw(lambda req: httpx.Response(404, json={"error": {"message": "no such file"}}))
    gw.cache.put_json(KEY, {"id": FID})
    assert gw.delete_file(FID, tag="dev", request_key=KEY) is True
    assert rows(cfg)[-1]["deleted"] is True and "404" in rows(cfg)[-1]["note"] and gw.cache.get_json(KEY) is None


def test_server_errors_are_retried_then_succeed(make_gw, cfg):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, text="busy") if len(calls) < 3 else httpx.Response(200, json={"deleted": True})
    gw = make_gw(handler)
    assert gw.delete_file(FID, tag="dev") is True and len(calls) == 3 and len(gw.sleeps) == 2


def test_ambiguous_failure_is_not_retried_and_is_recorded_as_undeleted(make_gw, cfg):
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ReadTimeout("timed out", request=req)
    gw = make_gw(handler)
    gw.cache.put_json(KEY, {"id": FID})
    assert gw.delete_file(FID, tag="dev", request_key=KEY) is False
    assert len(calls) == 1
    r = rows(cfg)[-1]
    assert r["deleted"] is False and r["maybe_undeleted"] is True and "ambiguous" in r["error"] and r["cost_usd"] == 0.0
    assert gw.cache.get_json(KEY) == {"id": FID}                    # kept: the caller can still find the id


def test_persistent_provider_error_returns_false_without_raising(make_gw, cfg):
    gw = make_gw(lambda req: httpx.Response(403, text="forbidden"))
    assert gw.delete_file(FID, tag="dev") is False
    assert rows(cfg)[-1]["maybe_undeleted"] is True and "403" in rows(cfg)[-1]["error"]


def test_bad_ids_and_tags_are_refused_before_any_request(make_gw, cfg):
    def handler(req):
        raise AssertionError("no request may be sent")
    gw = make_gw(handler)
    for bad in ("", "../etc/passwd", "a/b", "file id", "x" * 200, None):
        with pytest.raises(PolicyError):
            gw.delete_file(bad, tag="dev")
    with pytest.raises(PolicyError):
        gw.delete_file(FID, tag="not-a-tag")
    assert not cfg.ledger_path.exists()
