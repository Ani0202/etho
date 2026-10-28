"""Decode-free SAM line parser, and parse_line equivalence with the meta_sam_parser reference."""

from __future__ import annotations

import gzip
import math
from pathlib import Path

import numpy as np
import pytest
from meta_sam_parser import (
    CompletedOutcome,
    decode_mask_to_raster,
    image_segmentation_format,
    video_segmentation_format,
)

from etho.tracking import mask_codec
from etho.tracking.sam_lines import SamDiagnostic, iter_sam_records, parse_sam_line
from etho.tracking.sam_parse import parse_line

FIX = Path(__file__).parent / "fixtures"

BOX = "<|box;x1=10;y1=20;x2=13;y2=22;w=100;h=50{c}|>"
MASK = "<|mask;x=0;y=0;data=3,4,{payload}{c}|>"
PAYLOAD = "!!!!)"  # content is irrelevant to the parser; it is never decoded


def _line(frame=0, oid=0, bc="", mc="", payload=PAYLOAD):
    return f"<{frame}f>{oid}" + BOX.format(c=bc) + MASK.format(payload=payload, c=mc)


def _fixture_lines(name):
    with gzip.open(FIX / name, "rt") as fh:
        return fh.read().splitlines()


def test_single_record_fields():
    (r,) = parse_sam_line(_line(frame=7, oid=3, bc=";c=0.5", mc=";c=0.75"))
    assert (r.frame, r.object_id) == (7, "3")
    assert r.box == (10, 20, 14, 23)  # half-open
    assert (r.left, r.top, r.right, r.bottom) == (10, 20, 14, 23)
    assert (r.source_width, r.source_height) == (100, 50)
    assert r.confidence == 0.75 and r.box_confidence == 0.5
    assert (r.encoding, r.height, r.width, r.payload) == ("one_bit", 3, 4, PAYLOAD)


def test_lossless_encoding_detected_and_no_confidence():
    (r,) = parse_sam_line(_line(payload="~abcde"))
    assert r.encoding == "lossless" and r.payload == "~abcde"
    assert r.confidence is None and r.box_confidence is None


def test_multiple_objects_with_and_without_comma():
    one = _line(frame=2, oid=0)
    second = "1" + BOX.format(c="") + MASK.format(payload=PAYLOAD, c="")
    third = ",12" + BOX.format(c="") + MASK.format(payload=PAYLOAD, c="")
    recs = parse_sam_line(one + second + third)
    assert [(r.frame, r.object_id) for r in recs] == [(2, "0"), (2, "1"), (2, "12")]


def test_fields_in_any_order_and_whitespace():
    line = (
        "<5f>0<|mask; c=0.25 ;y=0;x=0; data=3,4,!!!!)|>"
        "<|box;h=50;w=100;y2=22;x2=13;y1=20; x1 = 10|>"
    )
    (r,) = parse_sam_line(line)
    assert r.box == (10, 20, 14, 23) and r.confidence == 0.25


def test_unknown_tokens_and_fields_are_ignored():
    line = "<0f>0<|extra;foo=1|>" + BOX.format(c=";zzz=9") + MASK.format(payload=PAYLOAD, c="")
    assert len(parse_sam_line(line)) == 1


@pytest.mark.parametrize("bad", ["nan", "2", "-0.1", "abc", "1e999", ""])
def test_bad_confidence_is_dropped_not_fatal(bad):
    (r,) = parse_sam_line(_line(mc=f";c={bad}"))
    assert r.confidence is None


def test_non_frame_lines_ignored():
    for line in ("", "   ", "hello", "<no frame>", "<|mask|>", "text <0f>0"):
        diags: list[SamDiagnostic] = []
        assert parse_sam_line(line, diagnostics=diags) == [] and diags == []


@pytest.mark.parametrize(
    "line,code",
    [
        ("<0f>", "malformed_record"),
        ("<0f>0", "malformed_record"),
        ("<0f>0" + BOX.format(c=""), "malformed_record"),  # no mask
        ("<0f>0" + MASK.format(payload="!!!)", c=""), "malformed_record"),  # no box
        ("<0f>0" + BOX.format(c="") + BOX.format(c="") + MASK.format(payload="!!!)", c=""), "malformed_record"),
        ("<0f>0" + BOX.format(c="").replace("x1=10;", "") + MASK.format(payload="!!!)", c=""), "malformed_record"),
        ("<0f>0" + BOX.format(c="").replace("x2=13", "x2=9") + MASK.format(payload="!!!)", c=""), "invalid_box"),
        ("<0f>0" + BOX.format(c="").replace("w=100", "w=13") + MASK.format(payload="!!!)", c=""), "invalid_box"),
        ("<0f>0" + BOX.format(c="").replace("x1=10", "x1=-1") + MASK.format(payload="!!!)", c=""), "invalid_box"),
        ("<0f>0" + BOX.format(c="") + MASK.format(payload="!!!)", c="").replace("x=0", "x=1"), "malformed_record"),
        ("<0f>0" + BOX.format(c="") + MASK.format(payload="!!!)", c="").replace("3,4", "0,4"),
         "invalid_mask_size"),
        ("<0f>0" + BOX.format(c="") + MASK.format(payload="!!!)", c="").replace("3,4,!", "3,4,?"),
         "malformed_record"),
        ("<0f>0" + BOX.format(c="") + "<|mask;x=0;y=0;x=0;data=3,4,!!)|>", "malformed_record"),  # repeated key
        ("<99999999999999999999f>0" + BOX.format(c="") + MASK.format(payload="!!!)", c=""), "invalid_frame"),
    ],
)
def test_malformed_lines_yield_diagnostic_not_exception(line, code):
    diags: list[SamDiagnostic] = []
    assert parse_sam_line(line, diagnostics=diags) == []
    assert [d.code for d in diags] == [code]


def test_good_records_before_a_bad_one_are_kept():
    good = _line(frame=1, oid=0)
    bad = "1<|box;x1=1|>"
    diags: list[SamDiagnostic] = []
    recs = parse_sam_line(good + bad, diagnostics=diags)
    assert [r.object_id for r in recs] == ["0"] and len(diags) == 1


def test_image_media_requires_frame_zero():
    diags: list[SamDiagnostic] = []
    assert len(parse_sam_line(_line(frame=0), media="image")) == 1
    assert parse_sam_line(_line(frame=3), media="image", diagnostics=diags) == []
    assert diags[0].code == "unexpected_frame"


def test_parser_never_decodes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("parser must not decode masks")

    names = ("decode_mask", "decode_one_bit", "decode_lossless", "_unpack", "_one_bit_kernel", "_lossless_kernel")
    for name in names:
        monkeypatch.setattr(mask_codec, name, boom)
    lines = _fixture_lines("sam_lossless_5lines.txt.gz") + _fixture_lines("sam_onebit_5lines.txt.gz")
    assert len(list(iter_sam_records(lines))) == 10
    # a syntactically valid but undecodable payload is fine for the parser
    assert len(parse_sam_line(_line(payload="!garbage"))) == 1


def test_iter_sam_records_collects_diagnostics():
    diags: list[SamDiagnostic] = []
    recs = list(iter_sam_records([_line(frame=0), "<1f>", "plain text", _line(frame=2)], diagnostics=diags))
    assert [r.frame for r in recs] == [0, 2] and len(diags) == 1


# ---- equivalence with the reference parser on real lines ----------------------------------------------
def _reference_records(lines, media="video"):
    fmt = video_segmentation_format() if media == "video" else image_segmentation_format()
    p = fmt.create_parser()
    for line in lines:
        p.push(line + "\n", emit=False)
    res = p.finish(CompletedOutcome()).result
    assert not res.diagnostics
    return [r for r in res.records if r.kind == "mask"]


@pytest.mark.parametrize("name", ["sam_lossless_5lines.txt.gz", "sam_onebit_5lines.txt.gz"])
def test_records_match_reference_parser(name):
    lines = _fixture_lines(name)
    ours = list(iter_sam_records(lines))
    theirs = _reference_records(lines)
    assert len(ours) == len(theirs) == 5
    for a, b in zip(ours, theirs, strict=True):
        assert a.frame == b.frame.frame_index and a.object_id == b.object_id
        assert a.box == (b.bounds.left, b.bounds.top, b.bounds.right, b.bounds.bottom)
        assert a.confidence == b.confidence
        assert (a.encoding, a.payload, a.width, a.height) == (
            b.mask.encoding, b.mask.payload, b.mask.width, b.mask.height)


@pytest.mark.parametrize("name", ["sam_lossless_5lines.txt.gz", "sam_onebit_5lines.txt.gz"])
def test_parse_line_matches_reference_path(name):
    """parse_line must return the same ObjectFrames and masks the old reference-based path did."""
    lines = _fixture_lines(name)
    ref = _reference_records(lines)
    for line, rr in zip(lines, ref, strict=True):
        ((of, mask),) = parse_line(line, want_mask=True)
        want = np.frombuffer(decode_mask_to_raster(rr.mask), np.uint8).reshape(rr.mask.height, rr.mask.width)
        assert np.array_equal(mask, want)
        assert of.frame == rr.frame.frame_index and of.object_id == rr.object_id
        assert of.box == (rr.bounds.left, rr.bounds.top, rr.bounds.right, rr.bounds.bottom)
        assert of.area == int(want.sum())
        assert of.confidence == rr.confidence
        ys, xs = np.nonzero(want)
        assert math.isclose(of.cx, xs.mean() + rr.bounds.left) and math.isclose(of.cy, ys.mean() + rr.bounds.top)


def test_parse_line_skips_corrupt_payload_and_keeps_others(caplog):
    good = _fixture_lines("sam_onebit_5lines.txt.gz")[0]
    bad = _line(frame=1, oid=0, payload="!garbage!")
    assert len(parse_line(good)) == 1
    assert parse_line(bad) == []
    assert "skipping" in caplog.text


def test_parse_line_keep_rle_and_no_mask_by_default():
    line = _fixture_lines("sam_onebit_5lines.txt.gz")[0]
    ((of, mask),) = parse_line(line, frame_shape=(570, 1024), keep_rle=True)
    assert mask is None and of.rle is not None and of.rle["size"] == [570, 1024]
