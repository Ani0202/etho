# Etho demo video: script for the video app

Source: `docs/DEMO_PLAN.md`, script v5 as of 2026-10-30 (395 words), and the draft captions `docs/demo_captions_v5.srt`
(34 captions, one per sentence, 145 wpm). Nine scenes, 0:00-2:56, at most 3:00 (hard limit).

Rules this script follows:
- **Voice-over** is the approved v5 text, word for word. Aniket records it himself. **Any narration the app
  generates is a timing placeholder only** and must be replaced by Aniket's recording before export.
- **Visuals of the product, the animals, ethograms, code and results are real assets only**: a screen recording of
  the real app, or a file listed below that was rendered by the repo's own scripts from the real run's video, masks
  and labels (no model calls, no generative imagery). The app may generate only non-product elements: backgrounds
  behind cards, title styling, transitions.
- **Every number on screen** comes from a committed file; the table at the end lists each one. Approved wording
  and footnotes are unchanged.
- **Live-run numbers are "FILL AFTER THE NOV 2 LIVE RUN"** (DEMO_PLAN, "Live-run order of operations"): record the
  live run, recompute its score with `eval/showcase_check.py`, update the voice-over line and captions, then record
  the final voice-over.

Global notes for the app:
- 1920x1080, 30 fps. Captions burned in from `docs/demo_captions_v5.srt` (retime to the real voice-over): white,
  bold, at least 48 px (52 px used in the rough cut), on a dark semi-opaque box, bottom-centred, never above
  y = 860 so they don't cover card content; split any caption longer than two lines.
- Fonts on cards are 44 px or more (footnotes 34-36 px). Finals are watched on a shared screen: no thin type, no
  small UI text without a zoom.
- Default transition: 0.5 s crossfade. Inside a scene, cut on action (a click, a page change).
- Audio: voice-over normalized to about -16 LUFS; music, if any, no louder than -30 LUFS.

Asset paths:
- `docs/...` and `video/...` are committed.
- `data/video/...` is gitignored, because it contains CalMS21 and Fly vs. Fly frames. Those files are rendered on the
  VM by the commands in the asset list. Copy them to the machine that runs the video app (`scp -r etho-vm:work/etho/data/video .`).

---

## Scene 1: Hook (0:00-0:20)

**Voice-over**
> In behavior labs, someone still watches every second of video and labels it by hand. For social behavior, that
> takes three to four times the video's length. This is Etho, for Push the Frontier: no training, just
> plain-English definitions. On a public benchmark, it reaches 0.58 F1.

**Visual**
- 0:00-0:05: `data/video/opening.mp4`. It shows the real overlay video of CalMS21 test video mouse075 (121.0-126.0 s
  of the showcase run), with SAM 3.1 outlines on both mice, while the Etho and Expert ethogram rows fill in under a
  moving playhead. It is rendered from `data/showcase/calms21-mouse075-live/overlay.mp4` and its labels.
- 0:05-0:20: `docs/video_assets/title.png`.

**On-screen text**
- Opening shot: behavior legend "attack · investigation · mount"; row labels "Etho", "Expert"; credit "Video:
  CalMS21 (Sun et al. 2021), CC BY 4.0".
- Title card: "Etho"; "Push the Frontier"; "Zero-shot animal behavior scoring: write the behaviors in plain English,
  get a scored ethogram."; the approved headline "Training-free¹: plain-English behavior definitions, no labeled
  examples given to the models, no model training."; footnote "¹ Tuned on 13 labeled dev videos; scored once on the
  19 official test videos. Full disclosure in README."

**Transition:** hard cut from black into the opening shot. 0.5 s crossfade into the title card.

**Notes for the app**
- The opening shot is the hook. Start on motion: no fade-in, no logo first.
- Hold the title card still. A slow push-in of at most 3% is fine, applied to the whole card, never to the text alone.

---

## Scene 2: Who it's for, the gap (0:20-0:35)

**Voice-over**
> Behavior scoring sits underneath neuroscience research and drug-safety studies. Today it takes a person with a
> stopwatch, or software you must first feed hand-labeled examples of every behavior. Etho needs one sentence per
> behavior.

**Visual:** `data/video/cards/who.png`. It shows the real CalMS21 human annotation for the clip (mouse075, 0-300 s)
drawn as an ethogram timeline, from `data/demo/expert_mouse075_task1_annotator1.npz`.

**On-screen text**
- The card's title and the behavior legend.
- The time axis.
- The credit "CalMS21, Sun et al. 2021, CC BY 4.0".

**Transition:** 0.5 s crossfade.

**Notes for the app**
- On "one sentence per behavior", the app may fade in one definition line from `configs/calms21_definitions.json`
  as a lower-third text overlay. Use the exact text, no paraphrase. Otherwise hold the card.

---

## Scene 3: Live run (0:35-1:04): numbers FILL AFTER THE NOV 2 LIVE RUN

**Voice-over**
> This is a test video Etho was never tuned on. I paste the dataset authors' own definitions of attack,
> investigation and mount. Muse Spark decides what to track. SAM 3.1 follows both mice, and keeps them apart even
> when they're tangled together. Muse Spark turns each definition into a short Python rule, and Muse Spark Open
> double-checks the borderline moments from strips of frames.

**Visual:** screen recording R1 (asset list), the real live run, cut to 29 s.
1. 0:35-0:40: the upload form. Pasting the dataset authors' definitions, then pressing score.
2. 0:40-0:47: the planner card. It shows the phrases Muse Spark proposed and the one SAM 3.1 confirmed.
3. 0:47-0:56: the overlay video playing: both mice tracked through a tangle.
4. 0:56-1:04: the stage progress, then the per-stage timing and cost card.

Waits are cut and tagged "time skipped". If the live run fails on the day, use the stand-in
`data/video/capture/live_run_standin.mp4`, labeled "precomputed" on screen.

**On-screen text**
- The app's own UI text, and a "time skipped" tag at each cut.
- Wall-clock time, total cost and per-stage numbers: **FILL AFTER THE NOV 2 LIVE RUN**. These are whatever the
  recorded run's timing card shows. The stand-in shows the showcase run, 2:09 and $0.89, from
  `data/showcase/calms21-mouse075-live/result.json`.

**Transition:** cut on action at each page change, then a 0.5 s crossfade out.

**Notes for the app**
- Zoom to about 1.4x on the planner card while "Muse Spark decides what to track" plays.
- Highlight the CHOSEN phrase with a soft outline drawn by the app over the recording, not baked into it.
- Keep the app's visible clock in frame during cuts.

---

## Scene 4: Code beat (1:04-1:21)

**Voice-over**
> Around the models, built this week: a gateway that caps every dollar and caches every call, the tracking and
> identity code, a locked-down sandbox for the model-written rules, and the two-step check, with more than six
> hundred tests.

**Visual**
- 1:04-1:11: `docs/architecture.svg`, also rendered as `data/video/capture/code_architecture.png`.
- 1:11-1:21: `data/video/capture/code.mp4`. It pans across real, syntax-highlighted excerpts:
  - `etho/gateway/gateway.py`, the cap check and cache;
  - `etho/adjudication/tier1.py`, the cascade decision;
  - `etho/rules/sandbox.py`, the bwrap sandbox and AST check;
  - then the commit-date overlay and the pytest summary line.
  Stills are `data/video/capture/code_gateway.png`, `code_tier1.png`, `code_sandbox.png` and `code_tests.png`.

**On-screen text**
- Diagram labels: 1 Planner (Muse Spark 1.3), 2 Tracking (SAM 3.1), 3 Rules (Muse Spark 1.3, sandbox), 4 Frame-strip
  check (Muse Spark Open), 5 Report (Muse Spark 1.3), 6 Correction loop, and the gateway bar.
- File names and the commit dates from `git log`.
- The pytest line, verbatim from a real run. The current capture shows "726 passed, 4 skipped". Re-run it on Nov 2
  (shot 2) and use that run's line.

**Transition:** 0.5 s crossfade, diagram to code. Continuous pan inside the code clip.

**Notes for the app**
- Split the caption "Around the models, built this week: ..." into two captions, so it never covers the gateway bar
  at the bottom of the diagram.

---

## Scene 5: Results and the traced error (1:21-1:50)

**Voice-over**
> Here's Etho beside the expert's labels. This is one of the harder clips: 0.42 here, against 0.58 across all 19
> test videos. Here, mount is where it struggles most. Every label is traceable, including the mistakes. At thirty-eight
> seconds, Etho says mount; the expert says attack. The frame check answered "attack", but a rule in the cascade
> set it aside, and the trace shows exactly which step. No label goes unexplained.

**Visual**
- 1:21-1:41: `data/video/results_beat.mp4`. It shows the real Etho-vs-Expert ethogram for 0-300 s with the score
  badge, then zooms to 34-48 s with a box over the traced error (37.5-44.0 s: Etho mount, expert attack). It is
  rendered from the showcase labels. The badge is `docs/video_assets/badge_clip_score.png`.
- 1:41-1:50: screen recording R2, the trace panel for the Etho mount bout at 37.5-38.0 s. A cut version is
  `data/video/capture/trace.mp4`; the full capture is `trace_full.mp4`.

**On-screen text**
- Badge: "this clip 0.42 · all 19 test videos 0.58". Second line: "all 19, per behavior: attack 0.449 · investigation
  0.635 · mount 0.656".
- Zoom labels "Etho: mount", "Expert: attack"; time axis 0:34-0:48.
- Trace panel, the app's own text:
  - "Trace: mount 0:37.5 to 0:38.0 (original rules)";
  - "SET ASIDE BY THE RESTRICTION · window 37.5-38.0 s: rules said 'mount', tier 1 said 'attack' (confidence 0.96)";
  - "Decided by: Rules".
- **0.42 and the trace: CONFIRM AFTER THE NOV 2 LIVE RUN.** They belong to the showcase run. If the video shows the
  Nov 2 run, recompute with `eval/showcase_check.py`, re-render the badge, and re-check that the traced bout's first
  verdict matches the line (DEMO_PLAN, live-run step 3).

**Transition:** 0.5 s crossfade, animation to trace recording.

**Notes for the app**
- Hold the badge for the whole "0.42 here, against 0.58" sentence.
- On "At thirty-eight seconds", hold the zoom on the gold box.
- In the trace recording, zoom to about 1.3x on the amber "SET ASIDE BY THE RESTRICTION" row, and keep "Decided by:
  Rules" in frame. Captions must not cover it, so move the caption up for this shot or crop the recording.

---

## Scene 6: Correction loop (1:50-2:00)

**Voice-over**
> Disagree? Mark a few bouts. Etho rewrites the rule and shows you exactly what changed. The researcher stays in
> control.

**Visual:** screen recording R3. The same page; click an Etho mount bout, mark it "actually attack", and the pending
correction appears. A cut version is `data/video/capture/correction.mp4`. The public read-only app shows "Revise rules"
disabled ("Live use is off"); see the shot list for showing a real revision.

**On-screen text:** the app's own UI text: the pending item, for example "mount 0:38.4-0:44.0 is wrong (actually
attack)", and the button label. No accuracy number (no gain claim).

**Transition:** 0.5 s crossfade.

**Notes for the app:** zoom to about 1.3x on the correction panel. Do not add any "accuracy improved" text.

---

## Scene 7: Evidence card (2:00-2:26)

**Voice-over**
> On the test set, scored once: 0.58 F1, with no training. Supervised methods, trained on labeled data and given
> pose keypoints, reach 0.79 to 0.86. Etho works from raw video, in about the video's own length, for about eleven
> dollars per hour of footage. Our biggest surprise: Muse Spark Open, looking at six frames at low effort, added
> 0.2 F1.

**Visual:** `docs/video_assets/results.png`.

**On-screen text**
- "Etho, macro F1, no training¹: 0.58"; "attack 0.449 · investigation 0.635 · mount 0.656".
- "Supervised, trained on labeled data and pose keypoints: 0.79-0.86 (.793 · .829 · .864)".
- "Etho speed 0.88× real time (worst 1.54×)".
- "Manual scoring takes 3-4× the video's length (Segalin et al. 2021)".
- "Model cost $11.25 per hour of video".
- Footnote: "¹ Tuned on 13 labeled dev videos; scored once on the 19 official test videos. Full disclosure in
  README."

**Transition:** 0.5 s crossfade.

**Notes for the app**
- The card is complete as rendered. The app may reveal the three tiles in sequence with the voice-over: accuracy,
  then supervised, then speed and cost. Keep each number's tile intact, and never show 0.58 without the per-behavior
  values.
- Do not add the 0.2 F1 as on-screen text. It is spoken only, with its source in the table.

---

## Scene 8: Second species (2:26-2:41)

**Voice-over**
> Same pipeline, new animal: fruit flies, from one sentence, with no retraining. It's a preview: it hasn't cleared
> the confidence bar we set, so there's no number here.

**Visual:** `data/video/capture/fly.mp4`, 15 s at real speed: the real SAM 3.1 masks on two flies in Fly vs. Fly training
movie 4, 992.0-1007.0 s. The window was fixed by Aniket's rule from expert labels only (DECISIONS 2026-10-30 20:29 UTC;
`eval/results/fly_clip_window.json`). Etho's tag and strip are its frozen gate predictions for that window.

**On-screen text**
- "Qualitative preview: not scored".
- "Fruit flies, same pipeline"; "One-sentence definitions, no retraining".
- "Etho's behavior tag": "Lunge", "Wing threat" or "No behavior detected".
- Legend "wing threat · lunge"; "Tracking: SAM 3.1 · detectors written by Muse Spark"; the movie clock "movie 4, time 16:32.0-16:47.0".
- Credit "Video: Fly vs. Fly (Eyjolfsdottir et al. 2014), CC0".
- **No accuracy number** (ADR-004).

**Transition:** 0.5 s crossfade.

**Notes for the app:** no zoom needed. Never add a score or a percentage to this scene.

---

## Scene 9: Close (2:41-2:56)

**Voice-over**
> Etho runs on Meta's models: SAM 3.1, Muse Spark and Muse Spark Open. It's for every lab that still scores
> behavior by hand. It's open source. Try it live at the link below.

**Visual**
- 2:41-2:48: `docs/video_assets/model_stack.png`.
- 2:48-2:56: `docs/video_assets/closing.png`.

**On-screen text**
- Model stack:
  - "Built on Meta's models";
  - "SAM 3.1: Tracking: masks and identity for both animals, every frame";
  - "Muse Spark 1.3: Planner, rules, report: picks what to track; turns each definition into a Python rule";
  - "Muse Spark Open: Frame-strip check: double-checks borderline moments from strips of frames".
- Closing:
  - "For every lab that still scores behavior by hand.";
  - "Open source. Try it live.";
  - REPO and LIVE URL boxes, which are placeholders (Aniket fills them in at export);
  - "Apache-2.0. Benchmark data: CalMS21, Sun et al. 2021, CC BY 4.0."

**Transition:** 0.5 s crossfade between the two cards; fade to black over the last 0.5 s.

**Notes for the app:** hold the closing card for at least 4 s so the URLs can be read. End at or before 2:55.

---

## Asset list

### Cards and images, rendered from committed numbers (no model calls)

| Asset | Path | How it is made |
|---|---|---|
| Title card | `docs/video_assets/title.png` (committed) | `uv run python -m video.cards --docs-assets` |
| Results / evidence card | `docs/video_assets/results.png` (committed) | same |
| Model-stack card | `docs/video_assets/model_stack.png` (committed) | same |
| Closing card | `docs/video_assets/closing.png` (committed; URL placeholders) | same |
| Clip-score badge (alpha) | `docs/video_assets/badge_clip_score.png` (committed) | same; re-render after live-run step 2 (Nov 2) |
| Who-it's-for card (CalMS21 annotation) | `data/video/cards/who.png` | `uv run python -m video.cards` |
| Opening shot, 5 s | `data/video/opening.mp4` | `uv run python -m video.anim` |
| Results-beat animation, 20 s | `data/video/results_beat.mp4` | `uv run python -m video.anim` |
| README GIF | `docs/media/etho-tracking.gif` (committed) | `uv run python -m video.anim` |
| Architecture diagram | `docs/architecture.svg` (committed); PNG `data/video/capture/code_architecture.png` | `uv run python -m video.capture.code_assets` |
| Code slides and pan, 17 s | `data/video/capture/code.mp4`, `code_*.png` | `uv run python -m video.capture.code_assets` (reads `data/video/tmp/pytest.txt`) |
| Fly clip, 14 s | `data/video/capture/fly.mp4` | `uv run python -m video.fly_clip` |
| Captions | `docs/demo_captions_v5.srt` (committed) | retime after the voice-over |

The renderer prints the number-to-source map. `uv run pytest video/tests` fails if a card shows a number it can't trace.

### Screen recordings

The app runs on the VM. On the Mac, use the hosted URL for read-only pages, or an SSH tunnel for a local instance:
`ssh -L 8770:127.0.0.1:8770 etho-vm`, then open `http://localhost:8770`. Browser window 1920x1080; presentation mode
on (the link at top right); page zoom 140%; hide bookmarks and extensions; cursor highlighting on.

| ID | What | Steps | Raw length, then cut length |
|---|---|---|---|
| R1 | **Live run** (scene 3) | Lead starts a local live instance on :8770. Open home, paste the three definitions from `configs/calms21_definitions.json` (attack, investigation, mount), upload `data/demo/mouse075_0-300s_h264.mp4`, press score. Record the whole run with the clock visible (about 130 s for the showcase run). Then scroll to the planner card, the overlay video and the timing card. | about 3 min raw, cut to 29 s |
| R2 | **Trace** (scene 5) | Open `/p/sc-calms21-mouse075-live`. Scroll to the ethogram, click the Etho **mount** bout at **37.5-38.0 s**: it is small, zoom in first. Wait for the trace panel, hold on it for 8 s. | about 20 s raw, cut to 9 s. Fallback: `data/video/capture/trace.mp4` |
| R3 | **Correction** (scene 6) | Same page. Click the Etho mount bout at 38.4-44.0 s, choose "wrong, actually attack", and let the pending item appear. Hover "Revise rules". | about 20 s raw, cut to 10 s. Fallback: `data/video/capture/correction.mp4` |
| R4 | Optional: code in your own editor (scene 4) | Open the three files at the excerpt lines (gateway.py 291-326, tier1.py 154-181, sandbox.py 167-267), font 20 pt or more, dark theme, scroll slowly. | 20 s raw, cut to 10 s. Default: `data/video/capture/code.mp4` |

---

## Shot list for Nov 2 (recording order, one session)

1. **Render (VM, lead, 10 min, $0).** Run:
   - `uv run python -m video.cards`
   - `uv run python -m video.cards --docs-assets`
   - `uv run python -m video.anim`
   - `uv run python -m video.fly_clip`

   Then `uv run pytest video/tests`.
2. **Test count (VM, lead, 3 min).** `uv run pytest -q -W ignore -o addopts="" | tail -1 > data/video/tmp/pytest.txt`,
   then `uv run python -m video.capture.code_assets`. Update the "more than six hundred tests" row in DEMO_PLAN's
   sources table with the count.
3. **Copy to the Mac (2 min).** `scp -r etho-vm:work/etho/data/video .`, plus `docs/video_assets/` from the repo.
4. **Browser setup (Mac, 5 min).**
   - Window 1920x1080, presentation mode, zoom 140%.
   - Open `https://<live URL>/p/sc-calms21-mouse075-live`.
5. **R2, trace (Mac, 10 min).** Do two takes. Check that the panel reads "tier 1 said 'attack' (confidence 0.96)" and
   "Decided by: Rules".
6. **R3, correction (Mac, 5 min),** on the same page. Do two takes.
7. **R4, optional editor code (Mac, 10 min).**
8. **Rough cut in the video app (Mac, 30-60 min).** Lay out scenes 1-9 with the assets above, use R2 and R3 (or the
   fallbacks), and the stand-in for scene 3, labeled "precomputed". Check that the total is 2:54 or less.
9. **R1, the live run (Nov 2, approved: local instance, `dev`, up to $0.90).**
   - Lead starts the local live instance on :8770, with a fresh gateway cache so the run is real and not replayed from
     cache, and the job ceiling set to the approved amount. Aniket opens the tunnel and records R1.
   - Same day, lead:
     - scores the run with `eval/showcase_check.py`;
     - re-renders the badge;
     - re-checks the traced bout against the line;
     - updates the voice-over line and captions;
     - commits all of it.
10. **Nov 3:** Aniket records the final voice-over (replacing any app narration); final assembly.
---

## Sources for every number on screen

| Number or text on screen | Scene | Source file |
|---|---|---|
| 0.58 (macro F1); 19 test videos | 1, 5, 7 | `eval/results/score-test-20261028T164333Z-473c64.json` (metrics.arms.zs_t1.macro_f1, n_sequences); approved wording `eval/results/test1_report.md` §11.1 |
| attack 0.449, investigation 0.635, mount 0.656 | 5, 7 | `eval/results/score-test-20261028T164333Z-473c64.json` (zs_t1 behaviors); `eval/results/test1_report.md` §11.1 |
| "Training-free¹: ..." headline and footnote "Tuned on 13 labeled dev videos; scored once on the 19 official test videos" | 1, 7 | `eval/results/test1_report.md` §11.2 (demo-slide version) |
| Supervised 0.79-0.86 (.793, .829, .864) | 7 | `eval/results/test1_report.md` §11.1 (Sun et al. 2021, arXiv:2104.02710, Tables 1 and 2) |
| 0.88× real time, worst 1.54× | 7 | `eval/results/test1_report.md` §11.3 (pooled 0.8777×, max 1.5421×), §11.1 speed line |
| Manual scoring 3-4× (Segalin et al. 2021) | 7 | `eval/results/test1_report.md` §11.1 speed line; DEMO_PLAN sources table |
| $11.25 per hour of video | 7 | `eval/results/test1_report.md` §11.1 cost line |
| this clip 0.42 | 5 | `eval/results/showcase_mouse075_check.json` (showcase.final.macro); **confirm after the Nov 2 live run** |
| Trace: 37.5-38.0 s, tier 1 "attack" (confidence 0.96), set aside, decided by rules | 5 | `data/showcase/calms21-mouse075-live/verdicts.jsonl` (app trace endpoint); bout table in `eval/results/showcase_mouse075_check.md` §5; **confirm after the Nov 2 live run** |
| Traced error 37.5-44.0 s (Etho mount, expert attack) | 5 | `eval/results/showcase_mouse075_check.md` §5 |
| Live-run wall time, cost, stage timings | 3 | **FILL AFTER THE NOV 2 LIVE RUN** (the recorded job's `result.json`); stand-in 2:09 / $0.89 from `data/showcase/calms21-mouse075-live/result.json` |
| pytest count | 4 | a real `uv run pytest` run on Nov 2 (shot 2), recorded in DEMO_PLAN's sources table |
| Commit dates | 4 | `git log` |
| SAM 3.1, Muse Spark 1.3, Muse Spark Open (roles) | 4, 9 | `docs/architecture.svg`; CLAUDE.md model table |
| "movie 4", clock 16:32.0-16:47.0 | 8 | `eval/results/fly_clip_window.json` (rule-fixed window, source frames 29760-30210 at 30 fps); no result number in this scene |
| Spoken only: "0.2 F1" (frame check lift) | 7 | `eval/results/test1_report.md` (zs_t1 − zs_rules +0.205, CI excludes 0) |
| Spoken only: "six frames at low effort" | 7 | `etho/adjudication/tier1.py` FRAMES_PER_STRIP = 6; `configs/frozen_test_v1.json` (effort low) |
