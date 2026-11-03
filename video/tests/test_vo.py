"""Voice-over pipeline tests with synthetic audio (tones and silence made by ffmpeg). No network, no models."""
import subprocess

import pytest

from video import timeline as T
from video import vo
from video.assemble import Piece, retime_pieces

needs_ffmpeg = pytest.mark.skipif(not vo.have_ffmpeg(), reason="ffmpeg not installed")
KEYS = [b.key for b in T.BEATS]


def test_sentences_cover_v5_text():
    s = vo.beat_sentences()
    assert list(s) == KEYS
    assert sum(len(v) for v in s.values()) == 34
    assert s["fly"][0].startswith("Same pipeline, new animal")
    assert sum(vo.word_count(x) for v in s.values() for x in v) == 395


def _durs(**over):
    d = {k: 8.0 for k in KEYS}
    d.update(over)
    return d


def test_timeline_fits_untouched():
    plans = vo.plan_timeline(_durs())
    assert [p.dur for p in plans] == [b.dur for b in T.BEATS]
    assert plans[0].start == 0 and plans[-1].end == T.total()


def test_timeline_extension_is_absorbed_by_later_spare():
    # hook take of 20.0 s needs 20.7 s: 0.7 s over; the next beat (15 s, take 10 s -> spare 4.3 s) gives it up
    plans = vo.plan_timeline(_durs(hook=20.0))
    assert plans[0].dur == pytest.approx(20.7)
    assert plans[1].dur == pytest.approx(14.3)
    assert plans[1].dur >= plans[1].need
    assert plans[2].start == pytest.approx(35.0)       # everything after is back on the plan
    assert plans[-1].end == pytest.approx(T.total())


def test_timeline_shrink_stops_at_minimum_then_total_grows():
    # who's take needs 15.7 s min; hook needs 30.7; spare later is limited by each beat's own minimum
    plans = vo.plan_timeline(_durs(hook=30.0, who=14.5, live_run=27.5))
    for p in plans:
        assert p.dur >= p.need - 1e-9
    total = plans[-1].end
    assert total > T.total() - 1e-9
    carry = sum(max(0, p.need - p.nominal_dur) for p in plans) - sum(
        p.nominal_dur - p.dur for p in plans if p.dur < p.nominal_dur)
    assert total == pytest.approx(T.total() + carry)


def test_timeline_over_limit_reports_per_beat():
    with pytest.raises(vo.OverLimit) as e:
        vo.plan_timeline(_durs(hook=60.0, live_run=80.0))
    msg = str(e.value)
    assert "exceeds the 180 s limit by" in msg
    assert all(k in msg for k in KEYS)


def test_cue_text_split_and_chunks():
    assert vo.split_lines("short text") == "short text"
    two = vo.split_lines("word " * 14 + "end")
    assert two.count("\n") == 1 and all(len(x) <= vo.MAX_LINE + 6 for x in two.split("\n"))
    long = vo.beat_sentences()["code"][0]
    chunks = vo.chunk_sentence(long)
    assert len(chunks) >= 2 and " ".join(chunks) == long
    assert all(len(c) <= 2 * vo.MAX_LINE for c in chunks)


def test_srt_roundtrip(tmp_path):
    cues = [vo.Cue(0.3, 2.5, "Hello there", "hook"), vo.Cue(3.0, 4.25, "Second\nline", "hook")]
    vo.write_srt(cues, tmp_path / "x.srt")
    got = vo.parse_srt((tmp_path / "x.srt").read_text())
    assert [(round(a, 3), round(b, 3)) for a, b, _ in got] == [(0.3, 2.5), (3.0, 4.25)]
    assert got[1][2] == "Second line"


@needs_ffmpeg
def test_find_takes_reports_missing(tmp_path):
    (tmp_path / "01_hook.mp3").write_bytes(b"x")
    with pytest.raises(FileNotFoundError) as e:
        vo.find_takes(tmp_path)
    assert "02_who" in str(e.value) and "01_hook" not in str(e.value)


@needs_ffmpeg
def test_load_take_trims_and_normalizes(tmp_path):
    src = tmp_path / "t.wav"
    raw = vo.make_synthetic_take(src, [10, 10], lead_s=1.0, tail_s=1.5, level_db=-30.0)
    t = vo.load_take("hook", src, tmp_path / "w")
    speech = raw - 1.0 - 1.5
    assert t.dur == pytest.approx(speech + 2 * vo.KEEP_S, abs=0.12)
    loud = vo.measure_lufs(t.wav)
    assert loud["integrated_lufs"] == pytest.approx(vo.TARGET_LUFS, abs=1.0)
    assert loud["true_peak_dbtp"] <= vo.TARGET_TP + 0.3


@needs_ffmpeg
def test_sentence_spans_use_pauses(tmp_path):
    src = tmp_path / "t.wav"
    vo.make_synthetic_take(src, [8, 20, 12], gap_s=0.5)
    t = vo.load_take("x", src, tmp_path / "w")
    b = vo.sentence_spans(t.wav, vo.TARGET_LUFS, t.dur, ["a " * 8, "b " * 20, "c " * 12])
    assert b.method == "pauses" and b.pauses_found == 2
    assert b.spans[0][1] == pytest.approx(b.spans[1][0] - 0.5, abs=0.1)
    assert b.spans[1][0] < b.spans[1][1] < b.spans[2][0]


@needs_ffmpeg
def test_sentence_spans_fall_back_to_word_proportional(tmp_path):
    src = tmp_path / "t.wav"
    vo.make_synthetic_take(src, [20, 20], gap_s=0.1)     # gap shorter than 0.25 s: not a pause
    t = vo.load_take("x", src, tmp_path / "w")
    b = vo.sentence_spans(t.wav, vo.TARGET_LUFS, t.dur, ["a " * 10, "b " * 30])
    assert b.method == "word-proportional"
    (s0, e0), (s1, e1) = b.spans
    assert e0 == pytest.approx(s1) and (e0 - s0) / (e1 - s1) == pytest.approx(1 / 3, rel=0.02)


@needs_ffmpeg
def test_mix_places_takes_on_the_timeline(tmp_path):
    takes = {}
    for k in KEYS:
        src = tmp_path / f"{k}.wav"
        vo.make_synthetic_take(src, [6, 6], lead_s=0.5, tail_s=0.5)
        takes[k] = vo.load_take(k, src, tmp_path / "w")
    plans = vo.plan_timeline({k: t.dur for k, t in takes.items()})
    mix = vo.mix_track(takes, plans, plans[-1].end, tmp_path / "mix.wav")
    assert vo.probe_duration(mix) == pytest.approx(plans[-1].end, abs=0.05)


def test_retime_pieces_hold_and_trim():
    ps = [Piece(b.key, "a", None, b.dur / 2, True) for b in T.BEATS] + \
         [Piece(b.key, "b", None, b.dur / 2, True) for b in T.BEATS]
    ps.sort(key=lambda p: KEYS.index(p.beat))
    new = {k: b.dur + (1.0 if k == "hook" else -1.0 if k == "who" else 0) for k, b in zip(KEYS, T.BEATS, strict=True)}
    out = retime_pieces(ps, new)
    for k in KEYS:
        assert sum(p.dur for p in out if p.beat == k) == pytest.approx(new[k])
    hook = [p for p in out if p.beat == "hook"]
    assert hook[0].dur == 10.0 and hook[1].dur == 11.0     # extra time goes to the last piece (held)


@needs_ffmpeg
def test_sentence_spans_find_pauses_above_a_room_noise_floor(tmp_path):
    """Real takes have room noise in the pauses; after loudness normalization it sits above level - 20 dB, so the
    threshold must rise until the pauses appear (the Nov 3 takes found 0 pauses at the fixed threshold)."""
    clean = tmp_path / "clean.wav"
    vo.make_synthetic_take(clean, [8, 20, 12], gap_s=0.5)
    noisy = tmp_path / "noisy.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clean), "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:amplitude=0.008:sample_rate=48000", "-filter_complex",
                    "[0][1]amix=inputs=2:duration=first:normalize=0", str(noisy)], check=True)
    t = vo.load_take("x", noisy, tmp_path / "w")
    pauses, thr = vo.detect_pauses(t.wav, vo.TARGET_LUFS, t.dur, need=2)
    assert len(pauses) >= 2 and thr > -60.0
    b = vo.sentence_spans(t.wav, vo.TARGET_LUFS, t.dur, ["a " * 8, "b " * 20, "c " * 12])
    assert b.method == "pauses"
