# Escalations to Aniket

Newest at the bottom. Each entry has: the question, a recommendation, whether it blocks work, and
the fallback if there's no reply in 2 hours (non-blocking items only).

## 2026-10-28 (Phase 1 checkpoint)

1. **Test-split SAM pass (about $17.50, over the $5 single-job limit).** The plan is all 19 official
   CalMS21 test sequences at 10 fps with one SAM call each: 87,374 frames, $17.47 from `eval-sam`.
   Test run 2 reuses those cached tracks. *Recommend:* approve when we reach test run 1 (around
   Nov 1); I'll write `ops/spend-plan.md` first. *Blocking:* not yet.
2. **MABe22 license (second-species demo).** The Caltech record has no license field. Its beetle-ant
   videos are only released resized to 224 px (1 GB train zip), and the label format isn't confirmed.
   *Recommend:* email or ask the authors, or treat it as "research use, cite the paper" and show it
   only in the demo, without redistributing frames in the repo. Alternatively, pick a second
   species with a clear license. *Blocking:* no. *Fallback:* explore the data locally only, commit
   nothing derived from it, and keep it out of the demo until the license is resolved.
3. **What the demo claims.** The proposed headline:
   - Etho's **zero-shot** CalMS21 test macro F1 (attack / investigation / mount), shown next to the
     **supervised** 1D-ConvNet baseline (avg F1 .793) and a random floor (.134). It will be clearly
     labeled zero-shot vs supervised.
   - Time and cost per video vs manual scoring at 3–4× the video's duration (MARS, eLife 2021).

   *Recommend:* yes, report F1 honestly even if it's well below .793. The zero-shot, zero-labels
   angle plus cost and time is the story. *Blocking:* no (targets get frozen around Oct 31).

### Answers from Aniket (2026-10-28, ~01:35 ET)
1. **Approved:** full dev-sequence scoring before any UI, starting with the mount tests.
   - **Top priority:** mask-decode speed and parallel SAM calls. Report end-to-end wall-clock per
     video-minute; the target is **≤ 1x real time**, which keeps it clearly faster than the MARS
     3–4x manual figure.
   - Validate the brightness identity rule against the pose keypoints on dev and report the
     identity error rate.
   - **Checkpoint by end of Oct 30 ET:** per-behavior F1 on dev and dev-holdout, speed, and spend.
     If it's at risk, tell Aniket what to cut by Oct 29.
2. **Pre-approved:** one SAM pass on all 19 test sequences, up to $20. Conditions:
   - `EVAL_PLAN.md` targets are committed;
   - the Codex `eval-plan` gate is done;
   - prompts and thresholds are frozen;
   - nothing is tuned on test.

   Write `ops/spend-plan.md` and send a PushNotification when it starts; no need to wait for a
   reply.
3. **Approved headline:** zero-shot test F1 next to the supervised .793 and the random .134, plus
   time and cost vs the 3–4x manual figure.
   - Show per-behavior F1, not only the average.
   - Confirm that .793 uses the same metric and split.
   - Never say "beats supervised" unless it does.
4. **Second species:**
   - Drop MABe22 from anything public.
   - Check the license of Caltech's Fly vs. Fly dataset. If it's open, aim for a measured F1 on
     1–2 behaviors and propose an identity method (brightness won't separate flies).
   - Fallback: the 2026 Scientific Data zebrafish dyads dataset, if its license is open.
   - Write an ADR and get Aniket's approval before committing any second-species data.

## 2026-10-28 ~08:30 UTC: tier-1 Fireworks spend on the test run (over $5, not covered by the pre-approval)
Your pre-approval covers the test **SAM** pass (up to $20). The headline zero-shot arm also needs tier-1
adjudication (Muse Spark Open on Fireworks) on the test windows.
- **Estimate:** about $15 (range $7.5–23), covering both arms. The few-shot arm's shared windows hit the cache.
- **Fireworks after this:** about $25 of the $90 cap.

**Recommend:** approve up to $25 on Fireworks for test run 1.

**Blocking:** only the tier-1 arms. Meanwhile I'll run the SAM pass and the rules-only arms, which are
approved and cost nothing extra. I won't start tier-1 on test until you reply; no fallback, since it's
spend over $5.

## 2026-10-28 ~09:00 UTC: headline wording ("zero-shot")
The Codex eval-plan gate (Astra) says "zero-shot / no labeled training frames" overstates it:
- no labeled example is ever shown to a model, and no model is trained;
- but our prompts, thresholds and cascade rule were chosen by looking at F1 on labeled dev videos.

*Recommend* wording like: **"No labeled examples in any prompt and no model training; pipeline settings
were tuned on 13 labeled development videos (disclosed)."** In short form, "zero-shot behavior
definitions, dev-tuned pipeline". The correction-loop arm is labeled "few-shot (18 corrections)".

*Blocking:* no (it affects the README, demo and report wording, not the test run).
*Fallback if no reply in 2 h:* use the precise wording above in all written material.

### Answers from Aniket (2026-10-28, ~09:20 ET)
1. **Approved:** up to $25 of Fireworks for tier-1 on test. Exactly the frozen settings, one run. Then
   score all four pre-registered arms once and report all four. State how the 18 corrections were
   produced (simulated from dev labels). The correction loop is presented as a feature; no accuracy-gain
   claim unless test shows one. Then the Codex `test-results` gate.
2. **Approved:** ADR-002 (Fly vs. Fly), up to $13.60 from `ablation`. Starts only after test scoring,
   the live-app timing test, and the rule sandbox are done. If wing threat isn't clearly above the
   random floor on train by end of Oct 31 ET, ship it as a qualitative demo clip with no F1 claim.
   Disclose pair-level scoring in the README and cite the dataset (even though CC0).
3. **Headline wording** (replaces 5fb5078): "Training-free: plain-English behavior definitions, no
   labeled examples, no model training." Footnote: "Pipeline settings were tuned on 13 labeled
   development videos; test results are on 19 untouched videos." Demo numbers: test F1 as a share of
   the supervised baseline; speed vs the 3-4x manual figure from the measured full-video end-to-end
   time; $ per hour of video.
4. **Next:** propose a demo-video script plan and an ADR on whether/where the app is hosted publicly
   during judging (public hosting needs approval).

## 2026-10-28 ~17:20 UTC: headline wording, after the Codex test-results gate
The Codex gate (Astra) and our evaluator independently flag two words in the approved wording:
- **"untouched":** no test video, prediction or score was looked at, and nothing was tuned on test. But per-video
  *summaries of test labels* (behavior counts) were read before the run. They shaped which dev videos we chose,
  the 10 fps choice and the targets (disclosed in EVAL_PLAN).
- **"no labeled examples"** can be read as "no labeled data was used at all", but dev labels were used to tune
  settings.

*Recommend*, keeping your structure:
- **Headline:** "Training-free: plain-English behavior definitions, no labeled examples given to the models, no
  model training."
- **Footnote:** "Pipeline settings were tuned on 13 labeled development videos and scored once on the 19 official
  test videos. Test-label summaries (behavior counts) informed dev-set selection and targets; no test predictions
  were used to choose settings."

Codex's own stricter version is in `ops/codex/ask-20261028-165452-293961.md`.

*Blocking:* no. It affects the README, the demo and the report text, not the results.
*Fallback if no reply in 2 h:* use the recommended version above in written material.

## 2026-10-28 ~17:55 UTC: Fly vs. Fly needs more than the approved $13.60 to run your "clearly above random on train" check
- **What we found (evaluator, train labels only):** in the 5-min dev windows, wing threat occurs only in movie 2
  (6 bouts). Movies 1-2 alone can't show "clearly above random". The check needs the spare train movies 3-5
  tracked as well, and ADR-002's $13.60 didn't include that.
- **Spent so far:** $3.71 of `ablation` (probe plus dev movies 1-2 tracked at 30 fps: both flies found in 99.5-99.9% of
  frames, 0.25x real time).
- **Lunge is decided first, on dev movies 1-2,** for about $0.5 of LLM and no new SAM. Then:

| Path | Extra SAM | Fly total (incl. LLM) | `ablation` used |
|---|---|---|---|
| Lunge GO: wing threat and lunge, 5-min windows at 30 fps (train 3-5 $5.47, test $9.12) | $14.6 | **~$19.3** | ~97% |
| Lunge NO-GO, A: wing threat only, 10-min windows at 10 fps (train 1-5 $6.1, test $6.1) | $12.2 | ~$16.4 | ~82% |
| Lunge NO-GO, B: wing threat only, 5-min windows at 10 fps (train 1-5 $3.0, test $3.0) | $6.0 | ~$10.2 | ~51% (within approval) |
| Stop now: qualitative clip, no F1 | $0 | $3.71 | 19% |

- **Risk on path B:** the test windows may hold fewer than 10 wing-threat bouts. The result would then be "not
  evaluable" and we'd make no claim. Train mid5 had 19 bouts over 5 movies.
- **Trade-off:** paths GO and A leave almost nothing in `ablation` for Etho Local (Glimmer distillation). That is a stretch
  goal, and I don't expect to reach it before the Nov 2 freeze.

*Recommend:* approve up to **$19.5 total** for the flies from `ablation` (whichever path lunge selects), and drop
Etho Local. *Blocking:* only the spare-movie tracking. Meanwhile I'll do the lunge decision, the definitions and
the Codex critique of the fly eval plan, all within the approved $13.60. *Fallback if no reply in 2 h:* stay
within $13.60, so path B if lunge is NO-GO; if lunge is GO, wait for your reply.

### Answers from Aniket (2026-10-28, ~13:50 ET)
1. **Fly budget APPROVED:** up to $19.5 total from `ablation`. Etho Local is dropped. The Oct 31 ET time limit and
   the pre-registered go/no-go still apply.
2. **Headline APPROVED in two versions:**
   - README and report: the full wording, including the complete footnote with the test-label-summaries
     disclosure.
   - Demo slide: "Training-free: plain-English behavior definitions, no labeled examples given to the models, no
     model training." Footnote: "Tuned on 13 labeled dev videos; scored once on the 19 official test videos.
     Full disclosure in README."
   - Accuracy line everywhere: "0.58 F1 with no training; supervised methods reach 0.79–0.86", citing all three
     baselines.
   - Speed line: "scores a video unattended in about its own length (0.88× real time on test; worst 1.54×);
     manual scoring takes 3–4×". No "14–18× faster" wording anywhere.
3. **Speed:** explain why test (0.88×) was ~4× slower than dev (0.22×), with a breakdown by stage, and propose a
   cheap fix if one exists. Measure on fresh clips only; never re-score test.
4. **ADR-003 APPROVED with changes:**
   - public read-only showcase;
   - live uploads only behind an access code that goes in the submission notes, not on the public page;
   - kill switch; $3/day and $20 total from `demo-reserve`; auto-off Nov 10 ET;
   - Tailscale Funnel, not a Cloudflare quick tunnel;
   - privacy notice: uploads are processed by Meta in Standard mode (not used for training) and deleted after
     24 h;
   - a separate Unix user: give Aniket the exact sudo commands to run.
5. **Demo plan:** Aniket is reviewing it and will reply separately.

### Answers from Aniket (2026-10-28, ~14:15 ET): demo plan APPROVED with changes
1. **Timing:**
   - 0:00-0:20 hook: problem, "Push the Frontier", and a one-line teaser of the headline number.
   - 0:20-0:35 who it's for and why current tools fall short, with a sourced impact line on who runs these
     assays.
   - 0:35-1:15 live run.
   - 1:15-1:45 results and audit trail, including one visible error traced to its rule and model call.
   - 1:45-2:00 correction loop (~15 s): "researcher stays in control", no gain claim.
   - 2:00-2:30 evidence card: .793/.829/.864, speed with the worst case, $ per video-hour.
   - 2:30-2:45 second species.
   - 2:45-3:00 close: Meta model stack, who it's for, repo and live link.
2. **Sources:** every factual claim in the voice-over needs a source in the repo. "The slowest step" becomes "one
   of the biggest bottlenecks" unless the stronger claim can be cited. Cite or soften "thousands of labeled
   frames".
3. Use the real CalMS21 human annotations rendered as a timeline, not a mock spreadsheet.
4. The live run uses a test video with clear attack and mount. It is recorded live with a visible clock, with a
   labeled precomputed fallback.
5. Aniket does the voice-over. Claude writes the full script (380-430 words) and burn-in captions.
6. Rough cut on Nov 2 (up to $5 of `demo-reserve` unlocked on Nov 2 for it). Final recording Nov 3.
7. Before export, confirm the maximum video length in the Official Rules, that a LICENSE file exists (MIT or
   Apache-2.0; Claude proposes which), and that all honesty checks pass.

## 2026-10-28 ~18:40 UTC: demo clip. No test video has clear attack and mount together
Evaluator, report section 12: in CalMS21 test, attack and mount almost never co-occur. mouse076-079 have no
mount, and mouse075 has one mount bout (1.1 s). Candidates (selection ranked by Etho's own F1, so any clip flatters
Etho; the demo shows pooled numbers, not clip F1):
1. **mouse075, 0-300 s:** 11 attack, 1 mount and 17 investigation bouts. This is the only test clip with both
   behaviors.
2. **Two clips:** mouse076 230-350 s (attack, live), plus mouse072 340-460 s (mount, precomputed and labeled).
3. **A dev video with both behaviors,** labeled "development video (used for tuning)".

*Recommend 1.* It's one live test clip. Its traced error is strong and honest: frames 1124-1304 (37.5-43.5 s).
Etho said mount, the expert said attack. The frame-strip model said "attack" (0.92) and was right, but the frozen
pipeline only lets that check confirm investigation or reject, so the verdict was discarded. That restriction was
chosen on dev (0.482 vs 0.406).

**License:** CalMS21 annotations are CC BY 4.0. Showing the human timeline in the video with credit is fine. The
exported arrays stay uncommitted in gitignored `data/demo/` unless you want them in the public repo.

*Blocking:* no, the rough cut is Nov 2. *Fallback in 2 h:* option 1.

### Answers from Aniket (2026-10-28, ~14:35 ET)
1. **Demo clip APPROVED:** mouse075, 0-300 s. Frame the error as "the trace shows exactly which step overrode the
   right answer". The discarded-verdict issue may be fixed in the app going forward, but never claimed in the
   test numbers.
2. **Video rules:** Aniket is checking the participants page and will send the max length, hosting and format,
   required content, and form fields.
3. **LICENSE APPROVED:** Apache-2.0. The SAM License stays with the decoder code it covers. Add a NOTICE file
   with third-party licenses and dataset credits.
4. **Fly bar:** not what Aniket meant. Record it as an ADR before any fly test-run labels are opened.
   - An on-screen fly F1 claim requires all three: (a) the CI lower bound above the random reference; (b) point
     F1 ≥ 0.40; (c) shown next to the frozen simple baseline's score. Otherwise the fly segment is labeled
     "qualitative".
   - The README reports the simple baselines regardless, and says the 0.989 mask-width figure is in-sample on
     6 bouts.
   - If Etho's rule resembles the width threshold, note that it was derived from the plain-English definition
     with no hand-written threshold.
5. **Evidence card:** "Supervised methods trained on labeled data and given pose keypoints reach 0.79-0.86;
   Etho works from raw video with no training." Keep 0.88x (test) as the speed claim on the card. The live
   run's clock shows whatever the app actually does.
6. **Hosting:** Aniket runs the sudo commands after the merge. Go live only after Nov 3.

### Answers from Aniket (2026-10-28, ~15:05 ET): Official Rules excerpt
1. The live product is a primary judging artifact, so hosting is required.
   - When a cap is hit, the app falls back gracefully to the read-only showcase with a message, never an error.
   - Add a web-app watchdog, and an external uptime check every 15 min that logs to ops/.
   - The app stays up from submission through Nov 9 (finals); live mode still turns off on Nov 10.
2. The video must show "features, code, and functionality built during the Hackathon". Add a 10-15 s code beat
   (architecture plus our cascade and gateway code). Hard limit: 3:00.
3. README section "What we learned about Meta's models", with evidence; the strongest finding gets a line in the
   voice-over.
4. Finals are shown live on a shared screen: large fonts, burned-in captions, even audio.
5. docs/SUBMISSION.md checklist:
   - the repo is public;
   - the video is Unlisted on YouTube, checked logged out;
   - only Aniket is listed;
   - **submit by Nov 4 12:00 PM ET; late submissions are not considered.**
