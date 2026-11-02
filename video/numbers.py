"""Every number shown on a card is loaded here, at render time, from a committed results file.

Nothing numeric is typed into the card scripts. `Numbers.get(key)` returns a `Num` (display string, raw value,
file, location) and records it as used; `Numbers.source_map()` prints number -> file + key. The card text is
checked against this registry by video/tests/test_numbers.py.

Sources (all committed):
- eval/results/score-test-20261028T164333Z-473c64.json   test-1 summary (0.58 macro, per-behavior F1, 19 videos)
- eval/results/live_run_nov2_check.json                  this clip's 0.44 and per-behavior F1 (live run; SOURCE=live)
- eval/results/showcase_mouse075_check.json              the showcase run's 0.42 (SOURCE=showcase)
- eval/results/test1_report.md                           supervised .793/.829/.864 (11.1), speed (11.1, 11.3),
                                                         cost (11.1), manual 3-4x, approved footnote (11.2)
The speed and cost figures exist only in the report: the JSON speed block (0.26x) is the pre-correction number
that omits preprocessing and first-pass tier-1 timing (report 11.3), so it is deliberately not used.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from video.common import ROOT, SOURCE

TEST_JSON = "eval/results/score-test-20261028T164333Z-473c64.json"
CHECK_JSONS = {"showcase": "eval/results/showcase_mouse075_check.json",   # the Oct 30 showcase run
               "live": "eval/results/live_run_nov2_check.json"}            # the Nov 2 live run (same clip)
CHECK_JSON = CHECK_JSONS[SOURCE]
REPORT_MD = "eval/results/test1_report.md"


@dataclass(frozen=True)
class Num:
    key: str
    display: str
    raw: float | str
    file: str
    where: str

    def __str__(self) -> str:
        return self.display


def _json(rel: str):
    return json.loads((ROOT / rel).read_text())


def _dig(obj, path: str):
    for part in path.split("/"):
        obj = obj[part]
    return obj


def _grab(text: str, pattern: str, what: str) -> re.Match:
    m = re.search(pattern, text, re.S)
    if not m:
        raise ValueError(f"{REPORT_MD}: could not find {what} (pattern {pattern!r})")
    return m


class Numbers:
    def __init__(self) -> None:
        self.test = _json(TEST_JSON)
        self.check = _json(CHECK_JSON)
        self.md = (ROOT / REPORT_MD).read_text(encoding="utf-8")
        self.used: dict[str, Num] = {}
        self._all: dict[str, Num] = {}
        self._build()

    # ---- construction -------------------------------------------------------------------------------------
    def _add(self, key, display, raw, file, where):
        self._all[key] = Num(key, display, raw, file, where)

    def _build(self) -> None:
        arm = "metrics/arms/zs_t1"
        macro = _dig(self.test, f"{arm}/macro_f1")
        self._add("macro_f1", f"{macro:.2f}", macro, TEST_JSON, f"{arm}/macro_f1")
        for b in ("attack", "investigation", "mount"):
            v = _dig(self.test, f"{arm}/behaviors/{b}/f1")
            self._add(f"f1_{b}", f"{v:.3f}", v, TEST_JSON, f"{arm}/behaviors/{b}/f1")
        n = _dig(self.test, f"{arm}/n_sequences")
        self._add("n_test_videos", str(n), n, TEST_JSON, f"{arm}/n_sequences")

        clip = _dig(self.check, "showcase/final/macro")
        self._add("clip_macro", f"{clip:.2f}", clip, CHECK_JSON, "showcase/final/macro")
        for b in ("attack", "investigation", "mount"):
            v = _dig(self.check, f"showcase/final/{b}/f1")
            self._add(f"clip_f1_{b}", f"{v:.3f}", v, CHECK_JSON, f"showcase/final/{b}/f1")
        # cross-check: the pooled values in the check file must equal the test summary
        for b in ("attack", "investigation", "mount", "macro"):
            a = _dig(self.check, f"test_averages/pooled_zs_t1/{b}")
            t = macro if b == "macro" else _dig(self.test, f"{arm}/behaviors/{b}/f1")
            if abs(a - t) > 1e-9:
                raise ValueError(f"pooled {b}: {a} in {CHECK_JSON} != {t} in {TEST_JSON}")

        # supervised references: report 11.1 accuracy line "(... Tables 1 and 2: .793, .829, .864)"
        m = _grab(self.md, r"Tables 1 and 2: (\.\d{3}), (\.\d{3}), (\.\d{3})\)", "supervised references")
        sup = [float(x) for x in m.groups()]
        for key, s, v in zip(("sup_1dconv", "sup_taskprog", "sup_mabe_top1"), m.groups(), sup, strict=True):
            self._add(key, s, v, REPORT_MD, "11.1 accuracy line, Tables 1 and 2")
        lo, hi = min(sup), max(sup)
        self._add("sup_lo", f"{lo:.2f}", lo, REPORT_MD, "11.1 accuracy line (min of the three, 2 dp)")
        self._add("sup_hi", f"{hi:.2f}", hi, REPORT_MD, "11.1 accuracy line (max of the three, 2 dp)")

        # speed: 11.3 "Pooled 0.8777x, median 0.8602x, max 1.5421x"
        m = _grab(self.md, r"\*\*Pooled (\d\.\d{4})x, median (\d\.\d{4})x, max (\d\.\d{4})x", "corrected speed")
        pooled, _median, mx = (float(x) for x in m.groups())
        self._add("speed_pooled", f"{pooled:.2f}", pooled, REPORT_MD, "11.3 'Pooled 0.8777x'")
        self._add("speed_worst", f"{mx:.2f}", mx, REPORT_MD, "11.3 'max 1.5421x'")
        # cross-check against the 11.1 speed line "0.88x real time on test; worst 1.54x"
        m1 = _grab(self.md, r"\(0\.88× real time on test; worst 1\.54×\); manual\s+(?:>\s*)?scoring takes (\d)–(\d)×",
                   "speed line")
        if f"{pooled:.2f}" != "0.88" or f"{mx:.2f}" != "1.54":
            raise ValueError("11.3 speed rounds to something other than the approved 11.1 line (0.88 / 1.54)")
        self._add("manual_lo", m1.group(1), int(m1.group(1)), REPORT_MD,
                  "11.1 speed line 'manual scoring takes 3–4×'")
        self._add("manual_hi", m1.group(2), int(m1.group(2)), REPORT_MD,
                  "11.1 speed line 'manual scoring takes 3–4×'")

        m = _grab(self.md, r"\*\*\$(\d+\.\d{2}) per hour of video\*\*", "cost per hour")
        self._add("cost_per_hour", f"${m.group(1)}", float(m.group(1)), REPORT_MD, "11.1 cost line")

        # approved demo-slide footnote (11.2)
        seg = _grab(self.md,
                    r"\*\*Demo-slide version\*\*.*?\n> \*\*(Training-free:[^\n]*\n> [^\n]*?)\*\*¹\n>\n> ¹ ([^\n]+)",
                    "demo-slide headline and footnote")
        head = re.sub(r"\n> ", " ", seg.group(1)).strip()
        foot = seg.group(2).strip()
        self._add("headline_text", head, head, REPORT_MD, "11.2 demo-slide headline")
        self._add("footnote_text", foot, foot, REPORT_MD, "11.2 demo-slide footnote")
        mm = _grab(foot, r"Tuned on (\d+) labeled dev", "dev video count")
        self._add("n_dev_videos", mm.group(1), int(mm.group(1)), REPORT_MD,
                  "11.2 footnote 'Tuned on 13 labeled dev videos'")
        mm = re.search(r"scored once on the (\d+) official test videos", foot)
        if int(mm.group(1)) != n:
            raise ValueError("footnote test-video count differs from the test summary")

    # ---- access -------------------------------------------------------------------------------------------
    def get(self, key: str) -> Num:
        n = self._all[key]
        self.used[key] = n
        return n

    def s(self, key: str) -> str:
        return self.get(key).display

    def all_displays(self) -> set[str]:
        return {n.display for n in self._all.values()}

    def source_map(self) -> str:
        rows = [f"{n.display if len(n.display) < 40 else n.display[:37] + '...':<40} <- {n.file} :: {n.where}"
                for n in sorted(self.used.values(), key=lambda x: x.key)]
        return "number -> source\n" + "\n".join(rows)


def numeric_tokens(text: str) -> list[str]:
    """Number-like tokens in visible text (e.g. '0.58', '.793', '$11.25', '3', '4')."""
    text = re.sub(r"\b\d+:\d{2}\b", " ", text)  # clock labels on time axes
    return re.findall(r"(?<![A-Za-z\d.])\$?\d*\.?\d+", text)


# Numeric tokens that are names or citations, not results: model versions, citation year, licence version.
NAME_TOKENS = {"3.1", "1.3", "2021", "4.0", "2.0"}   # model names (SAM 3.1, Muse Spark 1.3), years, licences


def untraceable(text: str, N: Numbers) -> list[str]:
    """Tokens in `text` that are neither a registered number nor a whitelisted name/citation token."""
    ok = N.all_displays() | NAME_TOKENS
    out = []
    for t in numeric_tokens(text):
        if t in ok or t.lstrip("$") in ok or ("$" + t) in ok:
            continue
        out.append(t)
    return out
