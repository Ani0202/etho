import json
import subprocess
from pathlib import Path

import pytest

from etho.perception import planner as P

LINE_TWO = ("<0f>0<|box;x1=1;y1=1;x2=2;y2=2;w=64;h=48|><|mask;x=0;y=0;data=2,2,!x|>,"
            "1<|box;x1=10;y1=10;x2=11;y2=11;w=64;h=48|><|mask;x=0;y=0;data=2,2,!x|>")
LINE_ONE = "<0f>0<|box;x1=1;y1=1;x2=2;y2=2;w=64;h=48|><|mask;x=0;y=0;data=2,2,!x|>"


class R:
    def __init__(self, lines=(), text="", cost=0.001):
        self._lines, self.text, self.cost_usd = list(lines), text, cost

    def lines(self):
        return iter(self._lines)


class FakeGW:
    def __init__(self, video_lines):
        self.video_lines = video_lines
        self.calls = []

    def chat(self, *a, **k):
        self.calls.append(("chat", k.get("effort")))
        return R(text=json.dumps({"animals": "two mice", "expected_count": 2, "phrases": ["black mouse", "mouse"],
                                  "identity_cue": "coat color: one black, one white"}))

    def sam(self, phrase, **k):
        self.calls.append(("sam", phrase, "video" if "video" in k else "image"))
        if "image" in k:
            return R([LINE_TWO])
        return R(self.video_lines[phrase])


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=30", "-t", "6",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    return v


def test_planner_falls_through_to_a_phrase_that_passes_the_video_probe(video):
    gw = FakeGW({"black mouse": [LINE_ONE] * 50, "mouse": [LINE_TWO] * 50})
    p = P.plan(Path(video), gw=gw, public_data=True)
    assert p.phrase == "mouse" and p.expected_count == 2 and p.identity_method == "intensity"
    probes = [s for s in p.steps if s["step"] == "video_probe"]
    assert [s["phrase"] for s in probes] == ["black mouse", "mouse"] and probes[0]["frac_with_expected"] < 0.9
    assert gw.calls[0] == ("chat", "medium")
