"""Final-cut voice-over: load one take per beat, normalize, re-time the beat table, caption, and mix.

No model calls, no speech generation, no transcription: only ffmpeg on Aniket's own recordings.
Takes live in data/video/vo/ as 01_hook ... 09_close (.wav .m4a .mp3 .aac). Run through `video.assemble --vo`.

Steps: load_take (trim silence, two-pass loudnorm) -> plan_timeline (beat table) -> caption_cues (silencedetect)
-> write_srt / mix_track. The v5 sentences come from docs/demo_captions_v5.srt (one cue per sentence), grouped by beat.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from video import common as C
from video import timeline as T

EXTS = (".wav", ".m4a", ".mp3", ".aac")
LEAD_S = 0.3             # the take starts this far into its beat
TAIL_S = 0.4             # minimum silence after the take, inside the beat
KEEP_S = 0.15            # silence kept at each end of a trimmed take
TARGET_LUFS = -16.0
TARGET_TP = -1.5
MIN_PAUSE_S = 0.25       # a sentence boundary is a pause at least this long
PAUSE_BELOW_DB = 20.0    # first pause threshold = take integrated loudness minus this
PAUSE_SPLIT = 0.4         # pause threshold: noise floor + this fraction of (speech level - noise floor)
ENV_HOP_S = 0.05          # envelope hop for pause detection
MIN_PAUSE_RANGE_DB = 10.0  # speech must sit at least this far above the noise floor for pauses to count
MAX_BOUNDARY_SHIFT_S = 2.0  # a chosen pause must lie within this of the word-count estimate of the sentence end
MAX_LINE = 46            # characters per caption line (52 px font, 1620 px of text width)
SRC_SRT = C.ROOT / "docs" / "demo_captions_v5.srt"
FINAL_SRT = C.ROOT / "docs" / "demo_captions_final.srt"
SR = 48000


def take_name(i: int, key: str) -> str:
    return f"{i:02d}_{key}"


def find_takes(vo_dir: Path) -> dict[str, Path]:
    """One file per beat; raise with the full list of missing takes."""
    found, missing = {}, []
    for i, b in enumerate(T.BEATS, 1):
        hit = next((vo_dir / f"{take_name(i, b.key)}{e}" for e in EXTS
                    if (vo_dir / f"{take_name(i, b.key)}{e}").exists()), None)
        if hit:
            found[b.key] = hit
        else:
            missing.append(take_name(i, b.key))
    if missing:
        raise FileNotFoundError(f"missing takes in {vo_dir}: {', '.join(missing)} (any of {', '.join(EXTS)})")
    return found


# ---------------------------------------------------------------- v5 text

def parse_srt(text: str) -> list[tuple[float, float, str]]:
    out = []
    for blk in re.split(r"\n\s*\n", text.strip()):
        m = re.match(r"\d+\s*\n(\d+):(\d\d):(\d\d),(\d{3}) --> (\d+):(\d\d):(\d\d),(\d{3})\s*\n(.*)", blk, re.S)
        if m:
            a = [int(x) for x in m.groups()[:8]]
            out.append((a[0] * 3600 + a[1] * 60 + a[2] + a[3] / 1000,
                        a[4] * 3600 + a[5] * 60 + a[6] + a[7] / 1000, " ".join(m.group(9).split())))
    return out


def beat_sentences(srt: Path = SRC_SRT) -> dict[str, list[str]]:
    """The v5 sentences of each beat (one SRT cue is one sentence; grouped by the cue's start time)."""
    cues = parse_srt(srt.read_text(encoding="utf-8"))
    return {b.key: [t for s, _, t in cues if b.start <= s < b.end] for b in T.BEATS}


def word_count(s: str) -> int:
    return len(s.split())


# ---------------------------------------------------------------- ffmpeg helpers

def _ff(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", *args], capture_output=True, text=True, check=True)


def probe_duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def _max_volume_db(p: Path) -> float:
    r = _ff(["-i", str(p), "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -20.0


def _trim_filter(thr_db: float) -> str:
    one = (f"silenceremove=start_periods=1:start_duration=0.02:start_threshold={thr_db}dB:"
           f"start_silence={KEEP_S}:detection=peak")
    return f"{one},areverse,{one},areverse"


def _loudnorm_json(stderr: str) -> dict:
    blocks = re.findall(r"\{[^{}]*\}", stderr, re.S)
    if not blocks:
        raise RuntimeError("loudnorm printed no measurement")
    return json.loads(blocks[-1])


@dataclass
class Take:
    key: str
    src: Path
    wav: Path             # trimmed + normalized, 48 kHz mono
    raw_s: float
    dur: float            # after trimming (includes about 0.15 s of silence at each end)
    lufs_in: float
    trimmed_s: float = 0.0
    peak_db: float = 0.0


def load_take(key: str, src: Path, work: Path) -> Take:
    """Trim leading/trailing silence (conservative, keep 0.15 s) then two-pass loudnorm to -16 LUFS / -1.5 dBTP."""
    work.mkdir(parents=True, exist_ok=True)
    raw = probe_duration(src)
    # Conservative threshold relative to the take's own peak: 45 dB below it, never above -35 dB or below -60 dB.
    thr = min(-35.0, max(-60.0, _max_volume_db(src) - 45.0))
    trimmed = work / f"{key}.trim.wav"
    _ff(["-y", "-i", str(src), "-vn", "-af", f"aformat=channel_layouts=mono,{_trim_filter(thr)}",
         "-ar", str(SR), "-c:a", "pcm_s16le", str(trimmed)])
    ln = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11"
    m = _loudnorm_json(_ff(["-i", str(trimmed), "-af", f"{ln}:print_format=json", "-f", "null", "-"]).stderr)
    pass2 = (f"{ln}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
             f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    out = work / f"{key}.norm.wav"
    _ff(["-y", "-i", str(trimmed), "-af", pass2, "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(out)])
    dur = probe_duration(out)
    return Take(key, src, out, raw, dur, float(m["input_i"]), max(0.0, raw - dur), _max_volume_db(out))


# ---------------------------------------------------------------- timeline

@dataclass
class BeatPlan:
    key: str
    title: str
    nominal_start: float
    nominal_dur: float
    take_dur: float
    need: float            # LEAD + take + TAIL: the shortest this beat can be
    dur: float = 0.0       # final length
    start: float = 0.0     # final start

    @property
    def end(self) -> float:
        return self.start + self.dur

    @property
    def delta(self) -> float:
        return self.dur - self.nominal_dur


class OverLimit(Exception):
    pass


def plan_timeline(take_durs: dict[str, float], limit: float = T.HARD_LIMIT_S, lead: float = LEAD_S,
                  tail: float = TAIL_S) -> list[BeatPlan]:
    """Extend any beat whose take does not fit; later beats give up spare time (down to their minimum) before the
    total grows. Raises OverLimit (with a per-beat report) if the total would exceed `limit`. Never touches speed."""
    plans = [BeatPlan(b.key, b.title, b.start, b.dur, take_durs[b.key], lead + take_durs[b.key] + tail)
             for b in T.BEATS]
    carry = 0.0
    for p in plans:
        if p.need > p.nominal_dur:
            p.dur = p.need
            carry += p.need - p.nominal_dur
        else:
            use = min(p.nominal_dur - p.need, carry)
            p.dur = p.nominal_dur - use
            carry -= use
    t = 0.0
    for p in plans:
        p.start = t
        t += p.dur
    if t > limit + 1e-9:
        raise OverLimit(over_report(plans, limit))
    return plans


def over_report(plans: list[BeatPlan], limit: float) -> str:
    total = sum(p.dur for p in plans)
    rows = [f"TOTAL {total:.2f} s exceeds the {limit:.0f} s limit by {total - limit:.2f} s. Per beat (the voice is "
            "never sped up; shorten or re-record the beats that run long):",
            "  beat          take   need  nominal  final  vs nominal"]
    for p in plans:
        rows.append(f"  {p.key:<12} {p.take_dur:5.1f} {p.need:6.1f} {p.nominal_dur:7.1f} {p.dur:6.1f} {p.delta:+8.1f}")
    return "\n".join(rows)


# ---------------------------------------------------------------- sentence boundaries

@dataclass
class Boundaries:
    method: str                              # "pauses" or "word-proportional"
    spans: list[tuple[float, float]]         # (start, end) of each sentence, seconds inside the take file
    pauses_found: int = 0
    detail: str = ""


def _envelope_db(wav: Path, hop_s: float = ENV_HOP_S) -> np.ndarray:
    """Short-term RMS level (dBFS) of a PCM wav in hops of `hop_s`."""
    with wave.open(str(wav)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype={2: np.int16, 4: np.int32}[sw]).astype(np.float64)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    x /= float(2 ** (8 * sw - 1))
    hop = max(1, int(hop_s * sr))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    return 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-9)


def detect_pauses(wav: Path, lufs: float, dur: float, need: int = 0) -> tuple[list[tuple[float, float]], float]:
    """Interior pauses (start, end) of at least MIN_PAUSE_S, and the threshold used (dBFS).

    The threshold comes from the take's own level distribution: noise floor (3rd percentile of the 50 ms RMS
    envelope) plus PAUSE_SPLIT of the way up to the speech level (80th percentile). A fixed "loudness minus N dB"
    threshold failed on real home recordings (Nov 3: 0 pauses in every take), because loudness normalization
    raises the room noise with the voice. `lufs` and `need` are kept for the call signature."""
    env = _envelope_db(wav)
    if env.size == 0:
        return [], 0.0
    floor, speech = np.percentile(env, 3), np.percentile(env, 80)
    thr = float(floor + PAUSE_SPLIT * (speech - floor))
    if speech - floor < MIN_PAUSE_RANGE_DB:      # no level contrast: no pause can be told from speech
        return [], thr
    low = env < thr
    pauses, i, n = [], 0, len(env)
    while i < n:
        if not low[i]:
            i += 1
            continue
        j = i
        while j < n and low[j]:
            j += 1
        st, e = i * ENV_HOP_S, j * ENV_HOP_S
        # ignore the kept lead/tail silence: a pause must have speech on both sides
        if e - st >= MIN_PAUSE_S and st > KEEP_S + 0.05 and e < dur - KEEP_S - 0.05:
            pauses.append((st, e))
        i = j
    return pauses, thr


def sentence_spans(wav: Path, lufs: float, dur: float, sentences: list[str]) -> Boundaries:
    k = len(sentences)
    sp0, sp1 = min(KEEP_S, dur / 4), max(dur - KEEP_S, dur * 3 / 4)    # speech occupies the trimmed take minus pads
    if k == 1:
        return Boundaries("single sentence", [(sp0, sp1)])
    words = [word_count(s) for s in sentences]
    tot = sum(words)
    # expected sentence ends from word counts (the fallback, and the anchor for choosing among pauses)
    expect, t = [], sp0
    for w in words:
        t += (sp1 - sp0) * w / tot
        expect.append(t)
    pauses, thr = detect_pauses(wav, lufs, dur, need=k - 1)
    if len(pauses) >= k - 1:
        # each boundary takes the unused pause nearest its word-count estimate, keeping order (so a comma pause
        # far from a sentence end is not chosen just because it is long)
        chosen, lo = [], 0
        for j in range(k - 1):
            hi = len(pauses) - (k - 2 - j)                      # leave enough pauses for the later boundaries
            cand = range(lo, hi)
            best = min(cand, key=lambda i: abs((pauses[i][0] + pauses[i][1]) / 2 - expect[j]))
            mid = (pauses[best][0] + pauses[best][1]) / 2
            if abs(mid - expect[j]) > MAX_BOUNDARY_SHIFT_S:     # too far from where the words say: use the estimate
                chosen.append((expect[j], expect[j]))
                continue
            chosen.append(pauses[best])
            lo = best + 1
        edges = [sp0] + [x for p in chosen for x in p] + [sp1]
        spans = [(edges[2 * i], edges[2 * i + 1]) for i in range(k)]
        return Boundaries("pauses", spans, len(pauses), f"threshold {thr:.1f} dB")
    spans, t = [], sp0
    for e in expect:
        spans.append((t, e))
        t = e
    return Boundaries("word-proportional", spans, len(pauses),
                      f"only {len(pauses)} pause(s) for {k} sentences (up to {thr:.1f} dB)")


# ---------------------------------------------------------------- captions

def split_lines(text: str, max_line: int = MAX_LINE) -> str:
    """Break one caption over at most 2 lines at the word boundary nearest the middle (only if it needs it)."""
    if len(text) <= max_line:
        return text
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def chunk_sentence(text: str, max_chars: int = 2 * MAX_LINE) -> list[str]:
    """A sentence longer than two lines becomes several cues, cut after commas/semicolons where possible."""
    if len(text) <= max_chars:
        return [text]
    n = math.ceil(len(text) / max_chars)
    words = text.split()
    target = len(text) / n
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        cl = len(" ".join(cur))
        soft = w[-1] in ",;:" and cl >= target * 0.6
        if len(chunks) < n - 1 and (cl >= target or soft) and cl >= target * 0.6:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    return chunks


@dataclass
class Cue:
    start: float
    end: float
    text: str
    beat: str = ""


def beat_cues(spans: list[tuple[float, float]], sentences: list[str], take_start: float, beat: str) -> list[Cue]:
    """Absolute-time cues. A sentence's cue stays up through its trailing pause, minus a small gap before the next."""
    cues = []
    for i, ((s, e), text) in enumerate(zip(spans, sentences, strict=True)):
        end = e + 0.1
        if i + 1 < len(spans):
            end = min(end, spans[i + 1][0] - 0.05)
        parts = chunk_sentence(text)
        total = sum(len(p) for p in parts)
        t = s
        for p in parts:
            d = (end - s) * len(p) / total
            cues.append(Cue(take_start + t, take_start + t + d, split_lines(p), beat))
            t += d
    return cues


def fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues: list[Cue], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [f"{i}\n{fmt_srt(c.start)} --> {fmt_srt(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


# ---------------------------------------------------------------- audio mix

def mix_track(takes: dict[str, Take], plans: list[BeatPlan], total: float, out: Path) -> Path:
    """Stereo 48 kHz track: every take at beat start + 0.3 s on a silent bed. No music."""
    cmd = ["-y", "-f", "lavfi", "-t", f"{total:.3f}", "-i", f"anullsrc=r={SR}:cl=mono"]
    filt, labels = [], ["[0:a]"]
    for i, p in enumerate(plans, 1):
        cmd += ["-i", str(takes[p.key].wav)]
        ms = int(round((p.start + LEAD_S) * 1000))
        filt.append(f"[{i}:a]aformat=sample_fmts=fltp:channel_layouts=mono,adelay={ms}[a{i}]")
        labels.append(f"[a{i}]")
    filt.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,"
                f"aformat=channel_layouts=stereo,atrim=0:{total:.3f}[mix]")
    _ff(cmd + ["-filter_complex", ";".join(filt), "-map", "[mix]", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)])
    return out


def measure_lufs(media: Path) -> dict[str, float]:
    """ebur128 integrated loudness and true peak of a file's audio."""
    r = _ff(["-i", str(media), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = r.stderr.split("Summary:")[-1]
    i = float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail).group(1))
    return {"integrated_lufs": i, "true_peak_dbtp": tp}


# ---------------------------------------------------------------- beat table

@dataclass
class Result:
    plans: list[BeatPlan]
    takes: dict[str, Take]
    bounds: dict[str, Boundaries]
    cues: list[Cue] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.plans[-1].end


def analyse(vo_dir: Path, work: Path, srt: Path = SRC_SRT) -> Result:
    srcs = find_takes(vo_dir)
    sents = beat_sentences(srt)
    takes = {}
    for b in T.BEATS:
        t = load_take(b.key, srcs[b.key], work)
        takes[b.key] = t
        print(f"  take {b.key:<10} {t.src.name:<18} raw {t.raw_s:6.2f} s  trimmed {t.trimmed_s:5.2f} s  "
              f"-> {t.dur:6.2f} s  (in {t.lufs_in:6.1f} LUFS, out peak {t.peak_db:5.1f} dB)")
    plans = plan_timeline({k: t.dur for k, t in takes.items()})
    res = Result(plans, takes, {})
    for p in plans:
        t = takes[p.key]
        bd = sentence_spans(t.wav, TARGET_LUFS, t.dur, sents[p.key])
        res.bounds[p.key] = bd
        res.cues += beat_cues(bd.spans, sents[p.key], p.start + LEAD_S, p.key)
    return res


def write_beats_final(res: Result, pieces_by_beat: dict[str, list], out: Path, loud: dict | None = None) -> None:
    rows = ["# Final cut v1: beat table from the real voice-over", "",
            f"Total {T.fmt(res.total)} ({res.total:.1f} s), hard limit {T.HARD_LIMIT_S:.0f} s. Voice-over starts "
            f"{LEAD_S} s into each beat, at least {TAIL_S} s of tail, never sped up.", "",
            "| # | Beat | Start | Dur (s) | Plan dur | Change | Take (s) | Caption method | On screen |",
            "|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(res.plans, 1):
        on = "; ".join(f"{x.label} ({x.dur:.2f} s)" for x in pieces_by_beat.get(p.key, []))
        rows.append(f"| {i} | {p.title} | {p.start:.2f} | {p.dur:.2f} | {p.nominal_dur:g} | {p.delta:+.2f} | "
                    f"{p.take_dur:.2f} | {res.bounds[p.key].method} | {on} |")
    if loud:
        rows += ["", f"Final mix: {loud['integrated_lufs']:.1f} LUFS integrated, true peak "
                 f"{loud['true_peak_dbtp']:.1f} dBTP."]
    rows.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows), encoding="utf-8")


# ---------------------------------------------------------------- synthetic takes (tests and dry runs only)

def make_synthetic_take(path: Path, sentence_words: list[int], wpm: float = 145.0, gap_s: float = 0.45,
                        lead_s: float = 0.4, tail_s: float = 0.5, level_db: float = -12.0, base_hz: float = 180.0,
                        ) -> float:
    """Sine bursts (one per sentence) separated by silent gaps, sized to wpm, with extra silence at both ends.
    Not speech: a stand-in to exercise the pipeline. Returns the file length."""
    total_speech = sum(sentence_words) / wpm * 60.0
    burst_total = max(0.5, total_speech - gap_s * (len(sentence_words) - 1))
    nw = sum(sentence_words)
    parts, cmd = [], ["-y"]
    n = 0

    def sil(d: float) -> None:
        nonlocal n
        cmd.extend(["-f", "lavfi", "-t", f"{d:.3f}", "-i", f"anullsrc=r={SR}:cl=mono"])
        parts.append(f"[{n}:a]")
        n += 1

    sil(lead_s)
    for i, w in enumerate(sentence_words):
        d = burst_total * w / nw
        cmd.extend(["-f", "lavfi", "-t", f"{d:.3f}", "-i",
                    f"sine=frequency={base_hz + 40 * (i % 5)}:sample_rate={SR},volume={level_db}dB"])
        parts.append(f"[{n}:a]")
        n += 1
        if i < len(sentence_words) - 1:
            sil(gap_s)
    sil(tail_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ff(cmd + ["-filter_complex", f"{''.join(parts)}concat=n={n}:v=0:a=1[o]", "-map", "[o]",
               "-ar", str(SR), "-ac", "1", str(path)])
    return probe_duration(path)


def make_synthetic_set(vo_dir: Path, wpm: float = 145.0, srt: Path = SRC_SRT) -> dict[str, float]:
    sents = beat_sentences(srt)
    out = {}
    for i, b in enumerate(T.BEATS, 1):
        out[b.key] = make_synthetic_take(vo_dir / f"{take_name(i, b.key)}.wav", [word_count(s) for s in sents[b.key]],
                                         wpm=wpm)
    return out


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
