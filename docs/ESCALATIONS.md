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
