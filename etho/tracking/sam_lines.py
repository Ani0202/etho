# Copyright (c) Meta Platforms, Inc. and affiliates. All Rights Reserved.
#
# Derived from meta-sam-parser (SAM License): the record grammar and validation rules below follow
# ``meta_sam_parser._segmentation`` (same regexes, same acceptance rules), minus the streaming
# machinery and, crucially, minus mask decoding.
"""Fast, decode-free parser for SAM 3.1 API output lines.

One line looks like::

    <12f>1<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;x=0;y=0;data=H,W,<enc><payload>;c=..|>

(an optional ``,`` and further ``<id><|box..|><|mask..|>`` groups may follow for more objects).

`parse_sam_line` returns one `SamRecord` per accepted object with the mask payload still encoded;
decoding is the caller's decision (see ``etho.tracking.mask_codec``). The reference parser decodes
every mask once just to validate it; skipping that is the point of this module. A payload that is
syntactically fine but corrupt is therefore only detected at decode time.

Acceptance rules mirror the reference: a line that is not a frame header is ignored (reference: a
text record); within a header line, records are read in order and the first malformed record ends
the line (earlier records on that line are kept, a diagnostic is appended).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

__all__ = ["SamRecord", "SamDiagnostic", "parse_sam_line", "iter_sam_records"]

_MAXIMUM_SAFE_INTEGER = (1 << 53) - 1
_JS_WHITESPACE = (
    "\t\n\v\f\r          "
    "        　﻿"
)
_JS_DOT_PATTERN = r"[^\n\r  ]"

_API_HEADER = re.compile(rf"^<([0-9]+)f(?:;([^>]*))?>({_JS_DOT_PATTERN}*)$")
_RECORD_START = re.compile(r",?([0-9]+)(?=<\|)")
_TOKEN = re.compile(r"<\|([A-Za-z][A-Za-z0-9_.-]*)(?:;([^|]*))?\|>")
_RECORD_BOUNDARY = frozenset(",0123456789")
_SIGNED_INT = re.compile(r"-?[0-9]+")
_UNSIGNED_INT = re.compile(r"[0-9]+")
_MASK_DATA = re.compile(r"([0-9]+),([0-9]+),([!~][^|]+)")
_CONFIDENCE = re.compile(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_BOX_KEYS = frozenset({"x1", "y1", "x2", "y2", "w", "h", "c"})
_MASK_KEYS = frozenset({"x", "y", "data", "c"})


@dataclass(frozen=True, slots=True)
class SamRecord:
    """One accepted object on one frame. The mask is still encoded in ``payload``."""

    frame: int
    object_id: str
    left: int  # box, half-open: [left, right) x [top, bottom), source pixels
    top: int
    right: int
    bottom: int
    confidence: float | None  # the mask token's ``c`` (what the reference exposes as record confidence)
    box_confidence: float | None
    encoding: str  # "one_bit" | "lossless"
    height: int  # mask raster size (H, W); the raster covers exactly the box
    width: int
    payload: str  # includes the leading "!" / "~" marker, ready for mask_codec.decode_*
    source_width: int  # full-frame size from the box token (w, h)
    source_height: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(frozen=True, slots=True)
class SamDiagnostic:
    code: str  # malformed_record | invalid_box | invalid_frame | unexpected_frame | invalid_mask_size
    message: str


def _trim(value: str) -> str:
    return value.strip(_JS_WHITESPACE)


def _int(value: str) -> int | None:
    parsed = int(value)
    return parsed if abs(parsed) <= _MAXIMUM_SAFE_INTEGER else None


def _read_fields(body: str | None, known: frozenset[str]) -> tuple[dict[str, str | None], bool] | None:
    """``;``-separated fields -> (values, repeated_confidence); None if a known key repeats (except c)."""
    values: dict[str, str | None] = {}
    repeated_c = False
    for part in [] if body is None else body.split(";"):
        field = _trim(part)
        if not field:
            continue
        key_text, sep, value_text = field.partition("=")
        key = _trim(key_text)
        value = _trim(value_text) if sep else None
        if key not in known:
            continue  # reference: ignored with a warning
        if key in values:
            if key != "c":
                return None
            repeated_c = True
            continue
        values[key] = value
    return values, repeated_c


def _confidence(fields: tuple[dict[str, str | None], bool]) -> float | None:
    values, repeated = fields
    if "c" not in values:
        return None
    text = values["c"]
    value = (
        float(text)
        if not repeated and text is not None and _CONFIDENCE.fullmatch(text) is not None
        else math.nan
    )
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return value + 0.0


def parse_sam_line(
    line: str,
    media: str = "video",
    diagnostics: list[SamDiagnostic] | None = None,
) -> list[SamRecord]:
    """Parse one SAM output line into records without decoding any mask.

    ``media`` is "video" (frame index from the ``<Nf>`` header) or "image" (frame must be 0, index 0).
    Problems are appended to ``diagnostics`` if given; they never raise.
    """

    def diag(code: str, message: str) -> None:
        if diagnostics is not None:
            diagnostics.append(SamDiagnostic(code, message))

    text = _trim(line)
    if not text or not text.startswith("<"):
        return []
    header = _API_HEADER.fullmatch(text)
    if header is None:
        return []
    frame = _int(header.group(1))
    if frame is None or frame < 0:
        diag("invalid_frame", "Frame references must be non-negative safe integers.")
        return []
    if media == "image":
        if frame != 0:
            diag("unexpected_frame", "Image segmentation records require frame zero.")
            return []
    body = header.group(3)
    if not body:
        diag("malformed_record", "Malformed SAM API object record.")
        return []

    out: list[SamRecord] = []
    position = 0
    n = len(body)
    while position < n:
        start = _RECORD_START.match(body, position)
        tokens: list[tuple[str, str | None]] = []
        end = position if start is None else start.end()
        token = None if start is None else _TOKEN.match(body, end)
        while token is not None:
            tokens.append((token.group(1), token.group(2)))
            end = token.end()
            token = _TOKEN.match(body, end)
        rec = None
        if start is not None and not (end < n and body[end] not in _RECORD_BOUNDARY):
            rec = _read_record(start.group(1), tokens, frame)
        if rec is None:
            diag("malformed_record", "Malformed SAM API object record.")
            return out
        if isinstance(rec, str):  # an error code from _read_record
            diag(rec, _MESSAGES[rec])
            if rec == "invalid_mask_size":  # the reference drops only this mask and keeps parsing
                position = end
                continue
            return out
        out.append(rec)
        position = end
    return out


_MESSAGES = {
    "invalid_box": "SAM API box coordinates are invalid.",
    "invalid_mask_size": "Mask dimensions must be positive.",
}


def _read_record(object_id: str, tokens: list[tuple[str, str | None]], frame: int) -> SamRecord | str | None:
    boxes = [body for name, body in tokens if name == "box"]
    masks = [body for name, body in tokens if name == "mask"]
    if len(boxes) != 1 or len(masks) != 1:
        return None
    box = _read_fields(boxes[0], _BOX_KEYS)
    mask = _read_fields(masks[0], _MASK_KEYS)
    if box is None or mask is None:
        return None
    bv, mv = box[0], mask[0]
    coords = [bv.get(k) for k in ("x1", "y1", "x2", "y2")]
    dims = [bv.get(k) for k in ("w", "h")]
    if (
        any(v is None or _SIGNED_INT.fullmatch(v) is None for v in coords)
        or any(v is None or _UNSIGNED_INT.fullmatch(v) is None for v in dims)
        or mv.get("x") != "0"
        or mv.get("y") != "0"
    ):
        return None
    data = _MASK_DATA.fullmatch(mv.get("data") or "")
    if data is None:
        return None
    vals = [_int(v) for v in coords + dims]  # type: ignore[arg-type]
    if any(v is None for v in vals):
        return "invalid_box"
    left, top, inc_right, inc_bottom, src_w, src_h = vals  # type: ignore[misc]
    if (
        src_w <= 0 or src_h <= 0 or left < 0 or top < 0
        or inc_right < left or inc_bottom < top or inc_right >= src_w or inc_bottom >= src_h
    ):
        return "invalid_box"
    mask_h, mask_w = int(data.group(1)), int(data.group(2))
    if mask_w <= 0 or mask_h <= 0 or mask_w * mask_h > _MAXIMUM_SAFE_INTEGER:
        return "invalid_mask_size"
    payload = data.group(3)
    return SamRecord(
        frame=frame,
        object_id=object_id,
        left=left,
        top=top,
        right=inc_right + 1,
        bottom=inc_bottom + 1,
        confidence=_confidence(mask),
        box_confidence=_confidence(box),
        encoding="lossless" if payload.startswith("~") else "one_bit",
        height=mask_h,
        width=mask_w,
        payload=payload,
        source_width=src_w,
        source_height=src_h,
    )


def iter_sam_records(
    lines: Iterable[str], media: str = "video", diagnostics: list[SamDiagnostic] | None = None
) -> Iterator[SamRecord]:
    for line in lines:
        yield from parse_sam_line(line, media, diagnostics)
