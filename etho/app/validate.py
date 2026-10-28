"""Upload validation: type, size, and ffprobe-checked duration. Nothing is spent before this passes."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ALLOWED_EXT, AppConfig


class ValidationError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


@dataclass
class VideoInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    codec: str
    size_bytes: int
    frames: int = 0

    def as_dict(self) -> dict:
        return vars(self)


def check_extension(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValidationError(f"unsupported file type {ext or '(none)'}: use {', '.join(ALLOWED_EXT)}", 415,
                              "bad_type")
    return ext


def _fraction(s: str) -> float:
    try:
        a, _, b = s.partition("/")
        return float(a) / float(b) if b else float(a)
    except (ValueError, ZeroDivisionError):
        return 0.0


# Demuxer forced by file extension (so a playlist or concat script in a ".mp4" is parsed as MP4 and fails),
# and the container names ffprobe may report back. Only the local file protocol is allowed.
DEMUXER = {".mp4": "mov,mp4,m4a,3gp,3g2,mj2", ".mov": "mov,mp4,m4a,3gp,3g2,mj2", ".avi": "avi"}
FORMATS = {".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}, ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
           ".avi": {"avi"}}
MAX_SRC_FPS = 125.0
_HARDEN = ["-protocol_whitelist", "file"]


def probe(path: Path) -> VideoInfo:
    """ffprobe the file (forced demuxer, file protocol only); raises ValidationError if it is not a decodable
    video. Duration is the largest of the container duration, the stream duration and decoded-frame-count / fps,
    and packets are actually counted, so a container that lies about its length cannot slip under the limit."""
    ext = path.suffix.lower()
    if ext not in DEMUXER:
        raise ValidationError(f"unsupported file type {ext or '(none)'}", 415, "bad_type")
    try:
        out = subprocess.run(["ffprobe", "-v", "error", *_HARDEN, "-f", DEMUXER[ext], "-count_packets",
                              "-print_format", "json", "-show_streams", "-show_format", str(path)],
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise ValidationError(f"could not inspect the video ({type(e).__name__})", 400, "probe_failed") from e
    if out.returncode != 0:
        raise ValidationError("file is not a readable video", 400, "unreadable")
    try:
        info = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise ValidationError("file is not a readable video", 400, "unreadable") from e
    names = set(str(info.get("format", {}).get("format_name", "")).split(","))
    if not names & FORMATS[ext]:
        raise ValidationError(f"container {sorted(names)} does not match a {ext} file", 400, "bad_container")
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vs:
        raise ValidationError("no video stream found", 400, "no_video")
    v = vs[0]
    fps = _fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0")
    try:
        frames = int(v.get("nb_read_packets") or 0)
    except (TypeError, ValueError):
        frames = 0
    cands = []
    for src in (info.get("format", {}).get("duration"), v.get("duration")):
        try:
            if src is not None and float(src) > 0:
                cands.append(float(src))
        except (TypeError, ValueError):
            continue
    if fps > MAX_SRC_FPS:
        raise ValidationError(f"frame rate {fps:.0f} fps is not supported", 400, "bad_fps")
    if frames and fps > 0:
        cands.append(frames / fps)
    if not cands:
        raise ValidationError("could not determine the video duration", 400, "no_duration")
    return VideoInfo(max(cands), int(v.get("width") or 0), int(v.get("height") or 0), fps,
                     str(v.get("codec_name", "")), path.stat().st_size, frames)


def check_video(path: Path, cfg: AppConfig) -> VideoInfo:
    info = probe(path)
    if info.duration_s > cfg.max_duration_s:
        raise ValidationError(f"video is {info.duration_s:.0f} s long; the limit is {cfg.max_duration_s:.0f} s",
                              413, "too_long")
    if info.frames > cfg.max_duration_s * MAX_SRC_FPS:
        raise ValidationError(f"video has {info.frames} frames; the limit is {cfg.max_duration_s * MAX_SRC_FPS:.0f}",
                              413, "too_long")
    if info.width < 64 or info.height < 64 or info.width * info.height > 4096 * 4096:
        raise ValidationError(f"unsupported frame size {info.width}x{info.height}", 400, "bad_size")
    return info


def remux_clean(src: Path, dst: Path, cfg: AppConfig, *, fps: int = 10, timeout_s: float = 600.0) -> None:
    """Re-encode the upload to a clean H.264 mp4 at `fps` before it reaches the pipeline (whose own ffmpeg calls
    are not hardened): same forced demuxer and file-only protocol, first video stream only, no audio, data or
    subtitle streams, and cut at the duration limit. The result is also what the pipeline's chunking sees, so
    chunk count and cost are bounded by the limit, not by the container's claims."""
    ext = src.suffix.lower()
    tmp = dst.with_name(dst.stem + ".part.mp4")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *_HARDEN, "-f", DEMUXER[ext], "-i", str(src),
                        "-t", f"{cfg.max_duration_s + 1:g}", "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", f"fps={fps}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-f", "mp4", str(tmp)],
                       check=True, capture_output=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
        tmp.replace(dst)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise ValidationError("could not re-encode the video", 400, "remux_failed") from e
    finally:
        tmp.unlink(missing_ok=True)


NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RESERVED = {"none", "other", "unsure", "video", "all"}


def normalize_name(raw: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
    if not NAME_RE.match(s):
        raise ValidationError(f"behavior name {raw!r} must start with a letter (letters, digits, spaces; <= 32 chars)")
    if s in RESERVED:
        raise ValidationError(f"behavior name {s!r} is reserved")
    return s


def check_definitions(raw, cfg: AppConfig) -> dict[str, str]:
    """`raw` is {name: definition} (or a list of {name, definition}); returns normalized names."""
    if isinstance(raw, list):
        try:
            raw = {str(x["name"]): str(x["definition"]) for x in raw}
        except (KeyError, TypeError) as e:
            raise ValidationError("definitions must be a list of {name, definition}") from e
    if not isinstance(raw, dict) or not raw:
        raise ValidationError("write at least one behavior definition")
    if len(raw) > cfg.max_behaviors:
        raise ValidationError(f"at most {cfg.max_behaviors} behaviors")
    out: dict[str, str] = {}
    for k, v in raw.items():
        name = normalize_name(k)
        text = " ".join(str(v).split())
        if len(text) < 10:
            raise ValidationError(f"definition for {name!r} is too short to be useful")
        if len(text) > 2000:
            raise ValidationError(f"definition for {name!r} is longer than 2000 characters")
        if name in out:
            raise ValidationError(f"duplicate behavior name {name!r}")
        out[name] = text
    return out


def check_text(s: str, what: str, default: str) -> str:
    s = " ".join((s or "").split()) or default
    if len(s) > 200:
        raise ValidationError(f"{what} is longer than 200 characters")
    return s
