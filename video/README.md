# Demo video production kit

No model calls, no spending. Needs the Playwright Chromium in `~/.cache/ms-playwright`, `ffmpeg` (with libass and
xfade), and the repo venv (OpenCV + NumPy). Outputs go to `data/video/` (git-ignored), plus one README GIF in
`docs/media/`.

    uv run python -m video.assemble          # cards + animations + silent rough cut + stills + beats.md
    uv run python -m video.assemble --no-cards   # reuse the cards and animations already rendered
    uv run python -m video.cards             # cards only; prints the number -> source map
    uv run python -m video.anim              # opening shot, README GIF, results-beat animation
    uv run pytest video/tests                # numbers traceability, timeline vs DEMO_PLAN, helpers

- `numbers.py`: every number on a card is loaded here from a committed file under `eval/results/`; `cards.py`
  never types one. `video/tests/test_numbers.py` fails if a card shows a number the registry cannot trace, and
  checks the DEMO_PLAN "Before export" honesty rules (no "beats supervised" or "N x faster", per-behavior F1s
  next to 0.58, no fly F1, "training-free" always with its footnote).
- `timeline.py`: the v5 beat table (9 beats, 174 s). `test_timeline.py` compares it with `docs/DEMO_PLAN.md`.
- `assemble.py`: app beats take `data/video/capture/<name>.(mp4|png)` for `live_run`, `code`, `trace`,
  `correction`, `fly`; a missing one becomes a labeled PLACEHOLDER slate. Re-run after dropping captures in.
- Captions: `docs/demo_captions_v5.srt` converted to ASS at PlayRes 1920x1080 (52 px, dark box, bottom safe area)
  and burned in with libass. Draft timings: retime from the real voice-over.
- Behavior colors and fill patterns are the app's dark theme (`etho/app/static/app.css`, `ethogram.js`).

- `fly_clip.py`: the qualitative fruit-fly beat (`python -m video.fly_clip`, writes `data/video/capture/fly.mp4`, 14 s). Reuses the stored SAM 3.1 masks and the frozen gate predictions for Fly vs. Fly movie 1; no model calls, no accuracy number on screen (ADR-004). Window choice is explained in its docstring.

## Final cut with the real voice-over (`vo.py`)
No model calls, no speech generation, no transcription: ffmpeg only. Put one take per beat in `data/video/vo/`
(`01_hook`, `02_who`, `03_live_run`, `04_code`, `05_results`, `06_correction`, `07_evidence`, `08_fly`, `09_close`;
`.wav .m4a .mp3 .aac`), then:

    uv run python -m video.assemble --vo            # re-renders cards/animations first; add --no-cards to skip

Writes `data/video/final_v1.mp4` (H.264 1080p30, AAC 192k), `data/video/beats_final.md`, `data/video/stills_final/`
and `docs/demo_captions_final.srt` (commit it). Each take is silence-trimmed (keeps 0.15 s) and two-pass loudnormed to
-16 LUFS / -1.5 dBTP; it starts 0.3 s into its beat with at least 0.4 s of tail. A beat that does not fit grows and later
beats give back spare time down to their own minimum; over 180.0 s it stops and prints the overage per beat (the voice is
never sped up). Captions: one per v5 sentence, timed from the k-1 longest pauses (silencedetect, >= 0.25 s) or, if there
are too few, split by word count (printed per beat); a sentence over two lines becomes several cues.
`--vo-dir DIR --out-dir DIR` redirect inputs/outputs (dry runs). `uv run pytest video/tests/test_vo.py` needs only ffmpeg.
