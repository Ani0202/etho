# Demo video plan (v3: APPROVED by Aniket 2026-10-28; updated for the Official Rules excerpt; script draft for his review)

- **Hard limit 3:00.** Aniket records the voice-over; captions are burned in.
- **Finals are watched live on a shared screen:** large fonts (UI zoom 150% or more, captions at least 48 px at
  1080p), burned-in captions, and even audio levels (normalize the voice-over to about -16 LUFS, no music
  louder than -30 LUFS).
- **The video must show "features, code, and functionality built during the Hackathon".** The code beat
  (1:05-1:20) shows the architecture diagram and our own gateway and cascade code, not only the rules the model
  wrote.
- **Schedule:** rough cut Nov 2 (up to $5 of `demo-reserve` unlocked for it). Final recording Nov 3.
  Submission by Nov 4, 12:00 PM ET (`docs/SUBMISSION.md`).
- **Sources and numbers:** every factual claim in the voice-over has a source (table below). Numbers come only
  from committed files.

## Script (draft v3)
About 420 words (GO fly line), ~2:54 at ~145 words/min, leaving ~6 s of slack under 3:00. Clip: test video mouse075,
0-300 s. Pending: ⟨fly result⟩ (Oct 31 gate; on-screen fly F1 only under ADR-004).

**0:00-0:20, hook** (on screen: two mice; title "Etho · Push the Frontier")
> Labs that study behavior still score it by hand: someone watches every second and writes down what the
> animals do. For social behavior that takes three to four times the length of the video. This is Etho, for
> Push the Frontier. It's training-free, and on a public benchmark it reaches 0.58 F1.

**0:20-0:35, who it's for and the gap** (on screen: the real CalMS21 human annotation as a timeline)
> Behavior scoring runs through neuroscience labs and regulated drug-safety testing. Today's options: a person
> with a stopwatch, or software that needs hand-labeled examples of every behavior. Etho needs a sentence.

**0:35-1:05, live run** (on screen: the app with a visible clock; waits are cut and marked with a "time
skipped" tag; the clock keeps the real elapsed time)
> This is a test video Etho was never tuned on. I paste the dataset authors' definitions of attack,
> investigation and mount. Muse Spark picks what to track, and SAM 3.1 follows both mice, even when one climbs
> on the other. Muse Spark turns each definition into a short Python rule, and Muse Spark Open checks
> borderline moments from strips of frames. The clock is real; we've only cut the waiting.

**1:05-1:20, code beat** (on screen: the architecture diagram, then ~4 s each of `etho/gateway/gateway.py`
(the cap check and cache), `etho/adjudication/tier1.py` (the cascade: strips, verdict, restricted apply) and
`etho/rules/sandbox.py`; a commit-date overlay showing it was written Oct 26-Nov 4)
> Everything around the models we built this week: a gateway that caps every dollar and caches every call,
> the tracking and identity code, the sandbox that runs the model's rules, and the two-tier cascade.

**1:20-1:45, results and audit trail, with one real error** (on screen: Etho's ethogram over the expert's)
> Here's the result beside the expert's annotation. Every label can be traced, including mistakes. At
> thirty-eight seconds Etho says mount; the expert says attack. The frame-strip check answered "attack",
> correctly, but this version only lets it confirm investigation or reject, so that answer was set aside. The
> trace shows exactly which step overrode the right answer.

**1:45-2:00, correction loop** (no gain claim)
> If the researcher disagrees, they mark a few bouts. Etho rewrites the rule and shows exactly what changed. The
> researcher stays in control.

**2:00-2:30, evidence card** (on screen: per-behavior F1 attack 0.449, investigation 0.635, mount 0.656;
supervised .793, .829 and .864; "0.88× real time on test; worst 1.54×; manual scoring takes 3-4×"; $11.25 per
hour of video; slide footnote)
> On the 19 official CalMS21 test videos, scored once, Etho reaches 0.58 F1 with no training. Supervised
> methods trained on labeled data and given pose keypoints reach 0.79 to 0.86; Etho works from raw video. It
> scores a video unattended in about its own length; manual scoring takes three to four times. About eleven
> dollars per hour of video. Our biggest surprise: Muse Spark Open, glancing at six frames at its lowest
> effort, added 0.2 F1.

**2:30-2:45, second species** (GO or NO-GO version, per the pre-registered gate)
> GO: Same pipeline, new animal: Caltech's fighting fruit flies, with wing threat defined in one sentence from
> the literature. No retraining. ⟨F1, shown next to the simple width-threshold baseline⟩.
> NO-GO: Same pipeline, new animal: fruit flies, scored from a one-sentence definition with no retraining. This
> one is qualitative; we haven't measured it to our standard yet.

**2:45-3:00, close** (on screen: Meta model stack, who it's for, repo and live link)
> Etho runs on Meta's models: SAM 3.1, Muse Spark and Muse Spark Open. It's for every lab that still scores by
> hand. It's open source, and live at the link below.

Captions: the script text, one caption per sentence, timed from the recorded voice-over (SRT generated Nov 2).

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

## Production
- **Live run:** test video mouse075, 0-300 s (11 attack, 1 mount, 17 investigation bouts).
  - It is the only test clip with both attack and mount. All 19 per-video results are published, so the choice
    is transparent (test1_report §12).
  - The traced error is at frames 1124-1304 (37.5-43.5 s).
  - The app may get a fix for discarded verdicts later; the test numbers never claim it.
  - Recorded live; waits are cut with a visible "time skipped" tag. If the live run fails on the day, use the
    precomputed run, labeled "precomputed".
- **Human annotation timeline:** the real CalMS21 labels for the clip, rendered like Etho's ethogram, credited
  "CalMS21, Sun et al. 2021, CC BY 4.0".
- **Code beat assets:** an architecture diagram (to draw: `docs/architecture.svg`, matching the six-stage
  architecture in the README), plus syntax-highlighted excerpts of the three files at 150% zoom.
- **Before export:**
  - total length ≤ 3:00;
  - every on-screen number matches a repo file;
  - "training-free" always comes with the footnote;
  - nothing says "beats supervised" or "N× faster";
  - the per-behavior F1s appear next to 0.58;
  - the fly F1 appears only under ADR-004;
  - LICENSE (Apache-2.0) and NOTICE exist;
  - YouTube is Unlisted and checked logged out (`docs/SUBMISSION.md`).
