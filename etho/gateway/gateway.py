"""The only path from Etho to any model provider.

Every call: policy check (effort, tag, data mode) -> cache lookup -> worst-case cost reservation
against the hard caps -> HTTP with retries -> cost from returned usage -> ledger row.
"""

from __future__ import annotations

import base64
import email.utils
import gzip
import inspect
import json
import mimetypes
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import prices
from .cache import DiskCache, file_sha256, request_key
from .config import GatewayConfig
from .errors import CapExceeded, PolicyError, ProviderError
from .ledger import Ledger

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}

# Allowed reasoning-effort values per provider (CLAUDE.md "How to set effort"). `max` exists on
# muse-spark-1.3 but our policy doesn't use it; `none` is rejected by Muse Spark.
EFFORTS = {
    "meta": {"minimal", "low", "medium", "high", "xhigh"},
    "baseten": {"low", "medium", "high", "xhigh"},
    "fireworks": {"low", "medium", "high", "xhigh"},
    "together": {"low", "medium", "high"},
}
GLIMMER_MODELS = {"accounts/fireworks/models/muse-glimmer-30b", "meta-models/Muse-Glimmer-30B"}

# Worst-case input-token guesses for pre-call reservations (refined from measured usage).
EST_TOKENS_PER_IMAGE = 3000
EST_TOKENS_PER_VIDEO = 150_000
DEFAULT_MAX_TOKENS = 4096


@dataclass
class LLMResult:
    text: str
    usage: dict
    cost_usd: float
    cache_hit: bool
    latency_s: float
    reasoning_tokens: int
    raw: dict = field(repr=False, default_factory=dict)


@dataclass
class SamResult:
    text_path: Path  # gzipped raw SAM output text (one line per frame)
    usage: dict
    cost_usd: float
    cache_hit: bool
    latency_s: float
    frames_billed: int

    def text(self) -> str:
        with gzip.open(self.text_path, "rt") as f:
            return f.read()

    def lines(self):
        with gzip.open(self.text_path, "rt") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    yield line


def _caller() -> str:
    for fr in inspect.stack()[2:]:
        mod = fr.frame.f_globals.get("__name__", "")
        if not mod.startswith("etho.gateway"):
            return f"{mod}:{fr.function}"
    return "unknown"


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def image_part(path_or_url: str | Path, api: str = "chat") -> dict:
    """Image content part. api='chat' (Chat Completions) or 'responses' (Responses API)."""
    url = str(path_or_url) if str(path_or_url).startswith(("http://", "https://", "data:")) \
        else _data_url(Path(path_or_url))
    if api == "responses":
        return {"type": "input_image", "image_url": url}
    return {"type": "image_url", "image_url": {"url": url}}


def video_part(path_or_url: str | Path, api: str = "chat", file_id: str | None = None) -> dict:
    if api == "responses":
        if file_id:
            return {"type": "input_video", "file_id": file_id}
        url = str(path_or_url) if str(path_or_url).startswith(("http", "data:")) \
            else _data_url(Path(path_or_url))
        return {"type": "input_video", "video_url": url}
    url = str(path_or_url) if str(path_or_url).startswith(("http", "data:")) \
        else _data_url(Path(path_or_url))
    return {"type": "video_url", "video_url": {"url": url}}


def _count_media(obj: Any) -> tuple[int, int, int]:
    """(text_chars, images, videos) in a messages/input structure, ignoring base64 payloads."""
    chars = images = videos = 0
    if isinstance(obj, dict):
        t = obj.get("type", "")
        if t in ("image_url", "input_image"):
            return 0, 1, 0
        if t in ("video_url", "input_video"):
            return 0, 0, 1
        for v in obj.values():
            c, i, vv = _count_media(v)
            chars, images, videos = chars + c, images + i, videos + vv
    elif isinstance(obj, list):
        for v in obj:
            c, i, vv = _count_media(v)
            chars, images, videos = chars + c, images + i, videos + vv
    elif isinstance(obj, str):
        chars = len(obj)
    return chars, images, videos


def estimate_llm_cost(provider: str, model: str, messages: Any, max_tokens: int) -> float:
    chars, images, videos = _count_media(messages)
    in_tok = chars / 3 + images * EST_TOKENS_PER_IMAGE + videos * EST_TOKENS_PER_VIDEO + 50
    return prices.token_cost(provider, model, int(in_tok), max_tokens)


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    if not v:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(v)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


class Gateway:
    def __init__(self, config: GatewayConfig | None = None, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], datetime] | None = None):
        self.cfg = config or GatewayConfig()
        self.ledger = Ledger(self.cfg.ops_dir)
        self.cache = DiskCache(self.cfg.cache_dir)
        self.http = client or httpx.Client(timeout=httpx.Timeout(self.cfg.timeout_s, connect=30.0))
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

    # ---- policy ---------------------------------------------------------------------------
    def _check_tag(self, tag: str) -> None:
        if tag not in prices.TAGS:
            raise PolicyError(f"tag must be one of {prices.TAGS}, got {tag!r}")
        if tag == "demo-reserve" and self.now() < datetime.fromisoformat(prices.DEMO_RESERVE_OPENS_UTC):
            raise PolicyError("demo-reserve is untouchable until Nov 3 (ET)")

    def _check_model(self, provider: str, model: str, public_data: bool) -> None:
        prices.price_for(provider, model)  # raises for unknown models
        if model.endswith("-contributor") and not public_data:
            raise PolicyError(
                f"{model} lets Meta train on content: only for public datasets. Pass public_data=True "
                "if this input comes from CalMS21/MABe22, otherwise use the standard model.")

    def _key(self, provider: str) -> str:
        key = self.cfg.api_key(provider)
        if not key:
            raise PolicyError(f"no API key for {provider} in {self.cfg.secrets_path}")
        return key

    # ---- HTTP -----------------------------------------------------------------------------
    def _request(self, provider: str, model: str, method: str, path: str, *, stream: bool = False,
                 **kw) -> httpx.Response:
        url = self.cfg.base_urls[provider].rstrip("/") + path
        headers = {"Authorization": f"Bearer {self._key(provider)}", **kw.pop("headers", {})}
        attempt = 0
        while True:
            attempt += 1
            try:
                if stream:
                    req = self.http.build_request(method, url, headers=headers, **kw)
                    resp = self.http.send(req, stream=True)
                else:
                    resp = self.http.request(method, url, headers=headers, **kw)
            except httpx.TransportError as e:
                if attempt > self.cfg.max_retries:
                    raise ProviderError(0, f"transport error: {e}", provider, model) from e
                self.sleep(min(60.0, 2 ** attempt + random.random()))
                continue
            if resp.status_code < 400:
                return resp
            if stream:
                resp.read()
                resp.close()
            if resp.status_code in RETRY_STATUS and attempt <= self.cfg.max_retries:
                wait = _retry_after(resp)
                if wait is None:
                    wait = min(60.0, 2 ** attempt + random.random())
                print(f"gateway: {provider} HTTP {resp.status_code}, retry {attempt} in {wait:.1f}s",
                      file=sys.stderr)
                self.sleep(min(wait, 300.0))
                continue
            raise ProviderError(resp.status_code, resp.text, provider, model)

    # ---- LLM calls ------------------------------------------------------------------------
    def chat(self, provider: str, model: str, messages: list[dict], *, effort: str, tag: str,
             max_tokens: int = DEFAULT_MAX_TOKENS, public_data: bool = False, caller: str | None = None,
             use_cache: bool = True, **extra) -> LLMResult:
        """OpenAI-compatible Chat Completions on meta / baseten / fireworks / together."""
        caller = caller or _caller()
        self._check_tag(tag)
        self._check_model(provider, model, public_data)
        if effort not in EFFORTS[provider]:
            raise PolicyError(f"{provider} effort must be one of {sorted(EFFORTS[provider])}, got {effort!r}")
        if extra.get("n", 1) != 1:
            raise PolicyError("n>1 is not allowed (Baseten returns 400; cache assumes one completion)")
        if "tool_choice" in extra and "tools" not in extra:
            raise PolicyError("tool_choice without tools")
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, **extra}
        if model in GLIMMER_MODELS:
            body["messages"] = [{"role": "system", "content": f"Reasoning strength: {effort}"}] + messages
        else:
            body["reasoning_effort"] = effort
        return self._llm(provider, model, "/chat/completions", body, tag, caller, use_cache,
                         estimate_llm_cost(provider, model, messages, max_tokens), api="chat")

    def responses(self, model: str, input: list[dict] | str, *, effort: str, tag: str,
                  max_output_tokens: int = DEFAULT_MAX_TOKENS, public_data: bool = False,
                  caller: str | None = None, use_cache: bool = True, **extra) -> LLMResult:
        """Meta Responses API (Muse Spark)."""
        provider = "meta"
        caller = caller or _caller()
        self._check_tag(tag)
        self._check_model(provider, model, public_data)
        if effort not in EFFORTS[provider]:
            raise PolicyError(f"meta effort must be one of {sorted(EFFORTS[provider])}, got {effort!r}")
        body = {"model": model, "input": input, "reasoning": {"effort": effort},
                "max_output_tokens": max_output_tokens, **extra}
        return self._llm(provider, model, "/responses", body, tag, caller, use_cache,
                         estimate_llm_cost(provider, model, input, max_output_tokens), api="responses")

    def _llm(self, provider, model, endpoint, body, tag, caller, use_cache, est, api) -> LLMResult:
        key = request_key(provider, endpoint, body)
        base_row = {"provider": provider, "model": model, "endpoint": endpoint, "tag": tag,
                    "caller": caller, "request_key": key, "effort": body.get("reasoning_effort")
                    or body.get("reasoning", {}).get("effort") or "system-prompt"}
        if use_cache and (hit := self.cache.get_json(key)) is not None:
            usage = _norm_usage(hit.get("usage") or {}, api)
            self.ledger.settle(None, {**base_row, "units": usage, "cost_usd": 0.0, "cache_hit": True,
                                      "reasoning_tokens": usage["reasoning_tokens"], "latency_s": 0.0})
            return LLMResult(_extract_text(hit, api), usage, 0.0, True, 0.0, usage["reasoning_tokens"], hit)
        rid = self.ledger.reserve(provider, tag, est)
        t0 = time.monotonic()
        try:
            resp = self._request(provider, model, "POST", endpoint, json=body)
            data = resp.json()
        except Exception as e:
            self.ledger.settle(rid, {**base_row, "units": {}, "cost_usd": 0.0, "cache_hit": False,
                                     "error": str(e)[:300], "latency_s": round(time.monotonic() - t0, 3)})
            raise
        latency = time.monotonic() - t0
        usage = _norm_usage(data.get("usage") or {}, api)
        cost = prices.token_cost(provider, model, usage["input_tokens"], usage["billed_output_tokens"],
                                 usage["cached_tokens"])
        if not data.get("usage"):
            cost = est  # no usage returned: book the worst-case estimate rather than $0
        self.ledger.settle(rid, {**base_row, "units": usage, "cost_usd": round(cost, 6), "cache_hit": False,
                                 "reasoning_tokens": usage["reasoning_tokens"], "latency_s": round(latency, 3),
                                 "est_cost_usd": round(est, 6)})
        if use_cache:
            self.cache.put_json(key, data)
        return LLMResult(_extract_text(data, api), usage, cost, False, latency, usage["reasoning_tokens"], data)

    # ---- Files ----------------------------------------------------------------------------
    def upload_file(self, path: Path, *, tag: str, public_data: bool = False, purpose: str = "user_data",
                    caller: str | None = None, use_cache: bool = True) -> str:
        """Upload to the Meta Files API (free). Cached by content hash, so each file uploads once."""
        caller = caller or _caller()
        self._check_tag(tag)
        path = Path(path)
        digest = file_sha256(path)
        key = request_key("meta", "/files", {"sha256": digest, "purpose": purpose})
        row = {"provider": "meta", "model": "files", "endpoint": "/files", "tag": tag, "caller": caller,
               "request_key": key, "units": {"bytes": path.stat().st_size}, "cost_usd": 0.0}
        if use_cache and (hit := self.cache.get_json(key)) is not None:
            self.ledger.settle(None, {**row, "cache_hit": True})
            return hit["id"]
        with open(path, "rb") as f:
            resp = self._request("meta", "files", "POST", "/files", data={"purpose": purpose},
                                 files={"file": (path.name, f, mimetypes.guess_type(path.name)[0]
                                                 or "application/octet-stream")})
        data = resp.json()
        self.ledger.settle(None, {**row, "cache_hit": False})
        self.cache.put_json(key, data)
        return data["id"]

    # ---- SAM 3.1 --------------------------------------------------------------------------
    def sam(self, phrase: str, *, tag: str, image: str | Path | None = None,
            video: str | Path | None = None, video_file_id: str | None = None, n_frames: int | None = None,
            public_data: bool = False, metadata: dict[str, str] | None = None,
            on_line: Callable[[str], None] | None = None, caller: str | None = None,
            use_cache: bool = True) -> SamResult:
        """SAM 3.1 concept segmentation/tracking via the Responses API (streamed).

        For video, `n_frames` (frame count of the clip) is required: it drives the pre-call cost
        estimate. Output text is streamed to a gzipped cache file; `on_line` sees each frame line.
        """
        caller = caller or _caller()
        self._check_tag(tag)
        if (image is None) == (video is None and video_file_id is None):
            raise PolicyError("pass exactly one of image= or video=/video_file_id=")
        if len(phrase.split()) > 6 or "," in phrase:
            raise PolicyError("SAM takes one short noun phrase per request")
        if image is not None:
            media = image_part(image, api="responses")
            est = prices.sam_image_cost(1)
            kind = "image"
        else:
            if not n_frames:
                raise PolicyError("video SAM calls need n_frames for the cost estimate")
            if video_file_id is None and not str(video).startswith(("http://", "https://")):
                # Local clips go through the Files API (cached by content hash, uploaded once).
                video_file_id = self.upload_file(Path(video), tag=tag, public_data=public_data,
                                                 caller=caller)
            media = video_part(video, api="responses", file_id=video_file_id)
            est = prices.sam_video_cost(n_frames)
            kind = "video"
        body: dict[str, Any] = {"model": prices.SAM_MODEL, "stream": True, "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": phrase}, media]}]}
        if metadata:
            body["metadata"] = metadata
        key = request_key("meta", "/responses", body)
        base_row = {"provider": "meta", "model": prices.SAM_MODEL, "endpoint": "/responses", "tag": tag,
                    "caller": caller, "request_key": key, "sam_kind": kind, "phrase": phrase}
        out_path = self.cache.text_path(key)
        meta_hit = self.cache.get_json(key) if use_cache else None
        if meta_hit is not None and out_path.exists():
            self.ledger.settle(None, {**base_row, "units": meta_hit.get("units", {}), "cost_usd": 0.0,
                                      "cache_hit": True})
            res = SamResult(out_path, meta_hit.get("usage", {}), 0.0, True, 0.0,
                            meta_hit.get("units", {}).get("frames_billed", 0))
            if on_line:
                for line in res.lines():
                    on_line(line)
            return res
        rid = self.ledger.reserve("meta", tag, est)
        t0 = time.monotonic()
        usage: dict = {}
        tmp = out_path.with_suffix(".partial")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = self._request("meta", prices.SAM_MODEL, "POST", "/responses", stream=True, json=body,
                                 headers={"Accept": "text/event-stream"})
            try:
                usage = _consume_sam_stream(resp, tmp, on_line)
            finally:
                resp.close()
        except Exception as e:
            # A stream that failed after starting may still bill; book the estimate to stay safe.
            started = tmp.exists()
            self.ledger.settle(rid, {**base_row, "units": {}, "cost_usd": round(est if started else 0.0, 6),
                                     "cache_hit": False, "error": str(e)[:300],
                                     "latency_s": round(time.monotonic() - t0, 3)})
            tmp.unlink(missing_ok=True)
            raise
        latency = time.monotonic() - t0
        if kind == "video":
            frames = int(usage.get("video_frames_processed") or 0)
            billed_frames = frames if frames else max(n_frames or 0, prices.SAM_MIN_VIDEO_FRAMES)
            cost = prices.sam_video_cost(billed_frames)
        else:
            billed_frames = 0
            cost = prices.sam_image_cost(1)
        units = {"frames_billed": billed_frames, "frames_requested": n_frames,
                 "frames_from_usage": bool(usage.get("video_frames_processed")), "images": int(kind == "image")}
        tmp.replace(out_path)
        self.ledger.settle(rid, {**base_row, "units": units, "cost_usd": round(cost, 6), "cache_hit": False,
                                 "latency_s": round(latency, 3), "est_cost_usd": round(est, 6)})
        self.cache.put_json(key, {"usage": usage, "units": units})
        return SamResult(out_path, usage, cost, False, latency, billed_frames)


def _sse_events(resp: httpx.Response):
    event, data_lines = None, []
    for line in resp.iter_lines():
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event, "\n".join(data_lines)


def _consume_sam_stream(resp: httpx.Response, out_tmp: Path, on_line) -> dict:
    """Write output_text deltas to a gzip file, return usage from response.completed."""
    usage: dict = {}
    buf = ""
    ctype = resp.headers.get("content-type", "")
    with gzip.open(out_tmp, "wt") as out:
        def emit(text: str) -> None:
            nonlocal buf
            out.write(text)
            buf += text
            *done, buf = buf.split("\n")
            if on_line:
                for ln in done:
                    if ln:
                        on_line(ln)

        if "text/event-stream" not in ctype:
            data = json.loads(resp.read())
            emit(_extract_text(data, "responses"))
            usage = data.get("usage") or {}
        else:
            for _event, data in _sse_events(resp):
                if data == "[DONE]":
                    break
                ev = json.loads(data)
                t = ev.get("type", _event)
                if t == "response.output_text.delta":
                    emit(ev.get("delta", ""))
                elif t == "response.completed":
                    usage = (ev.get("response") or {}).get("usage") or {}
                elif t in ("response.failed", "response.error", "error", "response.incomplete"):
                    raise ProviderError(200, json.dumps(ev)[:1000], "meta", prices.SAM_MODEL)
        if buf:
            out.write("\n")
            if on_line:
                on_line(buf)
    return usage


def _norm_usage(u: dict, api: str) -> dict:
    if api == "responses":
        inp = int(u.get("input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        rt = int((u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        cached = int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    else:
        inp = int(u.get("prompt_tokens") or 0)
        out = int(u.get("completion_tokens") or 0)
        rt = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens")
                 or u.get("reasoning_tokens") or 0)
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    # OpenAI convention: output/completion tokens include reasoning. If a provider reports
    # reasoning larger than output, it is clearly reported separately: bill both.
    billed_out = out + rt if rt > out else out
    return {"input_tokens": inp, "output_tokens": out, "reasoning_tokens": rt, "cached_tokens": cached,
            "billed_output_tokens": billed_out}


def _extract_text(data: dict, api: str) -> str:
    if api == "chat":
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return ""
        content = msg.get("content")
        if isinstance(content, list):
            return "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content or ""
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    parts.append(c.get("text", ""))
    return "".join(parts)


__all__ = ["Gateway", "LLMResult", "SamResult", "image_part", "video_part", "CapExceeded"]
