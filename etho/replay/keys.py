"""Semantic request keys.

An exact request hash (what the live gateway's disk cache uses) covers the bytes of every image and video in the
request. Those bytes come out of ffmpeg and OpenCV, which differ between machines, so an exact hash recorded here
would miss on a judge's Mac. A semantic key instead covers what a request MEANS:

    method + provider + model + caller module + the request with every image / video payload replaced by a
    placeholder (the prompt text, the NUMBER of images, the SAM phrase and the other parameters are kept)

plus an occurrence counter for requests whose keys are identical (the planner asks SAM for the same phrase on two
different frames: the first call replays the first recording, the second call the second). Tier-1 prompts carry the
window being judged ("t=12.3s to t=14.1s"), so a window that drifts changes the key and misses.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

IMAGE = "<image>"
VIDEO = "<video>"
PATH = "<path>"

# Arguments that say who is calling or how, not what is asked. `tag` is bookkeeping (the app forces its own);
# `use_cache` and `on_line` do not change the request.
IGNORED = frozenset({"tag", "caller", "use_cache", "on_line"})
IMAGE_TYPES = ("image_url", "input_image")
VIDEO_TYPES = ("video_url", "input_video")


def caller_module(caller: str | None) -> str:
    """`job:<id>|etho.rules.synthesize:attack` -> `etho.rules.synthesize` (job prefix and function name dropped)."""
    body = (caller or "unknown").split("|", 1)[-1]
    return body.split(":", 1)[0]


# The one part of any prompt that changes on every run: the report's methods prompt lists the stage wall-clock
# timings of the run that wrote it. A replay is faster than the live run, so exact timings can never match; they are
# masked in the key. (Costs stay in the key: a replay returns each call's recorded cost, so they do match.)
_TIMINGS = re.compile(r'("timings_s":\s*)\{[^{}]*\}')


def mask_volatile(text: str) -> str:
    return _TIMINGS.sub(r'\1"<timings>"', text)


def scrub(obj: Any) -> Any:
    """The request with media payloads replaced by placeholders. Text, structure and order are kept."""
    if isinstance(obj, dict):
        t = obj.get("type")
        if t in IMAGE_TYPES:
            return {"type": t, "media": IMAGE}
        if t in VIDEO_TYPES:
            return {"type": t, "media": VIDEO}
        return {str(k): scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    if isinstance(obj, Path):
        return PATH
    if isinstance(obj, str) and obj.startswith("data:"):
        return "<data-url>"
    if isinstance(obj, str):
        return mask_volatile(obj)
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return f"<{type(obj).__name__}>"


def count_media(obj: Any) -> tuple[int, int]:
    """(images, videos) in a messages / input structure."""
    if isinstance(obj, dict):
        t = obj.get("type")
        if t in IMAGE_TYPES:
            return 1, 0
        if t in VIDEO_TYPES:
            return 0, 1
        parts = [count_media(v) for v in obj.values()]
    elif isinstance(obj, (list, tuple)):
        parts = [count_media(v) for v in obj]
    else:
        return 0, 0
    return sum(p[0] for p in parts), sum(p[1] for p in parts)


def _extras(kw: dict) -> dict:
    return {k: scrub(v) for k, v in sorted(kw.items()) if k not in IGNORED}


def describe(method: str, provider: str, model: str, caller: str | None, **fields) -> dict:
    """The scrubbed, canonical description of one call. Its hash is the key."""
    return {"method": method, "provider": provider, "model": model, "caller": caller_module(caller),
            "request": dict(sorted(fields.items()))}


def key_of(desc: dict) -> str:
    blob = json.dumps(desc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def chat_desc(provider: str, model: str, messages: list[dict], effort: str, caller: str | None, kw: dict) -> dict:
    n_img, n_vid = count_media(messages)
    return describe("chat", provider, model, caller, messages=scrub(messages), effort=effort, n_images=n_img,
                    n_videos=n_vid, **_extras(kw))


def responses_desc(model: str, input_: Any, effort: str, caller: str | None, kw: dict) -> dict:
    n_img, n_vid = count_media(input_)
    return describe("responses", "meta", model, caller, input=scrub(input_), effort=effort, n_images=n_img,
                    n_videos=n_vid, **_extras(kw))


def sam_desc(phrase: str, caller: str | None, kw: dict) -> dict:
    """A SAM call. Image and video are placeholders; the Files API id (`video_file_id`) is not part of the meaning
    (it differs per upload), while `n_frames` and the metadata are."""
    media = IMAGE if kw.get("image") is not None else VIDEO
    rest = {k: v for k, v in kw.items() if k not in ("image", "video", "video_file_id")}
    return describe("sam", "meta", "sam-3.1", caller, phrase=phrase, media=media, **_extras(rest))


def upload_desc(caller: str | None, purpose: str, duration_s: float, n_frames: int) -> dict:
    """An upload is keyed on its purpose and the clip's duration and frame count, never on its bytes."""
    return describe("upload_file", "meta", "files", caller, purpose=purpose, duration_s=round(float(duration_s), 2),
                    n_frames=int(n_frames))


def texts(desc: dict) -> list[str]:
    """Every text field of the request, for showing what differs from the nearest recording."""
    out: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str):
                    if k in ("text", "content") and v not in (IMAGE, VIDEO):
                        out.append(v)           # not `role`, `type`, ...
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.append(o)

    req = desc["request"]
    walk(req.get("messages") if "messages" in req else req.get("input"))
    return out


def summary(desc: dict) -> str:
    """One line for error messages."""
    r = desc["request"]
    head = f"{desc['method']} {desc['provider']}/{desc['model']} caller={desc['caller']}"
    if desc["method"] == "sam":
        return f"{head} phrase={r.get('phrase')!r} media={r.get('media')} n_frames={r.get('n_frames')}"
    if desc["method"] == "upload_file":
        return f"{head} purpose={r.get('purpose')} duration_s={r.get('duration_s')} n_frames={r.get('n_frames')}"
    last = (texts(desc) or [""])[-1]       # the user prompt comes last (after any system prompt)
    return f"{head} effort={r.get('effort')} images={r.get('n_images')} text={last[:110]!r}"
