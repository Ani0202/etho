# Etho evaluation plan (DRAFT, not committed, targets not frozen)

Owner: evaluator subagent. Status: **DRAFT v1, 2026-10-28** (v1 adds the claim wording, disclosures,
reporting rules, baseline confirmation, and the scorer and identity definitions as implemented in
`eval/score.py` and `eval/identity.py`). Nothing here is binding until this file is committed. Targets are
marked **TBD, proposed: ...** and must be frozen in a commit **before the first test-split run** (CLAUDE.md,
"Evaluation integrity"). Changing this file after a test run requires a dated amendment section at the bottom,
never an in-place edit of a frozen item.

## 1. What we are claiming and what must be measured

**Claim wording.** Etho uses **no labeled training frames**: behavior definitions are written in English, no
model is trained or fine-tuned on CalMS21 labels, and no labeled frame is shown to any model as an example.
"Zero-shot" in any Etho material means exactly this and must be accompanied by the disclosure below.

**Disclosure (must accompany every reported number).**
- *Dev tuning.* Prompts, definitions wording, thresholds, reasoning effort, SAM phrases, and the overlap priority
  are chosen by developers by scoring against expert labels on the **dev** split (8 official-train sequences,
  36,980 frames, 20.5 min) and checked at milestones on **dev-holdout** (5 sequences, 28,916 frames). This is
  human-in-the-loop hyperparameter selection on labeled data; it is not training, but it is label use, and we say so.
- *Test-label aggregates informed the split design.* Before any test run, test labels were read (never viewed
  frame by frame, no test video, keypoints, or model output inspected) to compute per-sequence aggregates: frame
  counts per behavior, bout counts, and the short-bout histogram in section 3. These aggregates shaped dev
  selection (the "long attack sequence" constraint exists because all test attack is in 5 sequences of 17-20k
  frames at ~5% prevalence), the option-B subset, and the 10 fps decision. No test aggregate was used to tune
  any prompt, rule, threshold, or model setting.
- *Etho Local* (if built) is distilled from Muse Spark judgments on unused-train sequences, never human labels.

"Push the Frontier" (docs/PROBLEM_STATEMENTS.md) also requires measured **time and
cost per video** next to accuracy, compared against a **cited** manual-scoring baseline.

Evaluation answers four questions:
1. How well does zero-shot Etho agree with expert frame labels (CalMS21 Task 1)?
2. How does that compare to a supervised model trained on the 70 labeled train videos, and to human agreement?
3. How much does each adjudication tier add, and at what cost?
4. What does one video cost in dollars and wall-clock, versus manual scoring?

## 2. Data and splits

Benchmark: CalMS21 Task 1 (Sun et al., 2021), resident = black mouse (keypoint mouse 0), intruder = white
mouse (mouse 1). Labels: attack, investigation, mount, other; one label per frame, single annotator
(`annotator1`), 30 fps, 1024x570. Videos fetched per sequence by HTTP range; raw data never committed.
License: CalMS21 is **CC BY 4.0** (Caltech data record API, https://data.caltech.edu/api/records/s0vdx-0k302,
`metadata.rights` id `cc-by-4.0`). Citation for the README: Sun et al., "The Multi-Agent Behavior Dataset:
Mouse Dyadic Social Interactions", arXiv:2104.02710 (NeurIPS 2021 Datasets & Benchmarks).

Split file: `eval/splits/calms21_v1.json` (generator `eval/make_splits.py`, seed `20261028`; tests in
`eval/tests/test_splits.py`). Draft sha256: `59bfcd47f5801757efe73cbe7276d1990b15fa2f945cf96e5bf4f001ede2880b`.

| Split | Source | Seqs | Frames @30fps | Minutes | Attack fr (seqs, bouts) | Investigation fr (seqs, bouts) | Mount fr (seqs, bouts) |
|---|---|---|---|---|---|---|---|
| dev | official train | 8 | 36,980 | 20.5 | 2,798 (3, 19) | 10,020 (8, 117) | 2,599 (6, 24) |
| dev-holdout | official train | 5 | 28,916 | 16.1 | 1,965 (2, 40) | 5,235 (5, 124) | 1,850 (5, 27) |
| test (option A) | official test, all | 19 | 262,107 | 145.6 | 12,630 (5, 258) | 61,275 (19, 1,068) | 31,848 (14, 195) |
| test subset (option B, fallback only) | official test | 8 | 139,569 | 77.5 | 12,630 (5) | 25,532 (8) | 16,563 (4) |
| unused train | official train | 56 | rest | | | | |

Bout counts are maximal runs of a label in the 30 fps annotation.

- **dev** (`mouse008, 016, 021, 029, 030, 039, 041, 050`): all prompt, threshold, effort, and phrase tuning.
  Includes the two spike sequences already inspected (mouse016 frames 2400-3000, mouse050 frames 0-600),
  forced into dev so they can't contaminate dev-holdout.
- **dev-holdout** (`mouse004, 034, 037, 047, 070`): scored only at milestones (proposed: at most once per
  pipeline version, and once right before freezing the test config). Not tuned on. A gap between dev and
  dev-holdout F1 is our overfitting signal. Every look is logged.
- **test**: the 19 official test sequences. No test video, keypoint, overlay, or model output is inspected
  before test run 1. Test labels have been read only to compute the per-sequence aggregates disclosed in
  section 1 (counts in the table above, bout counts, the short-bout histogram in section 3).
- **unused train**: available for Etho Local distillation labels (Muse Spark judgments only, never human
  labels) and for free keypoint-only experiments. Never used for dev-style tuning without moving it to dev
  in a new split version.
- `mouse036` (96 frames, 3.2 s) excluded from the pool as too short.

**Selection rule** (exact constraints in the JSON): seeded random search over the 69 eligible train
sequences; accept the first dev set with 30,000-37,500 frames (2-phrase SAM pass under $5), >= 2,500 attack,
>= 2,500 mount, >= 8,000 investigation frames, each behavior in >= 3 sequences, and >= 1 long (>= 10k frame)
sequence with >= 500 attack frames. The last constraint exists because all 5 test attack sequences are
17-20k frames long with ~5% attack prevalence; a dev set of only short attack-dense clips would overstate
attack precision. Dev-holdout: 20,000-30,000 frames, same idea with lower floors.

**Known limitations of the split.** Dev has only 19 attack bouts and 24 mount bouts, so dev per-behavior F1
is noisy (one bout can move attack F1 by several points). Dev prevalences (attack 7.6%, mount 7.0%) differ
from test (attack 4.8%, mount 12.2%); test has proportionally twice as much mount. All test attack comes from
5 consecutive sequences (mouse075-079), so attack test F1 rests on 5 videos.

### Test option A vs B (recommendation: A)

Option A (all 19) is the only option directly comparable to published CalMS21 numbers. Option B (all 5
attack sequences + mount-richest others until >= 40% of test mount frames) halves SAM cost but isn't
comparable to the published baseline and keeps only 4 of 14 mount sequences. With the 1-phrase SAM
configuration chosen in the spike (section 8), A fits the budget, so B is not expected to be needed.

## 3. Prediction format and frame mapping

- Etho runs at 10 fps: sample `k` is source frame `3k` (frames 0, 3, 6, ...). Verified on the two local dev
  videos that the mp4 frame count equals the annotation length (mouse016: 3,692 = 3,692; mouse050:
  1,283 = 1,283). Not yet verified for other videos, nor that frame 0 aligns with label 0 (see open questions).
- Prediction directory, one file per sequence (implemented in `eval/score.py`; full spec in its docstring):
  - `<seq>.jsonl`: bouts `{"behavior", "start_frame", "end_frame"}` on native 30 fps indices, **end exclusive**,
    rasterized exactly as given. The sample rate used must be declared (`{"meta": {"sample_fps": 10}}` line or
    `manifest.json`); a file without it fails.
  - `<seq>.npz`: `labels` int array in CalMS21 coding (attack 0, investigation 1, mount 2, other 3), per 30 fps
    frame or per sample, plus `sample_fps`. Per-sample labels: 30 fps frame `f` takes sample
    `min(round(f/step), ceil(T/step) - 1)` (nearest sample; `3k+1 -> k`, `3k+2 -> k+1`); if fewer samples than
    that were emitted, the missing tail is "other".
  - `manifest.json` (optional): `sample_fps`, `config`, `cost`, `wall_clock_s`, `calls`, `notes`, copied to the log.
  - Product guidance: under the nearest-sample rule, sample `k` covers frames `3k-1 .. 3k+1`, so a bout of samples
    `k1..k2` (inclusive) corresponds to frames `[max(0, 3*k1 - 1), 3*k2 + 2)`. Emitting `[3*k1, 3*k2 + 3)` instead
    shifts every bout by one frame; emitting per-sample npz avoids the question.
- **Exclusive labels for the primary metric.** CalMS21 frames have exactly one label. If Etho predicts
  overlapping behaviors on a frame, a fixed priority resolves it (proposed: mount > attack > investigation;
  the order is set on dev and frozen with the test config). Frames with no behavior are "other".
- Frames where tracking failed, or past the end of predictions, count as "other". Nothing is dropped from
  the denominator.
- 10 fps loses little: in test, only 1 of 258 attack bouts, 19 of 1,068 investigation bouts, and 0 of 195
  mount bouts are <= 3 frames (0.1 s). At 5 fps (<= 6 frames) the losses grow to 14, 69, and 3 bouts, which is
  a measured reason not to subsample below 10 fps.

## 4. Metrics

### 4.1 Primary: frame-level F1 (CalMS21 Task 1 convention)
- Per behavior (attack, investigation, mount): precision, recall, F1, one-vs-rest at 30 fps, **pooled over all
  frames of all sequences in the split**.
- **Macro F1** = unweighted mean of the three F1s, excluding "other". This is the CalMS21 "average F1".
  Pooling convention still to be checked against the official CalMS21 evaluation code (open question 1);
  if it differs, we follow the official code and state it.
  **Confirmed from the paper text** (CalMS21, arXiv:2104.02710 v4, Section E and Table 1,
  https://arxiv.org/html/2104.02710v4; retrieved by a scout subagent, quotes forwarded by the lead, not re-read by
  the evaluator): "true positives (TP) is the number of frames that a model correctly labels as positive for a
  class"; "we use an unweighted average across behavior classes", "omitting the 'other' category"; "the metrics
  should be computed on the entire test set with all the videos concatenated into one sequence". Our pooled
  per-behavior F1 and unweighted macro over attack/investigation/mount match this. The official scorer code (AIcrowd
  GitLab) was not retrievable, so zero-division and tie conventions are ours: P, R, or F1 with a zero denominator
  is 0.0 (sklearn `zero_division=0`).
- Uncertainty: 95% CI by **sequence-level bootstrap** (resample sequences with replacement, 10,000 reps,
  seed 20261028, percentile interval; each resample pools the drawn sequences' TP/FP/FN). With 5 attack
  sequences, attack CIs will be wide; we report them anyway. Resamples in which a quantity is 0/0 are dropped
  for that quantity and their fraction is logged (`*_undefined_frac`); resamples that draw no ground-truth
  frames of a behavior but have false positives give F1 = 0, as pooling implies, and their fraction is logged
  (`ci95_no_gt_resample_frac`). On dev, attack is in 3 of 8 sequences, so about (5/8)^8 = 2.3% of resamples
  contain no attack.
- **Reporting rule.** Macro F1 is never shown alone: every table, chart, sentence, README line, and demo frame
  that shows a macro (average) F1 shows the three per-behavior F1s next to it, each with its CI where one exists.
  The CLI report (`eval.score`) prints them together.
- Secondary: per-sequence F1 table (mean and median over sequences that contain the behavior), confusion
  matrix over the 4 classes, and one-vs-rest F1 *without* the exclusivity rule (labeled as such).
- MAP (the other CalMS21 metric) only if Etho emits a per-frame confidence; otherwise reported as "not
  applicable" rather than computed from binary outputs.

### 4.2 Bout-level and ethogram metrics (cheap; computed from the same predictions)
- Bout matching per behavior: one-to-one greedy match by temporal IoU, threshold 0.3 (proposed; 0.5 also
  reported). Bout precision, recall, F1.
- Per-video summary statistics researchers actually report: total duration, bout count, and latency to first
  bout per behavior. Agreement with ground truth: mean absolute error and Pearson r across test videos,
  plus ICC(2,1) (proposed). These measure whether the *scientific readout* is right, which can hold even
  when frame F1 is moderate.

### 4.3 Tracking quality and identity (SAM 3.1 vs CalMS21 keypoints), `eval/identity.py`
CalMS21 keypoints are MARS pose-estimator outputs, not human ground truth (mean keypoint confidence on test
~0.67-0.74), so these are agreement-with-reference metrics. This section validates the brightness identity
rule (ADR-001: one `"mouse"` SAM call, resident/intruder from mask intensity) on dev, before any UI work.

Input: per sequence, a per-frame table (`frame` at 30 fps, `track_id`, `label` in {resident, intruder, unknown},
`cx`, `cy`, `area`, `present`), npz or parquet. Evaluated on the 10 fps grid (frames 0, 3, 6, ...); a grid frame
with no row counts as zero detections, so missing output lowers coverage instead of being skipped.

- **Keypoint centroid**: mean of all 7 keypoints (nose, ears, neck, hips, tail base). The tail base is on the
  rump, and the tail itself has no keypoint, so the "tail bias" seen with bounding boxes doesn't apply to mask
  centroids. Dropping the tail base moves the reference toward the head: it goes from about 46% to about 37% of
  the nose-to-tail-base length, by keypoint geometry, while a mask area centroid should sit near mid-body. The
  resident's implant and cable pull its mask toward the head, which could favor `no_tail` for the resident. So
  every dev identity run logs the median track-to-keypoint distance for `all7`, `no_tail`, and `torso` (neck,
  hips, tail base). The definition is fixed on dev from that evidence and frozen with the test config.
- **Eligibility**: a keypoint mouse is eligible on a frame if its mean keypoint score is >= 0.5. Measured on dev
  (`sanity:keypoint-tracks` run): both mice are eligible on 0.800 of samples; the resident (implant) is
  ineligible on ~16% of frames, the intruder on ~5%.
- **Matching** (label-blind): per frame, present tracks are matched one-to-one to the eligible keypoint mice by
  position only, maximizing the number of matches within a **60 px gate**, then minimizing total distance.
  Labels are compared only afterwards. Gate rationale (dev keypoints): median nose-to-tail-base length is 183 px
  (10th-90th percentile 141-225), so 60 px is about 1/3 of a body length. The 5th percentile of the inter-mouse
  centroid distance is 33 px during attack and 37 px during mount, and its median is 99 px (attack), 74 px
  (mount), and 117 px (investigation). Spurious extra tracks stay unmatched.
- **Ambiguity**: a match is ambiguous if the swapped assignment is also inside the gate and costs < 10 px more
  (centroids nearly coincide). With perfect tracks (keypoint centroids as tracks), 0.6% of mount samples and
  ~0% of others are ambiguous on dev.
- Reported overall and per ground-truth behavior (attack, investigation, mount, other), per video and pooled:
  - **identity error rate**: fraction of matched (mouse, sample) pairs with a resident/intruder label where the
    label disagrees with the matched keypoint mouse (mouse 0 = resident). Reported on all matches and on
    unambiguous matches. `unknown` labels are not errors; they are counted separately;
  - **swap events per video-minute**: grid samples at which at least one physical (keypoint) mouse's known label
    differs from its previous known label (unknown and unmatched samples are skipped). A full swap counts once;
    a single mislabeled sample counts twice (flip and flip back). Also reported on unambiguous matches only;
  - **coverage**: fraction of samples with both a resident-labeled and an intruder-labeled track present; and
    fraction of samples, among those where both keypoint mice are eligible, on which both mice are matched with
    correct labels;
  - **unknown rate**: fraction of present track-samples labeled unknown;
  - **detections**: fraction of samples with 0, 1, 2, and 3+ present tracks;
  - **localization**: median matched centroid distance (px) per animal. Keypoint containment in masks needs
    the masks and is not implemented (the tracking table carries no mask).
- **Frame-alignment check** (open question 3): per video and animal, for lags L = -10..+10 source frames, the
  median distance between the labeled track at video frame f and its keypoint mouse at frame f + L. Only frames
  where that mouse moves >= 1 px/frame are used, because stationary stretches match at every lag. We report the
  best lag, the median at the best lag and at 0, and the margin to the runner-up. Sign: best L > 0 means label
  index = video index + L. On perfect synthetic and keypoint-derived tracks the check recovers the planted lag,
  with a 1-frame margin of 3-13 px on dev geometry.

### 4.4 Cost and time per video (required by docs/PROBLEM_STATEMENTS.md)
- Dollars per video and per minute of video, split by stage (SAM, planner, rule synthesis, tier-1, tier-2,
  report) and by provider, summed from `ops/spend.jsonl` entries tagged with the run id.
- Report **uncached** cost: for cache hits, count the cost of the original call and label the number. Also
  report the marginal cost of the run as executed.
- Wall-clock per video, end to end (video fetch excluded and reported separately), with concurrency stated,
  plus per-stage latency. Machine: this VM (spec to be recorded).
- Manual-scoring baseline: **3-4 minutes of annotator time per minute of video**. Source: Segalin et al.,
  "The Mouse Action Recognition System (MARS)...", eLife 2021 (https://elifesciences.org/articles/63720),
  Introduction, verbatim: "Scoring for social behaviors often takes human annotators 3–4× the video's
  duration to annotate; for long recordings, there is also risk of drops in annotation quality due to
  drifting annotator attention." Caveat to state wherever this is used: the MARS paper gives no citation
  for the 3-4x figure itself. Report the range (3x and 4x), not a midpoint chosen in our favor. For the
  145.6 min test set (262,107 frames / 1,800) this corresponds to 436.8-582.4 annotator-minutes
  (7.28-9.70 h), rounded down.

## 5. Comparisons (clearly labeled)

| Reference | Supervision | Number | Status |
|---|---|---|---|
| CalMS21 Task 1 baseline, 1D ConvNet on raw pose (28 values/frame, +/-100-frame window) (Sun et al. 2021, Table 1) | **Supervised**, trained on the 70 official train sequences (507,738 frames per the lead; frame count not re-verified) | average F1 .793 +/- .011 (std over 5 training runs), MAP .856 | **Metric and split confirmed** (paper text, section 4.1): frame-level, pooled over the full official test set, unweighted mean over the 3 behaviors, so it is directly comparable to our test option A. **Per-class F1 unconfirmed** (appendix not retrieved): never print per-class baseline numbers until confirmed. Evaluator has not re-read the PDF |
| Other CalMS21 Task 1 results, context only (arXiv:2603.07279, 2026, as reported by a scout; evaluator has not read it, and its split and metric are not checked) | Supervised: MARS features 0.878, pose coordinates 0.593. Unsupervised clusters mapped to labels: MARS 0.701 | macro F1 | None is zero-shot; the cluster mapping uses labels. No zero-shot CalMS21 result was found. Not cited in public material until checked |
| Inter-annotator agreement (MARS, Segalin et al. 2021, eLife, per CalMS21) | Human vs human | TBD | The MARS main text gives no F1 values; they are in Figure 5, figure supplements 3-4, not yet retrieved. Note: our test labels are single-annotator, so this is context, not a computed ceiling on our test set |
| Frequency-matched random predictor (computed, `eval.score.random_baseline`) | None (uses the split's class frequencies) | expected F1 = pooled prevalence. dev: attack .0757, investigation .2710, mount .0703, macro .1390. dev-holdout: .0680, .1810, .0640, macro .1043. test: .0482, .2338, .1215, macro .1345 | dev confirmed by the scorer (`sanity:oracle-10fps` run); dev-holdout and test from split-file counts |
| 10 fps oracle (computed) | Ground truth subsampled to every 3rd frame, upsampled by nearest sample | dev: attack .995, investigation .992, mount .995, macro .994 | Ceiling imposed by 10 fps sampling alone (`sanity:oracle-10fps`) |
| All-"other" predictor | None | F1 = 0 for every behavior | Trivial |

Every chart or sentence that puts Etho next to the supervised baseline must say "zero-shot" (with the
section 1 disclosure) and "supervised" on the respective numbers.

**Comparison rules.**
- Never claim Etho "beats", "matches", or "outperforms" a supervised result unless the test-split number does so
  with the **whole 95% CI** above (or, for "matches", overlapping) the supervised point estimate, on the same
  metric and split. Never base such a claim on dev or dev-holdout, on a single behavior chosen after the fact,
  or on per-class baseline numbers we haven't confirmed. Otherwise the wording is the measured gap, e.g.
  "zero-shot macro F1 X (attack a, investigation i, mount m) vs .793 for a supervised model trained on 70 labeled videos".
- Per-behavior F1 always appears next to any average (section 4.1).
- Context-only numbers (rows marked unverified) are never shown in the demo or README.

## 6. Ablations

All ablations on test use the run(s) in section 7; no extra test runs.

1. **Adjudication cascade** (headline ablation): (a) rules only, (b) rules + tier-1 frame strips (Fireworks
   Muse Spark Open, low effort), (c) rules + tier-1 + tier-2 video (Baseten Muse Spark Open or Muse Spark 1.3,
   high). All three arms are derived from **one** pipeline execution: every candidate bout's rule decision,
   tier-1 verdict, and tier-2 verdict are logged, and arm (b) resolves tier-1 "unsure" bouts with the rule
   decision. For each arm: macro and per-behavior F1, cost, wall-clock, number of bouts judged per tier,
   and tier-1 escalation rate. Per-tier accuracy on the bouts it judged is reported too.
2. **Tier-1 effort low vs medium**: dev only.
3. **SAM 1 phrase ("mouse", identity from mask intensity) vs 2 phrases ("black mouse" + "white mouse")**:
   the spike chose 1 phrase (docs/SPIKE.md). A 2-phrase comparison, if run at all, is dev only.
4. **Keypoint-oracle features** (proposed, $0 SAM): run the same synthesized rules on features computed from
   CalMS21 keypoints instead of SAM masks, to separate tracking error from rule error. Labeled as using a
   pose estimator Etho doesn't run. Requires a keypoint-to-feature adapter in `eval/` (evaluator to build).
5. **Correction loop** (labeled "k corrected bouts", not zero-shot): simulate a user correcting k = 5 bouts
   (proposed selection: seeded random among Etho's errors) on the first half of each video; revise the rule;
   score the second half. Dev and dev-holdout first; on test only inside a planned test run.
6. **Etho Local** (stretch): distilled Glimmer as tier-1 replacement, measured on test in a planned test run.
   Distillation labels come only from Muse Spark judgments on **unused train** sequences (never dev,
   dev-holdout, test, or human labels).

Ablations that don't help are reported.

## 7. Test-run protocol

- **At most two test runs**, both reported. A "test run" is any execution that loads test behavior labels or
  computes any metric (including tracking metrics) on test outputs. A run that crashes before loading labels
  may be restarted; it is still logged as `status: failed`.
- **Pre-conditions for run 1**: this plan committed with frozen targets; test config frozen and committed
  (commit hash, English definitions, synthesized rule code hashes, thresholds, priority order, models and
  efforts per stage, SAM phrases, fps); dev-holdout scored on that exact config; cost estimate written and,
  because it exceeds $5, escalated via `ops/spend-plan.md` (lead's file).
- **No peeking**: no one inspects test videos, overlays, or outputs before run 1. Automated failure checks
  (API errors, empty masks) are allowed and logged.
- **Integrity**: predictions are written to disk and their sha256 recorded in the run log *before* the scorer
  opens the labels. Product code (`etho/`) never reads labels.
- **Run 2** is reserved for a fixed bug or a final config. Its changes vs run 1 are listed in the log.
  Pre-declared: the headline number is the **last** run, always shown with run 1 beside it. We never pick the
  better of the two.
- SAM tracking of test videos is cached by input hash; run 2 reuses run 1's tracks unless the tracking config
  changed (which would cost another full SAM pass, see section 8, and needs a fresh estimate).

## 8. Budget and cost estimates (no money spent by this plan)

SAM 3.1 video: $0.20 per 1k billed frames, 10 fps (every 3rd frame), min 50 frames per request (no
eligible sequence is that short, so the minimum doesn't bite unless videos must be chunked).

| Set | Frames billed @10fps | Video min | SAM 1 phrase | SAM 2 phrases |
|---|---|---|---|---|
| dev | 12,329 | 20.5 | $2.466 | $4.932 |
| dev-holdout | 9,639 | 16.1 | $1.928 | $3.856 |
| test A (all 19) | 87,374 | 145.6 | $17.475 | $34.950 |
| test B (8 seqs) | 46,525 | 77.5 | $9.305 | $18.610 |
| unused train (56), for distillation | 147,268 | | $29.454 | $58.907 |

**Spike decision (docs/SPIKE.md): one SAM phrase ("mouse") per video, with resident/intruder identity
from mask intensity. The 1-phrase column applies.** The 2-phrase rows are kept for reference only.

Fit against the `eval-sam` tag ($40), assuming dev is tracked twice (one phrase change) and holdout once:
- 1 phrase, test A: 2 x 2.466 + 1.928 + 17.475 = **$24.34**, leaving $15.66. A second full test SAM pass
  (17.475) would not fit; run 2 must reuse run-1 tracks.
- 2 phrases, test A: 2 x 4.932 + 3.856 + 34.950 = **$48.67**: over the tag. Even dev once + test A = $39.88.
  **Not feasible without an ADR moving budget** (escalation).
- 2 phrases, test B: 9.864 + 3.856 + 18.610 = **$32.33**: fits, at the cost of comparability.
- Distillation SAM on all unused train exceeds the `ablation` tag ($20) even with 1 phrase: use a subset.
- Any single test SAM pass is over $5, so it needs `ops/spend-plan.md` and escalation in every option.
- If SAM requires chunking long videos with overlap, cost rises by the overlap fraction (to be measured).

LLM costs (to be measured on dev before any test estimate is final): rule synthesis is 3 calls per config
(Meta `eval-llm`). Tier-1 volume scales with candidate bouts; test has 1,521 ground-truth bouts. **Rough,
unmeasured** assumption of ~3k input / ~400 output tokens per frame-strip call gives ~$0.0055 per call, so
3,000 candidates fully adjudicated would cost ~$16 per test run on Fireworks (cap $90). Tier-2 video token
cost is unknown; measure on dev.

## 9. Run logging: `eval/results/runs.jsonl`

Append-only, never edited, including failed, aborted, disappointing, and sanity runs. Each execution writes
**two** records with the same `run_id`: a `status: "started"` record (predictions/tracks sha256, config, git
state) appended **before any label file is opened**, then a final `ok` or `failed` record with the metrics or the
error, and `labels_loaded`. `kind` is `score` or `identity`. The test guard (`eval/common.py`) refuses test
labels unless `--confirm-test-run` and `--purpose test-1|test-2` are given, `eval/` and this file are committed
and clean, `test-2` follows a logged `test-1`, and the same (kind, purpose) hasn't already run. A run may only be
re-executed if it failed before loading labels. One test run = at most one `score` and one `identity`
execution under the same purpose. Fields:
`run_id`, `time_utc`, `split` (`dev`|`dev_holdout`|`test`), `purpose` (e.g. `tune`, `holdout-check`,
`test-1`, `test-2`, `ablation:<name>`), `git_commit`, `git_dirty`, `split_file` + sha256, `sequences`,
`config` (full: definitions, rule hashes, thresholds, priority, models, efforts, SAM phrases, fps) +
config sha256, `predictions_sha256` (recorded before scoring), `metrics` (per-behavior P/R/F1, macro F1, CIs,
bout metrics, tracking metrics, per-arm cascade metrics), `cost` (by provider/stage/tag; marginal and
uncached), `wall_clock_s` (total and per stage, concurrency), `calls` (count, cache hits), `status`
(`started`|`ok`|`failed`|`aborted`), `notes`. `cost`, `wall_clock_s`, `calls`, and `config` are copied from the
prediction directory's `manifest.json` (written by the pipeline); the scorer doesn't compute them. A full
result JSON is also written to `eval/results/<run_id>.json`.

## 9a. Milestones (approved direction, Aniket, 2026-10-28)
- Full dev-sequence scoring before any UI work.
- **Checkpoint by end of Oct 30 ET**: per-behavior F1 (attack, investigation, mount) with macro F1, on dev and on
  dev-holdout (this counts as one logged dev-holdout look), plus speed (wall-clock per video-minute) and spend.
- Identity validation of the brightness rule against keypoints on dev (section 4.3), with the identity error
  rate, as part of the same checkpoint.

## 10. Targets (TBD, not frozen)

Proposed, to be revisited after the first dev run and frozen before test run 1. The rationale is that a
zero-shot system shouldn't be expected to match a supervised one, so we set a floor well above trivial
baselines and a stretch near the supervised number.

| Metric (test, option A) | Target |
|---|---|
| Macro F1 (attack, investigation, mount) | TBD, proposed: >= 0.55 (minimum credible), stretch >= 0.70 |
| Attack F1 | TBD, proposed: >= 0.45 |
| Investigation F1 | TBD, proposed: >= 0.55 |
| Mount F1 | TBD, proposed: >= 0.65 |
| Identity error rate vs keypoints (section 4.3, all matches) | TBD, proposed: <= 0.05 overall; mount reported separately |
| Two-animal detection coverage | TBD, proposed: >= 0.95 of samples |
| Cost per video-minute, full cascade, uncached | TBD, proposed: <= $0.25 |
| Wall-clock per video-minute | TBD, proposed: <= 0.3 min per video-minute, i.e. >= 10x faster than the cited 3x manual figure (the low end, which is less favorable to us) |
| Tier-1 adds F1 over rules only | TBD, proposed: >= +0.03 macro F1 to justify its cost |

These are guesses informed only by the trivial baselines and the supervised number, not by any Etho run.

## 11. Open questions

1. ~~Official CalMS21 F1 convention~~: resolved from the paper text (pooled; section 4.1). Official code not
   retrieved; zero-division handling is ours.
2. Per-class F1 of the supervised baseline: still unconfirmed (CalMS21 appendix not retrieved). MARS inter-annotator
   F1 values are in MARS Figure 5, figure supplements 3-4 (not yet retrieved; the main text has none).
3. Frame alignment (video frame 0 vs label 0): frame counts match on 2 of 89 videos, and alignment has only
   been checked approximately (best box-center match at a lag of -1 to -2 source frames, biased by tails).
   **Planned check on the first dev run:** for each dev video, cross-correlate SAM mask centroids with
   CalMS21 keypoint centroids (body keypoints, tail excluded) over lags -10..+10 source frames, per
   animal; report the best lag and its margin. If a consistent nonzero offset is found, fix it in the
   eval harness's frame mapping (section 3), record it in this plan, and recheck on dev-holdout.
4. Whether SAM needs long videos split into overlapping chunks (overlap adds cost).
5. Manual-scoring baseline: the cited 3-4x figure has no primary source in MARS; a second source would
   strengthen it.
6. Exclusivity priority order and bout-IoU threshold: fix on dev.
