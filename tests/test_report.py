import numpy as np

from etho.report.report import ReportInput, build, metrics

CODES = {"attack": 0, "investigation": 1, "mount": 2, "other": 3}


def test_metrics_and_html(tmp_path):
    lab = np.full(600, 3, np.int8)
    lab[100:130] = 1
    lab[200:210] = 1
    lab[300:350] = 2
    m = metrics(lab, 10.0, CODES)
    assert m["investigation"]["bouts"] == 2 and m["investigation"]["total_s"] == 4.0
    assert m["investigation"]["latency_s"] == 10.0 and m["attack"]["bouts"] == 0 and m["attack"]["latency_s"] is None
    assert m["mount"]["pct_time"] == round(100 * 5 / 60, 1)
    out = build(ReportInput("t", lab, 10.0, {"video": "v.mp4"}), tmp_path / "r.html", codes=CODES, gw=None)
    html = out.read_text()
    assert html.count("<rect") == 3 and "investigation: 10.0-13.0 s" in html and "var(--series-3)" in html


def _header(tmp_path, meta):
    lab = np.full(100, 3, np.int8)
    return build(ReportInput("t", lab, 10.0, meta), tmp_path / "r.html", codes=CODES, gw=None).read_text()


def test_initial_report_keeps_the_training_free_header(tmp_path):
    for meta in ({"video": "v.mp4"}, {"video": "v.mp4", "version": 0}):
        h = _header(tmp_path, meta)
        assert "training-free: no labeled examples given to the models, no model training" in h


def test_revision_report_does_not_claim_no_labeled_examples(tmp_path):
    h = _header(tmp_path, {"video": "v.mp4", "version": 2, "n_corrections": 3})
    assert "rules revised from 3 researcher-corrected bouts; no model training" in h
    assert "no labeled examples" not in h and "training-free" not in h
    assert "researcher-corrected bout;" in _header(tmp_path, {"version": 1, "n_corrections": 1})
    h = _header(tmp_path, {"video": "v.mp4", "version": 1})           # the count is not known: leave the number out
    assert "rules revised from researcher-corrected bouts; no model training" in h and "no labeled examples" not in h
