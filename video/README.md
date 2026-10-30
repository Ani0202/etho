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
