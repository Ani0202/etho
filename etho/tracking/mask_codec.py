# Copyright (c) Meta Platforms, Inc. and affiliates. All Rights Reserved.
#
# Derived from meta-sam-parser (SAM License): the pure-Python reference decoder in
# ``meta_sam_parser._mask_codec`` was ported to numba for speed. The bitstream format, context
# model, count updates and predictors are unchanged; only the implementation differs. Redistribution
# is under the terms of the SAM License shipped in the meta-sam-parser package metadata.
"""Fast decoder for SAM 3.1 API mask payloads (``one_bit`` and ``lossless``).

A mask token carries ``data=H,W,<enc><payload>`` where ``<enc>`` is ``!`` (one_bit) or ``~``
(lossless). The payload is base85 (custom alphabet, 4 bytes -> 5 chars, 5-char length prefix),
then range-coded with a context model. This module is bit-exact with
``meta_sam_parser.decode_mask_to_raster`` (lossless output thresholded at >= 129), but about two
orders of magnitude faster, because the per-pixel loops run as compiled numba code.

Differences from the reference, by design:
  * No canonical re-encode check (the reference re-encodes every one_bit mask, doubling the cost)
    and no canonical-form repack check of the base85 text. Structural checks are kept: length
    prefix vs actual length, base85 digits, group range, truncated range-coder stream, invalid
    predictor selector, and a pixel-count sanity cap.
  * The ``payload`` argument INCLUDES the leading ``!`` / ``~`` encoding marker, exactly as it
    appears after ``H,W,`` in the token (the marker cannot be dropped: ``!`` is also a valid base85
    digit, so the two cannot be told apart).

The jitted kernels release the GIL (``nogil=True``), so they also scale across threads.
"""

from __future__ import annotations

import numpy as np
from numba import njit

__all__ = ["MaskDecodeError", "decode_one_bit", "decode_lossless", "decode_mask", "MAX_PIXELS"]

# Refuse absurd dimensions before allocating (4K video is ~8.3 M px; this is 64 Mpx).
MAX_PIXELS = 1 << 26


class MaskDecodeError(ValueError):
    """Raised for a malformed or unsupported SAM mask payload."""


# ---- constants (identical to the reference) -------------------------------------------------------
_EXCLUDED = frozenset({'"', "\\", ",", ";", "<", ">", "|"})
_ALPHABET = "".join(c for code in range(0x21, 0x7F) if (c := chr(code)) not in _EXCLUDED)
_RADIX = 85
_LUT = np.full(256, -1, dtype=np.int64)
for _i, _c in enumerate(_ALPHABET):
    if _i < _RADIX:  # the reference rejects digit values >= 85
        _LUT[ord(_c)] = _i

_PREFIX = 5
_M32 = 0xFFFFFFFF
_TOP = 1 << 24
_BOTTOM = 1 << 16
_SYMBOLS = 256
_TOTAL_IDX = 256
_SLOT = 257

# status codes -> messages
_OK = 0
_E_SHORT, _E_CHAR, _E_LEN, _E_GROUP, _E_TAIL = 1, 2, 3, 4, 5
_E_FINAL, _E_ENDED, _E_STATE, _E_PREDICTOR, _E_MALFORMED, _E_TRUNC = 6, 7, 8, 9, 10, 11
_MESSAGES = {
    _E_SHORT: "Mask payload is missing its length prefix.",
    _E_CHAR: "Mask payload contains an invalid character.",
    _E_LEN: "Mask payload length does not match its prefix.",
    _E_GROUP: "Mask payload contains an out-of-range group.",
    _E_TAIL: "Mask payload contains an out-of-range tail.",
    _E_FINAL: "Mask payload is missing its finalization.",
    _E_ENDED: "Mask payload ended before decoding completed.",
    _E_STATE: "Mask payload has invalid coding state.",
    _E_PREDICTOR: "Lossless mask payload uses an invalid predictor.",
    _E_MALFORMED: "Lossless mask payload is malformed.",
    _E_TRUNC: "Lossless mask payload is truncated.",
}


# ---- numba kernels ---------------------------------------------------------------------------------
@njit(cache=True, nogil=True)
def _unpack(buf, start, lut):
    """base85 -> bytes. Returns (status, bytes). ``buf`` is uint8 ASCII, ``start`` skips the marker."""
    n = buf.shape[0] - start
    empty = np.zeros(0, dtype=np.uint8)
    if n < _PREFIX:
        return _E_SHORT, empty
    length = 0
    for i in range(_PREFIX):
        d = lut[buf[start + i]]
        if d < 0:
            return _E_CHAR, empty
        length = length * 85 + d
    rem = length % 4
    expected = _PREFIX + (length // 4) * 5 + (0 if rem == 0 else rem + 1)
    if n != expected:
        return _E_LEN, empty
    out = np.zeros(length, dtype=np.uint8)
    src = start + _PREFIX
    dst = 0
    full = length - rem
    while dst < full:
        v = 0
        for i in range(5):
            d = lut[buf[src + i]]
            if d < 0:
                return _E_CHAR, empty
            v = v * 85 + d
        if v > 0xFFFFFFFF:
            return _E_GROUP, empty
        src += 5
        out[dst] = (v >> 24) & 0xFF
        out[dst + 1] = (v >> 16) & 0xFF
        out[dst + 2] = (v >> 8) & 0xFF
        out[dst + 3] = v & 0xFF
        dst += 4
    if rem > 0:
        v = 0
        for i in range(5):
            if i < rem + 1:
                d = lut[buf[src + i]]
                if d < 0:
                    return _E_CHAR, empty
            else:
                d = 84
            v = v * 85 + d
        if v > 0xFFFFFFFF:
            return _E_TAIL, empty
        for i in range(rem):
            out[dst + i] = (v >> (24 - i * 8)) & 0xFF
    return _OK, out


@njit(cache=True, nogil=True)
def _renorm(low, rng, code, pos, buf, n):
    """Range-coder normalisation. Returns (low, range, code, pos); pos == -1 means stream exhausted."""
    while True:
        if (low ^ ((low + rng) & 0xFFFFFFFF)) < 16777216:
            pass
        elif rng < 65536:
            rng = (-low) & 65535
        else:
            break
        if pos >= n:
            return low, rng, code, -1
        code = ((code << 8) | buf[pos]) & 0xFFFFFFFF
        pos += 1
        low = (low << 8) & 0xFFFFFFFF
        rng = (rng << 8) & 0xFFFFFFFF
    return low, rng, code, pos


@njit(cache=True, nogil=True)
def _one_bit_kernel(buf, start, width, height, out):
    n = buf.shape[0]
    if n - start < 4:
        return _E_FINAL
    pw = width + 4
    pad = np.zeros(pw * (height + 2), dtype=np.uint8)
    counts = np.ones(4096 * 2, dtype=np.int64)
    low = 0
    rng = 0xFFFFFFFF
    code = 0
    pos = start
    for _ in range(4):
        code = (code << 8) | buf[pos]
        pos += 1
    for y in range(height):
        row = (y + 2) * pw + 2
        for x in range(width):
            p = row + x
            q = p - pw
            e = q - pw
            ctx = (
                pad[p - 1]
                | (pad[p - 2] << 1)
                | (pad[q - 2] << 2)
                | (pad[q - 1] << 3)
                | (pad[q] << 4)
                | (pad[q + 1] << 5)
                | (pad[q + 2] << 6)
                | (pad[e - 2] << 7)
                | (pad[e - 1] << 8)
                | (pad[e] << 9)
                | (pad[e + 1] << 10)
                | (pad[e + 2] << 11)
            )
            off = ctx << 1
            zero = counts[off]
            one = counts[off + 1]
            total = zero + one
            scaled = rng // total
            if scaled == 0:
                return _E_STATE
            v = ((code - low) & 0xFFFFFFFF) // scaled
            if v >= total:
                v = total - 1
            if v < zero:
                bit = 0
                cum = 0
                freq = zero
            else:
                bit = 1
                cum = zero
                freq = one
            low = (low + scaled * cum) & 0xFFFFFFFF
            rng = scaled * freq
            low, rng, code, pos = _renorm(low, rng, code, pos, buf, n)
            if pos < 0:
                return _E_ENDED
            if bit:
                pad[p] = 1
                out[y, x] = 1
            counts[off + bit] += 14
            if counts[off] + counts[off + 1] >= 4096:
                counts[off] = (counts[off] >> 1) or 1
                counts[off + 1] = (counts[off + 1] >> 1) or 1
    return _OK


@njit(cache=True, nogil=True)
def _lossless_kernel(buf, width, height, out):
    n = buf.shape[0]
    if n < 5:
        return _E_TRUNC
    mode = buf[0]
    if mode >= 5:
        return _E_PREDICTOR
    pw = width + 2
    op = np.zeros(pw * (height + 1), dtype=np.uint8)  # output, 1-px zero border (top/left/right)
    rp = np.zeros(pw * (height + 1), dtype=np.uint8)  # residuals, same layout
    zc = np.ones(32, dtype=np.int64)
    o1 = np.ones(65536, dtype=np.int64)
    o1t = np.full(256, 256, dtype=np.int64)
    o0 = np.full(256, 2, dtype=np.int64)
    o0t = 512
    slot_of = np.full(65536, -1, dtype=np.int32)  # (prev << 8 | above) -> order-2 slot
    cap = 64
    pool = np.zeros(cap * 257, dtype=np.uint16)
    nslots = 0
    low = 0
    rng = 0xFFFFFFFF
    code = 0
    pos = 1
    for _ in range(4):
        code = (code << 8) | buf[pos]
        pos += 1
    prev = 0
    for y in range(height):
        for x in range(width):
            p = (y + 1) * pw + 1 + x
            u = p - pw
            # ---- zero / non-zero flag ----
            zctx = 0
            if rp[p - 1] == 0:
                zctx |= 1
            if rp[u] == 0:
                zctx |= 2
            if rp[u - 1] == 0:
                zctx |= 4
            if rp[u + 1] == 0:
                zctx |= 8
            zoff = zctx << 1
            zero = zc[zoff]
            nonzero = zc[zoff + 1]
            total = zero + nonzero
            scaled = rng // total
            if scaled == 0:
                return _E_STATE
            v = ((code - low) & 0xFFFFFFFF) // scaled
            if v >= total:
                v = total - 1
            if v < zero:
                bit = 0
                cum = 0
                freq = zero
            else:
                bit = 1
                cum = zero
                freq = nonzero
            low = (low + scaled * cum) & 0xFFFFFFFF
            rng = scaled * freq
            low, rng, code, pos = _renorm(low, rng, code, pos, buf, n)
            if pos < 0:
                return _E_ENDED
            zc[zoff + bit] += 32
            if zc[zoff] + zc[zoff + 1] >= 16384:
                zc[zoff] = (zc[zoff] >> 1) or 1
                zc[zoff + 1] = (zc[zoff + 1] >> 1) or 1

            symbol = 0
            if bit == 1:
                key = (prev << 8) | rp[u]
                slot = slot_of[key]
                if slot < 0:
                    slot = nslots
                    nslots += 1
                    slot_of[key] = slot
                    if nslots > cap:
                        cap *= 2
                        bigger = np.zeros(cap * 257, dtype=np.uint16)
                        bigger[: pool.shape[0]] = pool
                        pool = bigger
                b = slot * 257
                fo = prev << 8
                excl = np.int64(pool[b]) + o1[fo] + o0[0]
                total = np.int64(pool[b + 256]) + o1t[prev] + o0t - excl
                if total <= 0:
                    return _E_STATE
                scaled = rng // total
                if scaled == 0:
                    return _E_STATE
                target = ((code - low) & 0xFFFFFFFF) // scaled
                if target >= total:
                    target = total - 1
                cumulative = 0
                symbol = 1
                freq = np.int64(pool[b + 1]) + o1[fo + 1] + o0[1]
                while cumulative + freq <= target:
                    cumulative += freq
                    symbol += 1
                    if symbol >= 256:
                        return _E_MALFORMED
                    freq = np.int64(pool[b + symbol]) + o1[fo + symbol] + o0[symbol]
                low = (low + scaled * cumulative) & 0xFFFFFFFF
                rng = scaled * freq
                low, rng, code, pos = _renorm(low, rng, code, pos, buf, n)
                if pos < 0:
                    return _E_ENDED
                # order-2 update (u16 semantics via the uint16 pool)
                pool[b + symbol] = (np.int64(pool[b + symbol]) + 616) & 0xFFFF
                pool[b + 256] = (np.int64(pool[b + 256]) + 616) & 0xFFFF
                if pool[b + 256] >= 50176:
                    s = 0
                    for k in range(256):
                        h = np.int64(pool[b + k]) >> 1
                        pool[b + k] = h
                        s += h
                    pool[b + 256] = s & 0xFFFF
                # order-1 update
                o1[fo + symbol] += 56
                o1t[prev] += 56
                if o1t[prev] >= 8192:
                    s = 0
                    for k in range(256):
                        h = (o1[fo + k] >> 1) or 1
                        o1[fo + k] = h
                        s += h
                    o1t[prev] = s
                # order-0 update
                o0[symbol] += 32
                o0t += 32
                if o0t >= 4096:
                    s = 0
                    for k in range(256):
                        h = o0[k] >> 1
                        o0[k] = h
                        s += h
                    o0t = s
            rp[p] = symbol
            # ---- predictor + un-zigzag ----
            left = np.int64(op[p - 1])
            up = np.int64(op[u])
            if mode == 1:
                pred = up
            elif mode == 2:
                pred = left
            elif mode == 3:
                pred = (left + up) >> 1
            else:
                ul = np.int64(op[u - 1])
                if mode == 0:  # Paeth
                    est = left + up - ul
                    dl = abs(est - left)
                    du = abs(est - up)
                    dul = abs(est - ul)
                    if dl <= du and dl <= dul:
                        pred = left
                    elif du <= dul:
                        pred = up
                    else:
                        pred = ul
                else:  # mode 4: median edge detector
                    mx = left if left > up else up
                    mn = left if left < up else up
                    if ul >= mx:
                        pred = mn
                    elif ul <= mn:
                        pred = mx
                    else:
                        pred = left + up - ul
            if symbol & 1:
                delta = -((symbol + 1) >> 1)
            else:
                delta = symbol >> 1
            op[p] = (pred + delta) & 0xFF
            prev = symbol
    for y in range(height):
        base = (y + 1) * pw + 1
        for x in range(width):
            if op[base + x] >= 129:
                out[y, x] = 1
    return _OK


# ---- python wrappers -------------------------------------------------------------------------------
def _prepare(payload, width, height, marker: str) -> np.ndarray:
    if isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, (int, np.integer)) \
            or not isinstance(height, (int, np.integer)):
        raise MaskDecodeError("Mask dimensions must be integers.")
    if width <= 0 or height <= 0:
        raise MaskDecodeError("Mask dimensions must be positive.")
    if width * height > MAX_PIXELS:
        raise MaskDecodeError(f"Mask is too large ({width}x{height} > {MAX_PIXELS} px).")
    if not isinstance(payload, str):
        raise MaskDecodeError("Mask payload must be a string.")
    if not payload.startswith(marker):
        kind = "one_bit" if marker == "!" else "lossless"
        raise MaskDecodeError(f"{kind} mask payloads must start with {marker}.")
    try:
        raw = payload.encode("ascii")
    except UnicodeEncodeError as e:
        raise MaskDecodeError("Mask payload contains an invalid character.") from e
    return np.frombuffer(raw, dtype=np.uint8)


def _check(status: int) -> None:
    if status != _OK:
        raise MaskDecodeError(_MESSAGES.get(status, f"Mask payload could not be decoded ({status})."))


def decode_one_bit(payload: str, width: int, height: int) -> np.ndarray:
    """Decode a ``one_bit`` mask (payload starts with ``!``) to a (height, width) uint8 0/1 array."""
    buf = _prepare(payload, width, height, "!")
    status, packed = _unpack(buf, 1, _LUT)
    _check(status)
    out = np.zeros((height, width), dtype=np.uint8)
    _check(_one_bit_kernel(packed, 0, width, height, out))
    return out


def decode_lossless(payload: str, width: int, height: int) -> np.ndarray:
    """Decode a ``lossless`` mask (payload starts with ``~``), thresholded at >= 129 like the reference.

    Returns a (height, width) uint8 0/1 array.
    """
    buf = _prepare(payload, width, height, "~")
    status, packed = _unpack(buf, 1, _LUT)
    _check(status)
    out = np.zeros((height, width), dtype=np.uint8)
    _check(_lossless_kernel(packed, width, height, out))
    return out


def decode_mask(payload: str, width: int, height: int) -> np.ndarray:
    """Dispatch on the marker character: ``!`` one_bit, ``~`` lossless."""
    if payload.startswith("~"):
        return decode_lossless(payload, width, height)
    return decode_one_bit(payload, width, height)
