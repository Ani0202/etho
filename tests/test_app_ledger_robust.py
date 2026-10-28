"""The app's reading of the shared ledger fails closed: a row it cannot read closes the caps, it never hides the rows
after it, and a bad ledger never stops the service from starting or answering."""

import json
import math
from datetime import datetime, timezone

import pytest

from etho.app.config import AppConfig
from etho.app.guards import Refused, SpendGuard
from etho.app.jobgw import BAD_ROW_USD, AppLedger
from tests.app_env import make_app

NOW = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)


def row(caller="job:a|x", cost=1.0, tag="demo-reserve", **kw):
    return {"caller": caller, "cost_usd": cost, "tag": tag, "time": NOW.isoformat(), **kw}


def write(path, *lines):
    path.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))
    return AppLedger(path)


def guard(path, led):
    cfg = AppConfig(root=path.parent, tag="demo-reserve", daily_cap_usd=3.0, total_cap_usd=20.0)
    return SpendGuard(cfg, led, now=lambda: NOW)


def test_a_non_dict_line_never_hides_the_rows_after_it(tmp_path):
    p = tmp_path / "s.jsonl"
    led = write(p, row(cost=1.0), "[1,2]", "5", '"job:a|x"', row(cost=1.0), row("job:b|x", 1.0))
    led.refresh()
    # the lines that mention the app's prefix but are not rows (a string, here) close the caps; plain junk is only logged
    assert led.job_cost("a") == 2.0 and led.job_cost("b") == 1.0           # both later rows were read
    assert led.bad_rows == 1 and led.spend_total("demo-reserve") >= BAD_ROW_USD
    led2 = write(tmp_path / "t.jsonl", row(cost=1.0), "[1,2]", "5", row(cost=1.0))
    assert led2.spend_total("demo-reserve") == pytest.approx(2.0) and led2.bad_rows == 0   # junk without the prefix


@pytest.mark.parametrize("bad", ['"nan"', "NaN", "Infinity", "-Infinity", "-0.5", '"abc"', '[1]', '{"x":1}', '"1e999"'])
def test_an_unreadable_cost_on_an_app_row_closes_the_caps_and_later_rows_still_count(tmp_path, bad):
    p = tmp_path / "s.jsonl"
    p.write_text(f'{{"caller":"job:a|x","cost_usd":{bad},"tag":"demo-reserve","time":"{NOW.isoformat()}"}}\n'
                 + json.dumps(row("job:b|x", 0.25)) + "\n")
    led = AppLedger(p)
    assert led.spend_total("demo-reserve") >= BAD_ROW_USD and led.bad_rows == 1
    assert led.job_cost("b") == 0.25                                        # the row after it was not lost
    g = guard(p, led)
    with pytest.raises(Refused) as e:
        g.reserve("new", 0.1)
    assert e.value.code in ("daily_cap", "total_cap")                      # the caps are closed
    assert g.status()["remaining_usd"] == 0.0


def test_a_missing_cost_is_free_but_a_bad_one_on_another_callers_row_is_ignored(tmp_path):
    p = tmp_path / "s.jsonl"
    led = write(p, {"caller": "job:a|x", "tag": "demo-reserve", "time": NOW.isoformat()},
                row("scripts.demo:run", float("nan")), row("scripts.demo:run", -5))
    assert led.spend_total("demo-reserve") == 0.0 and led.bad_rows == 0


def test_an_abandoned_reservation_with_a_bad_cost_closes_the_caps(tmp_path):
    p = tmp_path / "s.jsonl"
    led = write(p, row("ledger:abandoned-reservation", "oops", abandoned_reservation="r1"))
    assert led.spend_total("demo-reserve") >= BAD_ROW_USD


def test_invalid_json_that_names_the_app_closes_the_caps_and_the_rest_is_read(tmp_path):
    p = tmp_path / "s.jsonl"
    led = write(p, '{"caller": "job:a|x", "cost_usd": 1.0, "time": ', row("job:b|x", 0.5))
    assert led.spend_total("demo-reserve") >= BAD_ROW_USD and led.job_cost("b") == 0.5
    led2 = write(tmp_path / "t.jsonl", "{broken", row("job:b|x", 0.5))               # nothing app-related in the junk
    assert led2.spend_total("demo-reserve") == pytest.approx(0.5)


def test_offsets_advance_once_the_chunk_is_processed_and_nothing_is_counted_twice(tmp_path):
    p = tmp_path / "s.jsonl"
    led = write(p, row(cost=1.0), '"job:a|x"')
    led.refresh()
    led.refresh()
    assert led.bad_rows == 1 and led.job_cost("a") == 1.0
    with p.open("a") as f:
        f.write(json.dumps(row(cost=2.0))[:30])                                      # a partial line stays buffered
    led.refresh()
    assert led.job_cost("a") == 1.0
    with p.open("a") as f:
        f.write(json.dumps(row(cost=2.0))[30:] + "\n")
    assert led.job_cost("a") == 3.0 and led.bad_rows == 1


def test_a_bug_while_applying_a_row_does_not_lose_it_or_the_chunk(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    led = write(p, row(cost=1.0), row("job:b|x", 1.0))
    real = led._row
    calls = []

    def flaky(line):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real(line)
    monkeypatch.setattr(led, "_row", flaky)
    led.refresh()
    assert led.bad_rows == 1 and led.job_cost("b") == 1.0 and led.spend_total("demo-reserve") >= BAD_ROW_USD
    led.refresh()
    assert led.bad_rows == 1                                                          # not re-read


def test_an_unreadable_ledger_counts_as_spent_and_recovers(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(row(cost=1.0)) + "\n")
    led = AppLedger(p)
    assert led.spend_total("demo-reserve") == 1.0
    p.chmod(0o000)
    try:
        import os
        if os.access(p, os.R_OK):
            pytest.skip("running as a user that ignores file modes")
        led.refresh()
        assert led.unreadable and led.spend_total("demo-reserve") >= BAD_ROW_USD
        assert math.isfinite(led.spend_window(NOW, tag="demo-reserve"))
    finally:
        p.chmod(0o644)
    assert led.spend_total("demo-reserve") == 1.0 and led.unreadable is None


def test_the_service_starts_and_answers_with_a_bad_ledger(tmp_path, monkeypatch):
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "spend.jsonl").write_text('"job:zzz"\n{"caller":"job:a|x","cost_usd":"nan"}\n')
    client, c = make_app(tmp_path, monkeypatch)                    # create_app runs the startup purge against it
    h = client.get("/api/health").json()
    assert h["mode"] == "live" and h["caps"]["remaining_usd"] == 0.0
    cfg = client.get("/api/config").json()
    assert cfg["cap"]["remaining_usd"] == 0.0 and cfg["live"]["capped"] and cfg["live"]["can_submit"] is False
    assert cfg["live"]["message"]
    assert h["ledger_bad_rows"] == 2 and c.app.state.ledger.bad_rows == 2          # localhost: no code, so the full view


def test_the_startup_purge_is_guarded(tmp_path, monkeypatch):
    from etho.app import server as srv
    from etho.app.retention import Retention

    def boom(self, *a, **k):
        raise RuntimeError("ledger exploded")
    monkeypatch.setattr(Retention, "run_once", boom)
    client, c = make_app(tmp_path, monkeypatch)                    # must not raise
    assert client.get("/api/health").status_code == 200 and srv is not None
