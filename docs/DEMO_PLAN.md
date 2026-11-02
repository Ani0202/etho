# Demo video plan (v5 script: v4 plus Aniket's harder-clip line, 2026-10-30; plan v3 APPROVED 2026-10-28)

- **Hard limit 3:00.** Aniket records the voice-over; captions are burned in.
- **Finals are watched live on a shared screen:** large fonts (UI zoom 150% or more, captions at least 48 px at
  1080p), burned-in captions, and even audio levels (normalize the voice-over to about -16 LUFS, no music
  louder than -30 LUFS).
- **The video must show "features, code, and functionality built during the Hackathon".** The code beat
  (1:04-1:21) shows the architecture diagram and our own gateway and cascade code, not only the rules the model
  wrote.
- **Schedule:** rough cut Nov 2 (up to $5 of `demo-reserve` unlocked for it). Final recording Nov 3.
  Internal submission target Nov 4, 12:00 PM ET; official deadline Nov 4, 9:00 PM ET (`docs/SUBMISSION.md`).
- **Sources and numbers:** every factual claim in the voice-over has a source (table below). Numbers come only
  from committed files.

## Script (v5, 2026-10-30)
v5 = v4 plus Aniket's line in the results beat ("This is one of the harder clips: ..."; showcase option A, 2026-10-30),
with trims elsewhere to stay near 400 words: "actually" and "did it" (results); "On the nineteen official test
videos" -> "On the test set" (the new line already says all 19), "unattended," and ", the biggest gain of any step on
top of the rules" (evidence card); "from a one-sentence definition" -> "from one sentence" (flies).
395 words ("Here," added, Aniket 2026-10-30). At 145 words/min that is 2:43 of speech; the timeline below adds 11.7 s for
on-screen moments and ends at 2:56 (fly beat 15 s at real speed, DECISIONS 2026-10-30 20:29 UTC), leaving 4 s under the
hard 3:00 limit. Clip: test video mouse075, 0-300 s. Retime from the real
recording on Nov 2 (`docs/demo_captions_v5.srt` is a draft at 145 wpm, one caption per sentence, 34 captions;
the v4 file is kept for reference).

Source check of the two new phrases (2026-10-28):
- "more than six hundred tests": supported. `uv run pytest` gives 662 passed (all written during the event).
- "the largest single gain in the pipeline": **adjusted** to "the biggest gain of any step on top of the rules".
  The rules alone go from the 0.134 random reference to 0.375 (+0.240), which is larger than +0.205, so "largest
  single gain" is not supported. Among the steps added on top of the rules, the frame check is the largest
  measured gain (test: +0.205; corrections inconclusive; dev: video tier +0.005, medium effort -0.011).
- "Nothing is a black box." replaced by "No label goes unexplained." (Aniket, 2026-10-29): the models themselves
  are black boxes; what is traceable is each label. 390 words; the beat timings stand.

| Beat | Time | Words | Speech |
|---|---|---|---|
| Hook | 0:00-0:20 | 47 | 19.4 s |
| Who it's for, the gap | 0:20-0:35 | 34 | 14.1 s |
| Live run | 0:35-1:04 | 64 | 26.5 s |
| Code beat | 1:04-1:21 | 38 | 15.7 s |
| Results and traced error | 1:21-1:50 | 71 | 29.4 s |
| Correction loop | 1:50-2:00 | 20 | 8.3 s |
| Evidence card | 2:00-2:26 | 60 | 24.8 s |
| Second species | 2:26-2:41 | 28 | 11.6 s |
| Close | 2:41-2:56 | 33 | 13.7 s |

**0:00-0:20, hook** (on screen: two mice; title "Etho · Push the Frontier"; slide footnote with the headline)
> In behavior labs, someone still watches every second of video and labels it by hand. For social behavior, that
> takes three to four times the video's length. This is Etho, for Push the Frontier: no training, just
> plain-English definitions. On a public benchmark, it reaches 0.58 F1.

**0:20-0:35, who it's for** (on screen: the real CalMS21 human annotation as a timeline)
> Behavior scoring sits underneath neuroscience research and drug-safety studies. Today it takes a person with a
> stopwatch, or software you must first feed hand-labeled examples of every behavior. Etho needs one sentence per
> behavior.

**0:35-1:04, live run** (on screen: the app with a visible clock; waits cut and tagged "time skipped"; the
planner card shows the phrases Muse Spark proposed and the one SAM confirmed)
> This is a test video Etho was never tuned on. I paste the dataset authors' own definitions of attack,
> investigation and mount. Muse Spark decides what to track. SAM 3.1 follows both mice, and keeps them apart even
> when they're tangled together. Muse Spark turns each definition into a short Python rule, and Muse Spark Open
> double-checks the borderline moments from strips of frames.

**1:04-1:21, code beat** (architecture diagram, then `etho/gateway/gateway.py`, `etho/adjudication/tier1.py`,
`etho/rules/sandbox.py`; commit-date overlay; the pytest summary line)
> Around the models, built this week: a gateway that caps every dollar and caches every call, the tracking and
> identity code, a locked-down sandbox for the model-written rules, and the two-step check, with more than six
> hundred tests.

**1:21-1:50, results and one real error** (on screen: Etho's ethogram over the expert's; "this clip 0.44 · all 19
test videos 0.58" next to it; the trace panel)
> Here's Etho beside the expert's labels. This is one of the harder clips: 0.44 here, against 0.58 across all 19
> test videos. Here, mount is where it struggles most. Every label is traceable, including the mistakes. At thirty-eight
> seconds, Etho says mount; the expert says attack. The frame check answered "attack", but a rule in the cascade
> set it aside, and the trace shows exactly which step. No label goes unexplained.

Traced-error contingency (Aniket, 2026-10-28, decided before the run):
- If the live run shows a different real error, use it and rewrite this line to match.
- The number is the Nov 2 live run's (0.44, eval/results/live_run_nov2_check.json; the packaged showcase run scored 0.42).
  The video uses the number of the run it shows.
- If no clear error appears, show the trace from the committed test report, labeled "from the test run".
- Never re-roll runs to get a particular error.

**1:50-2:00, correction loop** (no gain claim)
> Disagree? Mark a few bouts. Etho rewrites the rule and shows you exactly what changed. The researcher stays in
> control.

**2:00-2:26, evidence card** (on screen: macro F1 0.58 with per-behavior attack 0.449, investigation 0.635,
mount 0.656; supervised .793, .829, .864; "0.88× real time on test; worst 1.54×; manual scoring takes 3-4×";
$11.25 per hour of video; slide footnote)
> On the test set, scored once: 0.58 F1, with no training. Supervised methods, trained on labeled data and given
> pose keypoints, reach 0.79 to 0.86. Etho works from raw video, in about the video's own length, for about eleven
> dollars per hour of footage. Our biggest surprise: Muse Spark Open, looking at six frames at low effort, added
> 0.2 F1.

**2:26-2:41, second species** (qualitative clip; no F1 on screen; wing gate NO-GO, eval/flies_decision.json)
> Same pipeline, new animal: fruit flies, from one sentence, with no retraining. It's a preview: it
> hasn't cleared the confidence bar we set, so there's no number here.

**2:41-2:56, close** (on screen: Meta model stack, who it's for, repo and live link)
> Etho runs on Meta's models: SAM 3.1, Muse Spark and Muse Spark Open. It's for every lab that still scores
> behavior by hand. It's open source. Try it live at the link below.

Captions: one per sentence, burned in (draft timings in `docs/demo_captions_v5.srt`).

## Live-run order of operations (Aniket, 2026-10-30; steps 1-3 on Nov 2, step 4 on Nov 3)
1. Nov 2: record the live run (local instance, `dev` tag, up to $0.90, approved 2026-10-30).
2. Nov 2: recompute that run's score with `eval/showcase_check.py` (labels only, no model calls, $0).
3. Nov 2: update the voice-over line ("0.44 here", was "0.42 here", per-behavior if shown) and the captions with that number. Also check the
   traced-error line against that run's trace: the clicked bout's first visible verdict must be the one the line
   describes (in the showcase run, the 37.5-38.0 s bout: tier 1 "attack", set aside; the 38.4-44.0 s bout opens with
   tier 1 agreeing "mount", so it does not match). If it doesn't match, apply the traced-error contingency above. If the score no
   longer makes it one of the harder clips (25th-percentile rule in DECISIONS 2026-10-30), flag it to Aniket before
   step 4. The showcase page note follows the run the page shows.
4. Nov 3: Aniket records the final voice-over; final assembly.

## Sources for every claim (all in the repo or cited)
| Claim | Source |
|---|---|
| Scored by hand; "generally done through manual annotation, a highly time-consuming and tedious process" | Segalin et al. 2021, eLife, MARS, doi:10.7554/eLife.63720, Abstract |
| 3-4× the video's length | Same paper, Introduction: "Scoring for social behaviors often takes human annotators 3–4× the video's duration to annotate" |
| Regulated drug-safety testing evaluates behavior | ICH S7A (2000), §2.7.1: "Motor activity, behavioral changes, coordination, sensory/motor reflex responses and body temperature should be evaluated. For example, a functional observation battery (FOB), modified Irwin's, or other appropriate test can be used." OECD TG 424 (1997) covers neurobehavioural effects in rodents |
| Supervised software needs hand-labeled examples | MARS: "We trained classifiers on 6.95 hr of video annotated by a single individual" (Results). DeepEthogram (Bohnslav et al. 2021, eLife, doi:10.7554/eLife.63377): >90% accuracy "with only 80 example frames of a given behavior". "Thousands of frames" is not claimed |
| SAM follows both mice through mounts | docs/SPIKE.md; DECISIONS 2026-10-28 chunk-length table (100% two-animal coverage during mount at 60 s chunks) |
| 0.58 F1; 0.79-0.86; per behavior; speed; $/h | eval/results/test1_report.md §11.1, §11.9 (Sun et al. 2021, CalMS21, arXiv:2104.02710, Tables 1, 2, 8) |
| "about its own length" | test1_report §11.3 (0.88× pooled, worst 1.54×) |
| Biggest surprise: 6 frames, lowest effort, +0.2 F1 | test1_report (zs_t1 − zs_rules +0.205, CI excludes 0); tier1.FRAMES_PER_STRIP = 6; effort low (configs/frozen_test_v1.json); README "What we learned" |
| Gateway caps every dollar and caches every call | etho/gateway/ (ledger, caps, cache); CLAUDE.md budget caps |
| Fly definitions from the literature | configs/flies_definitions.json (Chen et al. 2002, PNAS, Table 1) |
| "More than six hundred tests" | `uv run pytest`: 662 passed (2026-10-28) |
| (v4 only, cut in v5) "Biggest gain of any step on top of the rules" | test1_report §11 paired differences: tier-1 lift +0.205 (CI excludes 0); fs_t1 vs zs_t1 inconclusive; dev video tier +0.005 and medium effort -0.011 (README "What we learned") |
| Fly "hasn't cleared the confidence bar we set" | ADR-004; eval/flies_decision.json (wing CI lower 0.009 <= random reference 0.043) |
| "One of the harder clips: 0.44 here, against 0.58 across all 19"; "here, mount is where it struggles most" | eval/results/live_run_nov2_check.json (the Nov 2 live run shown in the video, job j46d77dd8aee9): macro 0.4376 (attack 0.5695, investigation 0.7431, mount 0.0000), below the 25th percentile of the 19 per-video macros (0.4650) under every p25 definition; pooled test macro 0.5800 (test1_report §11.1). Rounded to 2 decimals. The packaged showcase run scored 0.4193 (showcase_mouse075_check.json) |
| Traced error at 37.5-38.0 s in the live run | eval/results/live_run_nov2_check.json mount_fragments: Etho mount, expert attack (100%), tier 1 "attack" (0.95) set aside by the restriction |
| "Keeps them apart even when they're tangled together" | test1_report: identity error 0.29%; DECISIONS chunk-length table (both mice labeled on 100% of mount frames) |

## Production
- **Live run:** test video mouse075, 0-300 s (11 attack, 1 mount, 17 investigation bouts).
  - It is the only test clip with both attack and mount. All 19 per-video results are published, so the choice
    is transparent (test1_report §12).
  - The traced error is at frames 1124-1304 (37.5-43.5 s).
  - The app may get a fix for discarded verdicts later; the test numbers never claim it.
  - The rules stage takes about 0.7 s on camera: the dataset authors' exact definitions reuse the rules Muse Spark
    wrote for the benchmark (configs/detectors/R1_zero_shot.json). Shown as is, and the voice-over line stays
    accurate (Aniket, 2026-10-29).
  - Recorded live; waits are cut with a visible "time skipped" tag. If the live run fails on the day, use the
    precomputed run, labeled "precomputed".
- **Human annotation timeline:** the real CalMS21 labels for the clip, rendered like Etho's ethogram, credited
  "CalMS21, Sun et al. 2021, CC BY 4.0".
- **Code beat assets:** the architecture diagram `docs/architecture.svg` (done 2026-10-28, 1920x1080:
  five stages, the correction loop, and the gateway under every model call), plus syntax-highlighted excerpts of the three files at 150% zoom.
- **Before export:**
  - total length ≤ 3:00;
  - every on-screen number matches a repo file;
  - "training-free" always comes with the footnote;
  - nothing says "beats supervised" or "N× faster";
  - the per-behavior F1s appear next to 0.58;
  - the fly F1 appears only under ADR-004;
  - LICENSE (Apache-2.0) and NOTICE exist;
  - YouTube is Unlisted and checked logged out (`docs/SUBMISSION.md`).
