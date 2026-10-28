"""Bit-exactness and edge cases for the numba SAM mask decoder (no network).

The reference is ``meta_sam_parser.decode_mask_to_raster`` (pure Python, ~0.15 s per mask), so the
fixtures are small: 5 real lines per encoding from cached SAM 3.1 outputs on a public dataset.
"""

from __future__ import annotations

import gzip
import random
import time
from pathlib import Path

import numpy as np
import pytest
from meta_sam_parser import InvalidSegmentationMaskError, SegmentationMask, decode_mask_to_raster
from meta_sam_parser import _mask_codec as ref

from etho.tracking.mask_codec import (
    MAX_PIXELS,
    MaskDecodeError,
    decode_lossless,
    decode_mask,
    decode_one_bit,
)
from etho.tracking.sam_lines import parse_sam_line

FIX = Path(__file__).parent / "fixtures"


def _records(name: str):
    with gzip.open(FIX / name, "rt") as fh:
        return [r for line in fh for r in parse_sam_line(line)]


@pytest.fixture(scope="module")
def lossless_records():
    return _records("sam_lossless_5lines.txt.gz")


@pytest.fixture(scope="module")
def onebit_records():
    return _records("sam_onebit_5lines.txt.gz")


def _reference(encoding: str, payload: str, width: int, height: int) -> np.ndarray:
    raw = decode_mask_to_raster(SegmentationMask(encoding=encoding, payload=payload, width=width, height=height))
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width)


# ---- bit-exactness on real masks --------------------------------------------------------------------
def test_fixtures_have_expected_encodings(lossless_records, onebit_records):
    assert len(lossless_records) == 5 and len(onebit_records) == 5
    assert {r.encoding for r in lossless_records} == {"lossless"}
    assert {r.encoding for r in onebit_records} == {"one_bit"}


def test_lossless_bit_exact_on_real_masks(lossless_records):
    for r in lossless_records:
        got = decode_lossless(r.payload, r.width, r.height)
        want = _reference("lossless", r.payload, r.width, r.height)
        assert got.dtype == np.uint8 and got.shape == (r.height, r.width)
        assert np.array_equal(got, want)
        assert 0 < got.sum() < got.size  # a real, non-trivial mask


def test_one_bit_bit_exact_on_real_masks(onebit_records):
    for r in onebit_records:
        got = decode_one_bit(r.payload, r.width, r.height)
        want = _reference("one_bit", r.payload, r.width, r.height)
        assert got.dtype == np.uint8 and got.shape == (r.height, r.width)
        assert np.array_equal(got, want)
        assert 0 < got.sum() < got.size


def test_decode_mask_dispatches_on_marker(lossless_records, onebit_records):
    for r in lossless_records[:1] + onebit_records[:1]:
        want = decode_lossless if r.encoding == "lossless" else decode_one_bit
        assert np.array_equal(decode_mask(r.payload, r.width, r.height), want(r.payload, r.width, r.height))


# ---- differential fuzzing against the reference ------------------------------------------------------
def _outcome(fn, *a):
    try:
        return fn(*a)
    except (InvalidSegmentationMaskError, MaskDecodeError):
        return None


@pytest.mark.parametrize("seed", range(12))
def test_one_bit_random_streams_match_reference(seed):
    """Random (non-canonical) streams drive every model path; skip the reference's re-encode check."""
    rng = random.Random(seed)
    w, h = rng.randrange(1, 90), rng.randrange(1, 70)
    data = bytes(rng.randrange(256) for _ in range(max(8, w * h // 6)))
    payload = "!" + ref._pack(data)
    want = _outcome(lambda: np.frombuffer(ref._decode_raster(payload, w, h), np.uint8).reshape(h, w))
    got = _outcome(decode_one_bit, payload, w, h)
    assert (want is None) == (got is None)
    if want is not None:
        assert np.array_equal(want, got)


def _lossless_random(seed: int):
    rng = random.Random(1000 + seed)
    w, h = rng.randrange(1, 60), rng.randrange(1, 40)
    body = bytes([rng.choice([0, 1, 2, 3, 4])]) + bytes(rng.randrange(256) for _ in range(w * h * 3 + 16))
    return "~" + ref._pack(body), w, h


@pytest.mark.parametrize("seed", range(12))
def test_lossless_random_streams_match_reference(seed):
    payload, w, h = _lossless_random(seed)
    want = _outcome(lambda: np.frombuffer(
        bytes(v >= 129 for v in ref._decode_lossless_raster(payload, w, h)), np.uint8).reshape(h, w))
    got = _outcome(decode_lossless, payload, w, h)
    assert (want is None) == (got is None)
    if want is not None:
        assert np.array_equal(want, got)


def test_lossless_random_streams_actually_decode():
    """Guard the fuzz above against passing vacuously (both sides raising)."""
    for seed in range(12):
        payload, w, h = _lossless_random(seed)
        assert decode_lossless(payload, w, h).shape == (h, w)


@pytest.mark.parametrize("seed", range(10))
def test_one_bit_roundtrip_random_masks(seed):
    """Encode with the reference encoder, decode with the fast decoder."""
    rng = np.random.default_rng(seed)
    h, w = int(rng.integers(1, 40)), int(rng.integers(1, 40))
    mask = (rng.random((h, w)) < rng.random()).astype(np.uint8)
    payload = ref._encode_raster(mask.ravel().tolist(), w, h)
    assert np.array_equal(decode_one_bit(payload, w, h), mask)


# ---- edge cases ---------------------------------------------------------------------------------------
def _lossless_all_zero_payload(w: int, h: int, selector: int = 0) -> str:
    """Hand-encode an all-zero lossless mask: every residual is 0, so only the zero-flag model runs."""
    enc = ref._RangeEncoder()
    zero, nonzero = 1, 1
    for _ in range(w * h):
        enc.encode(0, zero, zero + nonzero)
        zero += 32
        if zero + nonzero >= 16384:
            zero, nonzero = (zero >> 1) or 1, (nonzero >> 1) or 1
    return "~" + ref._pack(bytes([selector]) + enc.finish())


@pytest.mark.parametrize("w,h", [(1, 1), (7, 3), (64, 64), (1, 50), (50, 1), (200, 150)])
def test_empty_mask_both_encodings(w, h):
    zeros = np.zeros((h, w), dtype=np.uint8)
    one_bit = ref._encode_raster([0] * (w * h), w, h)
    lossless = _lossless_all_zero_payload(w, h)
    assert np.array_equal(decode_one_bit(one_bit, w, h), zeros)
    assert np.array_equal(decode_lossless(lossless, w, h), zeros)
    assert np.array_equal(_reference("one_bit", one_bit, w, h), zeros)
    assert np.array_equal(_reference("lossless", lossless, w, h), zeros)


def test_one_by_one_masks():
    for bit in (0, 1):
        payload = ref._encode_raster([bit], 1, 1)
        got = decode_one_bit(payload, 1, 1)
        assert got.shape == (1, 1) and got[0, 0] == bit
        assert np.array_equal(got, _reference("one_bit", payload, 1, 1))


def test_one_by_one_lossless_threshold():
    """1x1 lossless: residual symbol s with predictor 0. 253 -> 129 (set), 255 -> 128 (clear)."""
    for symbol, expected in ((253, 1), (255, 0), (2, 0)):
        enc = ref._RangeEncoder()
        enc.encode(1, 1, 2)  # non-zero flag
        enc.encode((symbol - 1) * 3, 3, 765)  # uniform prior over symbols 1..255 (3 each, total 765)
        payload = "~" + ref._pack(bytes([0]) + enc.finish())
        got = decode_lossless(payload, 1, 1)
        assert got.shape == (1, 1) and got[0, 0] == expected
        assert np.array_equal(got, _reference("lossless", payload, 1, 1))


def test_dtype_and_contiguity(onebit_records):
    r = onebit_records[0]
    m = decode_one_bit(r.payload, r.width, r.height)
    assert m.dtype == np.uint8 and m.flags["C_CONTIGUOUS"] and m.flags["WRITEABLE"]
    assert set(np.unique(m)) <= {0, 1}


# ---- malformed input --------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def good_one_bit():
    rng = np.random.default_rng(3)
    mask = (rng.random((12, 15)) < 0.3).astype(np.uint8)
    return ref._encode_raster(mask.ravel().tolist(), 15, 12), 15, 12


def test_wrong_marker(good_one_bit):
    payload, w, h = good_one_bit
    with pytest.raises(MaskDecodeError, match="must start with !"):
        decode_one_bit("~" + payload[1:], w, h)
    with pytest.raises(MaskDecodeError, match="must start with ~"):
        decode_lossless(payload, w, h)
    with pytest.raises(MaskDecodeError, match="must start with !"):
        decode_one_bit("", w, h)


def test_invalid_character(good_one_bit):
    payload, w, h = good_one_bit
    for bad in ('"', "|", ";", "}", "\x01"):
        with pytest.raises(MaskDecodeError, match="invalid character"):
            decode_one_bit(payload[:10] + bad + payload[11:], w, h)
    with pytest.raises(MaskDecodeError, match="invalid character"):
        decode_one_bit(payload[:10] + "é" + payload[11:], w, h)


def test_length_prefix_mismatch(good_one_bit):
    payload, w, h = good_one_bit
    with pytest.raises(MaskDecodeError, match="length does not match"):
        decode_one_bit(payload[:-2], w, h)
    with pytest.raises(MaskDecodeError, match="length does not match"):
        decode_one_bit(payload + "!!", w, h)


def test_missing_prefix():
    with pytest.raises(MaskDecodeError, match="length prefix"):
        decode_one_bit("!!!!", 4, 4)
    with pytest.raises(MaskDecodeError, match="length prefix"):
        decode_lossless("~", 4, 4)


def test_out_of_range_group():
    # length prefix 4 ("!!!!&" = 4), then a group of five max digits (85^5-1 > 2^32-1)
    payload = "!" + "!!!!&" + "{{{{{"
    with pytest.raises(MaskDecodeError, match="out-of-range group"):
        decode_one_bit(payload, 2, 2)


def test_truncated_stream_raises(good_one_bit):
    payload, w, h = good_one_bit
    packed = ref._unpack(payload[1:])
    cut = "!" + ref._pack(packed[:6])
    with pytest.raises(MaskDecodeError, match="ended before decoding completed"):
        decode_one_bit(cut, w, h)
    with pytest.raises(MaskDecodeError, match="finalization"):
        decode_one_bit("!" + ref._pack(packed[:3]), w, h)


def test_lossless_truncated_and_bad_predictor():
    with pytest.raises(MaskDecodeError, match="truncated"):
        decode_lossless("~" + ref._pack(bytes([0, 1, 2])), 3, 3)
    with pytest.raises(MaskDecodeError, match="invalid predictor"):
        decode_lossless("~" + ref._pack(bytes([9]) + bytes(32)), 3, 3)
    with pytest.raises(MaskDecodeError, match="ended before decoding completed"):
        decode_lossless("~" + ref._pack(bytes([0]) + b"\xa5\x5a\xff\x13\x77\xc4"), 40, 40)


def test_bad_dimensions(good_one_bit):
    payload, w, h = good_one_bit
    for bw, bh in ((0, 5), (5, 0), (-1, 5)):
        with pytest.raises(MaskDecodeError, match="positive"):
            decode_one_bit(payload, bw, bh)
    with pytest.raises(MaskDecodeError, match="integers"):
        decode_one_bit(payload, 1.5, 3)  # type: ignore[arg-type]
    with pytest.raises(MaskDecodeError, match="too large"):
        decode_one_bit(payload, MAX_PIXELS, 2)
    with pytest.raises(MaskDecodeError, match="string"):
        decode_one_bit(None, 3, 3)  # type: ignore[arg-type]


def test_error_is_value_error():
    with pytest.raises(ValueError):
        decode_one_bit("junk", 2, 2)


# ---- benchmark (cheap; run with -s to see the numbers) ------------------------------------------------
@pytest.mark.bench
def test_benchmark_masks_per_second(lossless_records, onebit_records, capsys):
    rows = []
    for name, recs, fn in (
        ("lossless", lossless_records, decode_lossless),
        ("one_bit", onebit_records, decode_one_bit),
    ):
        for r in recs:  # warm up (JIT compile or cache load)
            fn(r.payload, r.width, r.height)
        reps = 20
        t0 = time.perf_counter()
        for _ in range(reps):
            for r in recs:
                fn(r.payload, r.width, r.height)
        dt = time.perf_counter() - t0
        n = reps * len(recs)
        rows.append((name, n / dt, dt / n * 1e3))
        assert n / dt > 50  # the reference manages ~6/s; anything near it means the JIT path broke
    with capsys.disabled():
        for name, rate, ms in rows:
            print(f"\n[bench] {name}: {rate:.0f} masks/s ({ms:.2f} ms/mask)")
