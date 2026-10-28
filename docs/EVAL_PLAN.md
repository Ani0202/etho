# Etho evaluation plan, v1-final + amendment A1

Owner: evaluator subagent. Status: **v1-final, 2026-10-28** (written ~08:20 UTC), **amended by A1 (2026-10-28,
~08:50 UTC, section 13) before any test run**, reconciling the Codex eval-plan gate. Passages changed by A1 are
tagged **[A1]**; the v1-final text is in commit e68f1e7. Targets (section 10) and the
test-run protocol (section 7) become binding when this file is committed, which must happen **before test
run 1** (CLAUDE.md, "Evaluation integrity"). Gate before the run: Codex eval-plan review of this exact text
(Aniket's condition 2); its findings are reconciled in a dated amendment, never by silently editing a frozen
item. After the commit, any change needs a dated amendment section at the bottom (section 13). Changes
since the draft: section 12.

## 1. What we are claiming and what must be measured

**Claim wording [A1].** The headline supervision statement for any test number is:

> **No task-specific model fine-tuning and no labeled examples in any prompt; pipeline settings (prompts,
> thresholds, cascade rules) were selected using labeled development videos.**

This replaces the v1-final wording "no labeled training frames" / "zero-shot" as the *headline* description,
because the overall system was selected against labeled examples (dev and dev-holdout), which is supervision of
the system even though no network weights were updated. Foundation-model exposure to CalMS21 during pretraining
is also unverified. In this plan "zero-shot arm" (`zs_*`) remains an internal arm name for "no corrections"; in
public material it is described by the sentence above, never by "zero-shot" alone. The correction arm is
"few-shot (N corrections)" (section 6a). *The product's public one-liner ("zero-shot animal behavior scoring",
"no labeled training frames", CLAUDE.md) is product direction and Aniket's decision; the evaluator only fixes the
wording attached to reported numbers.*

**Disclosure (must accompany every reported number).**
- *Dev tuning.* Prompts, definitions wording, thresholds, reasoning effort, SAM phrases, and the overlap priority
  are chosen by developers by scoring against expert labels on the **dev** split (8 official-train sequences,
  36,980 frames, 20.5 min) and checked at milestones on **dev-holdout** (5 sequences, 28,916 frames). This is
  human-in-the-loop hyperparameter selection on labeled data; it is not training, but it is label use, and we say so.
- *Test-label aggregates informed the split design.* Before any test run, test labels were read (never viewed
  frame by frame, no test video, keypoints, or model output inspected) to compute per-sequence aggregates: frame
  counts per behavior, bout counts, and the short-bout histogram in section 3. These aggregates shaped dev
  selection (the "long attack sequence" constraint exists because all test attack is in 5 sequences of 17-20k
  frames at ~5% prevalence), the option-B subset, and the 10 fps decision. **[A1] Narrowed:** no test aggregate
  was used directly as the objective when tuning a prompt, rule, threshold, or model setting. Indirectly, they
  did influence the pipeline: they shaped the dev set every setting was tuned on, and the 10 fps sampling rate
  is itself a pipeline setting. On 2026-10-28 the evaluator re-read the same per-sequence
  aggregates (`data/calms21/seq/index_calms21_task1_test.json`, `counts` and `frames`) to project the expected
  test F1 in section 10. That projection informed the targets. The test-1 scorer reads the index's `frames`
  field (annotation length per sequence) as a label-free length check (section 7.2).
- *[A1] Test-1 is the first test **prediction** evaluation, not the first access to test labels:* the
  aggregates above were read earlier.
- *Dev-holdout was not a pristine holdout.* It was scored 7 times before the freeze (section 10.1 lists the
  runs). For the **zero-shot arm** the config was chosen on dev before its first holdout look, and the variants
  rejected afterwards (joint classifier, 5-synthesis ensemble) were worse on dev as well as on holdout, so
  holdout did not flip a zero-shot decision. For the **few-shot arm** it did inform the design: the self-check
  in the revision loop was added after the unchecked revision was seen on both dev-minus-windows (rules-only
  macro 0.3764 to 0.2747) and dev-holdout (0.3375 to 0.3349). Dev alone would have motivated the change, but
  holdout was seen, so the few-shot holdout number (0.5631) is optimistically biased.
- *Etho Local* (if built) is distilled from Muse Spark judgments on unused-train sequences, never human labels.

"Push the Frontier" (docs/PROBLEM_STATEMENTS.md) also requires measured **time and
cost per video** next to accuracy, compared against a **cited** manual-scoring baseline.

Evaluation answers four questions:
1. How well does Etho, with no task-specific fine-tuning and no labeled examples in its prompts (section 1),
   agree with expert frame labels (CalMS21 Task 1)?
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
`eval/tests/test_splits.py`). **Frozen sha256: `59bfcd47f5801757efe73cbe7276d1990b15fa2f945cf96e5bf4f001ede2880b`**
(the same file every dev and dev-holdout run logged). Its internal `"status": "DRAFT ..."` string is stale and is
deliberately left unedited, because editing it would change the hash all prior runs recorded. This plan
supersedes it.

Sequence lengths differ a lot between splits. The median length is about 3,100 frames on dev (1.7 min), 5,133
on dev-holdout (2.9 min), and 17,483 on test (9.7 min). Test runs from 4,881 to 23,810 frames.

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
  pipeline version, and once right before freezing the test config). **[A1]** It was intended not to be tuned
  on, but it was looked at 7 times and informed the few-shot design; section 1 ("Dev-holdout was not a pristine
  holdout") is the accurate account and supersedes "not tuned on". A gap between dev and dev-holdout F1 is our
  overfitting signal. Every look is logged.
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

### Test option A vs B (decided: A, all 19 sequences)

Option A (all 19) is the only option directly comparable to published CalMS21 numbers. Option B (all 5
attack sequences + mount-richest others until >= 40% of test mount frames) halves SAM cost but isn't
comparable to the published baseline and keeps only 4 of 14 mount sequences. With the 1-phrase SAM
configuration chosen in the spike (section 8), A fits the budget. **v1-final: test runs use option A only.** B
would need an amendment committed before any test run, and none is planned.

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
  seed 20261028, percentile interval; each resample pools the drawn sequences' TP/FP/FN). **[A1] Scope:** the
  interval describes variation across sampled sequences *conditional on this fitted pipeline and its realized
  predictions*. It does not cover the development choices (prompts, thresholds, cascade rules selected on
  labeled dev videos), provider sampling noise (one execution), other labs, setups, or species. It treats
  sequences as independent units, which is not verified (consecutive test sequences such as mouse075-079 may
  share animals or sessions), and attack rests on 5 attack-positive test videos. The scorer writes this scope
  into the result (`metrics.bootstrap_scope`). With 5 attack
  sequences, attack CIs will be wide; we report them anyway. Resamples in which a quantity is 0/0 are dropped
  for that quantity and their fraction is logged (`*_undefined_frac`); resamples that draw no ground-truth
  frames of a behavior but have false positives give F1 = 0, as pooling implies, and their fraction is logged
  (`ci95_no_gt_resample_frac`). **[A1]** The per-behavior undefined fractions are kept in the result JSON as
  `behaviors.<b>.ci95_undefined_frac.{precision,recall,f1}` (v1-final computed them and then dropped them). On dev, attack is in 3 of 8 sequences, so about (5/8)^8 = 2.3% of resamples
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
- Per-video summary statistics researchers actually report. **Implemented (v1-final, `eval/score_arms.py`):**
  total scored duration per behavior per video against ground truth, as MAE (s), mean signed error (s), and
  Pearson r across the test videos. **Not implemented, so not reported in test run 1:** bout count, latency to
  first bout, and ICC(2,1). These measure whether the *scientific readout* is right, which can hold even when
  frame F1 is moderate.

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
  **Frozen for test (v1-final):** `--centroid all7 --gate-px 60 --margin-px 10 --min-kp-score 0.5 --step 3`,
  which are the parameters of the dev identity run `identity-dev-20261028T060822Z-2ef506`.
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
  - **[A1] Retained-output diagnostics.** The exported tracks (`etho/tracking/pipeline.py`, `tracks.npz`) keep
    only the resident/intruder assignments the pipeline retained; unknown-labeled and extra raw SAM detections
    are not exported. So on this export the unknown rate and the "3+ detections" fraction are **zero by
    construction**, and "coverage (both labeled)" counts retained labels only. These three are reported as
    *retained-output diagnostics* (the identity output carries `retained_output_diagnostics`), never as raw
    detection quality. Identity error and matched-correct coverage remain informative;
  - **localization**: median matched centroid distance (px) per animal. Keypoint containment in masks needs
    the masks and is not implemented (the tracking table carries no mask).
- **Frame-alignment check** (open question 3): per video and animal, for lags L = -10..+10 source frames, the
  median distance between the labeled track at video frame f and its keypoint mouse at frame f + L. Only frames
  where that mouse moves >= 1 px/frame are used, because stationary stretches match at every lag. We report the
  best lag, the median at the best lag and at 0, and the margin to the runner-up. Sign: best L > 0 means label
  index = video index + L. On perfect synthetic and keypoint-derived tracks the check recovers the planted lag,
  with a 1-frame margin of 3-13 px on dev geometry.
  - **Dev result** (`identity-dev-20261028T060822Z-2ef506`): the best lag is negative on 15 of 16 (video, animal)
    pairs and 0 on one. The values range from -4 to 0, with a median of -2. The margin to the runner-up is
    0.09-3.78 px. On the 15 pairs with a negative best lag, the median distance at lag 0 is 1.99-8.97 px
    worse than at the best lag. The cause is not resolved: it could be a true
    label/video offset, or SAM mask centroids lagging the MARS keypoints. **Frozen for test: no shift (lag 0)**,
    which is the mapping every dev and holdout number used. Test alignment is reported as a diagnostic only. A
    nonzero lag on test does not change test-1 scoring.

### 4.4 Cost and time per video (required by docs/PROBLEM_STATEMENTS.md)
- Dollars per video and per minute of video, split by stage (SAM, planner, rule synthesis, tier-1, tier-2,
  report) and by provider, summed from `ops/spend.jsonl` entries tagged with the run id.
- Report **uncached** cost: for cache hits, count the cost of the original call and label the number. Also
  report the marginal cost of the run as executed.
- **Cost per video-minute (v1-final definition):** the uncached dollars of every stage the zero-shot arm runs per
  video (SAM 3.1, plus perception planner if run, plus tier-1), summed over the 19 test videos and divided by
  145.6 test video-minutes. One-time costs that don't scale with video (rule synthesis: 3 calls, done once on
  dev and reused) are reported on a separate line, never folded in or left out silently. If the test pipeline
  doesn't run the planner (the frozen config fixes the phrase `"mouse"`), the measured number is labeled
  "without planner", and a "new-user" figure adds the dev-measured planner cost explicitly.
- **Speed [A1] (Aniket's headline, target <= 1x real time). What is measured:** the runner records, per test
  video, the wall-clock of each stage run on that video: `track_s` (chunking, SAM upload and inference, mask
  decode, features, identity), then `rules_s`, then `tier1_s` (manifest `wall_clock_s.per_video.<seq>`). The
  **per-video processing time** is the sum of those stage times. It is *not* a single measured interval from
  file to output (v1-final promised that; the runner times stages separately, so any gap between stages is not
  counted, and the claim is worded accordingly). Video download is excluded. **Real-time ratio** = per-video
  processing time / video duration, with duration = decoded `src_frames` / 30 from the manifest. **All 19 videos
  are in the denominator, failures included** (a failed video contributes the time its stages took). Reported:
  all 19 per-video ratios, the **median** and the **max**, and pooled (sum of processing time / sum of
  durations). The speed targets use these three numbers, computed by `eval.score_arms` from the `zs_t1`
  manifest (`speed` in the run record; "NOT COMPUTED" if any video lacks a stage timing). Requirements:
  - SAM and tier-1 must be uncached for these timings. On test they are first-time calls by construction, but
    the few-shot arm reuses zero-shot verdicts from the cache, so its timing is reported as marginal and isn't
    used for the speed claim.
  - The concurrency is stated: videos in flight, SAM chunk parallelism, and Fireworks slots (`FW_CONCURRENCY`
    = 6 at the frozen commit). The preferred protocol is **one video at a time**, which is the case a single
    researcher sees. If videos run concurrently, the per-video ratios are reported as measured, with no
    correction for contention, and labeled with the concurrency. Batch throughput (total wall-clock / total
    video-minutes) is then reported as a separate number, never in place of the per-video ratio.
  - Rate-limit retries (HTTP 429) and failures count toward wall-clock.
  - Machine: this VM, with CPU count and RAM recorded in the manifest.
- Manual-scoring baseline: **3-4 minutes of annotator time per minute of video**. Source: Segalin et al.,
  "The Mouse Action Recognition System (MARS)...", eLife 2021 (https://elifesciences.org/articles/63720),
  Introduction, verbatim: "Scoring for social behaviors often takes human annotators 3–4× the video's
  duration to annotate; for long recordings, there is also risk of drops in annotation quality due to
  drifting annotator attention." Caveat to state wherever this is used: the MARS paper gives no citation
  for the 3-4x figure itself. Report the range (3x and 4x), not a midpoint chosen in our favor. For the
  145.6 min test set (262,107 frames / 1,800) this corresponds to 436.8-582.4 annotator-minutes
  (7.28-9.70 h), rounded down.
- **[A1] What this comparison is.** It is a *contextual runtime comparison*: Etho's machine processing time next
  to a cited estimate of human annotation time. It is **not** demonstrated labor savings at equal quality: we
  do not measure human review or correction time, and Etho's accuracy is below supervised and human levels.
  Every use of the 3-4x figure says so.

## 5. Comparisons (clearly labeled)

| Reference | Supervision | Number | Status |
|---|---|---|---|
| CalMS21 Task 1 baseline, 1D ConvNet on raw pose (28 values/frame, +/-100-frame window) (Sun et al. 2021, Table 1) | **Supervised**, trained on the 70 official train sequences (507,738 frames per the lead; frame count not re-verified) | average F1 .793 +/- .011 (std over 5 training runs, **[A1]** not a confidence interval and not comparable to our sequence-bootstrap CI), MAP .856 | **Metric and split confirmed** (paper text, section 4.1): frame-level, pooled over the full official test set, unweighted mean over the 3 behaviors, so it is directly comparable to our test option A. **Per-class F1 unconfirmed** (appendix not retrieved): never print per-class baseline numbers until confirmed. Evaluator has not re-read the PDF |
| Other CalMS21 Task 1 results, context only (arXiv:2603.07279, 2026, as reported by a scout; evaluator has not read it, and its split and metric are not checked) | Supervised: MARS features 0.878, pose coordinates 0.593. Unsupervised clusters mapped to labels: MARS 0.701 | macro F1 | None is zero-shot; the cluster mapping uses labels. No zero-shot CalMS21 result was found. Not cited in public material until checked |
| Inter-annotator agreement (MARS, Segalin et al. 2021, eLife, per CalMS21) | Human vs human | TBD | The MARS main text gives no F1 values; they are in Figure 5, figure supplements 3-4, not yet retrieved. Note: our test labels are single-annotator, so this is context, not a computed ceiling on our test set |
| Frequency-matched random predictor (computed, `eval.score.random_baseline`) | None (uses the split's class frequencies) | expected F1 = pooled prevalence. dev: attack .0757, investigation .2710, mount .0703, macro .1390. dev-holdout: .0680, .1810, .0640, macro .1043. test: .0482, .2338, .1215, macro .1345 | dev confirmed by the scorer (`sanity:oracle-10fps` run); dev-holdout and test from split-file counts |
| 10 fps oracle (computed) | Ground truth subsampled to every 3rd frame, upsampled by nearest sample | dev: attack .995, investigation .992, mount .995, macro .994 | Ceiling imposed by 10 fps sampling alone (`sanity:oracle-10fps`) |
| All-"other" predictor | None | F1 = 0 for every behavior | Trivial |

Every chart or sentence that puts Etho next to the supervised baseline must carry the section 1 supervision
statement [A1] (not "zero-shot" alone) on Etho's number and "supervised" on the baseline.

**The supervised .793 is a reference, not a target.** It comes from a model trained on 70 labeled videos, and no
Etho target is defined relative to it (section 10). It is shown so readers can see the gap, and the gap is
reported as measured. Its metric and split are confirmed only from the paper text as quoted by a scout. The
evaluator has not read the PDF, so the README and demo should cite Table 1 of arXiv:2104.02710 directly, and
someone should re-read it before publishing (section 11).

**Comparison rules.**
- **[A1]** Never claim Etho "beats" or "outperforms" a supervised result unless the test-split number does so
  with the **whole 95% CI** above the supervised point estimate, on the same metric and split. **Never claim
  "matches", "is comparable to", or "approaches"** a reference on the basis of CI overlap: an uncertain, much
  worse system can overlap. No equivalence margin is pre-registered, so no equivalence claim is made at all.
  Never base a claim on dev or dev-holdout, on a single behavior chosen after the fact, or on per-class baseline
  numbers we haven't confirmed. The wording is the **observed gap**, e.g. "macro F1 X (attack a, investigation
  i, mount m) vs .793 for a supervised model trained on 70 labeled videos, a gap of .793 - X".
- **[A1] Paired differences** (tier-1 lift, few-shot effect): CI entirely above 0 is "increased by X [CI]",
  entirely below 0 is "decreased by X [CI]", and a CI containing 0 is **"inconclusive (X [CI])"**, never "no
  change" or "no effect". The scorer prints this reading (`ci_reading`).
- Per-behavior F1 always appears next to any average (section 4.1).
- Context-only numbers (rows marked unverified) are never shown in the demo or README.

## 6. Ablations

All ablations on test use the run(s) in section 7; no extra test runs. **On test (v1-final), the ablations are
exactly the four arms of test run 1** (section 7.1):
- the cascade: rules only vs. rules + tier-1, in both the zero-shot and the few-shot arm;
- zero-shot vs. few-shot.

Every other row below is dev only, or isn't run on test. Tier-2 isn't in the frozen config (it added +0.005 dev
macro F1 for $2.30), so cascade arm (c) isn't run on test and is reported as "not run (dev result only)".

1. **Adjudication cascade** (headline ablation): (a) rules only, (b) rules + tier-1 frame strips (Fireworks
   Muse Spark Open, low effort), (c) rules + tier-1 + tier-2 video (Baseten Muse Spark Open or Muse Spark 1.3,
   high). All three arms are derived from **one** pipeline execution: every candidate bout's rule decision,
   tier-1 verdict, and tier-2 verdict are logged, and arm (b) resolves tier-1 "unsure" bouts with the rule
   decision. For each arm: macro and per-behavior F1, cost, wall-clock, number of bouts judged per tier,
   and tier-1 escalation rate. Per-tier accuracy on the bouts it judged is reported too.
   - *Dev decision (2026-10-28), restricted cascade:* tier 1 may only assign `investigation` or `none`; other
     tier-1 labels fall back to the rule decision. Chosen on **dev** after seeing dev scores, so it is dev
     tuning: dev macro F1 rules only 0.3665 (`score-dev-20261028T061615Z-04822e`), rules + unrestricted tier 1
     0.4063 (`...063110Z-812a0f`), rules + restricted tier 1 0.4820 (attack 0.4651, investigation 0.4716,
     mount 0.5092; `...063139Z-e705b8`; its manifest records no cost or call count, so whether it reused the
     unrestricted run's verdicts is not verified by the evaluator). **Dev-holdout check** with the config
     frozen: rules only 0.3375 (attack
     0.4718, investigation 0.2048, mount 0.3358; `score-dev_holdout-20261028T063741Z-07acb5`) to rules +
     restricted tier 1 0.5026 (0.5630, 0.5155, 0.4294; `...063741Z-c674ac`). Caveats: unrestricted tier 1 was
     not scored on dev-holdout, so the holdout shows the cascade helps, not that the restriction does; the
     holdout tier-1 manifest does not record the `allow` set (restricted is inferred from
     `etho/adjudication/tier1.py` `DEFAULT_ALLOW` at commit 2f3c448, which predates that run directory, and
     the lead's statement); 95% CIs are wide (dev restricted macro [0.3262, 0.6156]; 5 holdout sequences).
2. **Tier-1 effort low vs medium**: dev only. *Result (2026-10-28):* on the restricted cascade, `medium` gave dev
   macro F1 0.4714 (attack 0.4814, investigation 0.4634, mount 0.4695; `score-dev-20261028T065530Z-373134`)
   vs 0.4820 for `low`; medium was better on attack only. Tier-1 cost per the run manifests: medium $2.409,
   low $1.498 (481 windows each; the low figure is from the unrestricted `pred_dev_t1` manifest, since the
   restricted directory records no cost). Medium did not beat low, so **tier 1 stays `low`**. One run per arm,
   no repeat to measure model sampling noise, and no paired test; the 0.0106 gap is small next to the per-arm
   sequence-bootstrap CI width (about 0.29 for low).
3. **SAM 1 phrase ("mouse", identity from mask intensity) vs 2 phrases ("black mouse" + "white mouse")**:
   the spike chose 1 phrase (docs/SPIKE.md). A 2-phrase comparison, if run at all, is dev only.
4. **Keypoint-oracle features** (proposed, $0 SAM): run the same synthesized rules on features computed from
   CalMS21 keypoints instead of SAM masks, to separate tracking error from rule error. Labeled as using a
   pose estimator Etho doesn't run. Requires a keypoint-to-feature adapter in `eval/` (evaluator to build).
5. **Correction loop** (labeled "with N corrections", few-shot, never zero-shot): protocol in section 6a.
   (Replaces the earlier "k = 5 bouts on the first half of each video" draft, which was never run.)
6. **Etho Local** (stretch): distilled Glimmer as tier-1 replacement, measured on test in a planned test run.
   Distillation labels come only from Muse Spark judgments on **unused train** sequences (never dev,
   dev-holdout, test, or human labels).

Ablations that don't help are reported.

## 6a. Correction-loop protocol (simulated user), `eval/simulate_corrections.py`

The product's correction loop is: a user fixes a few bouts, Muse Spark revises the detector code from those
fixes, and we report the accuracy change. In eval a seeded **simulated user** plays the user from expert labels.
It makes no model calls and imports no product code; product code receives only its output file.

- **Supervision label.** Any number produced after corrections is **few-shot** and is reported as "with N
  corrections" (N = number of corrected windows, with the K per behavior and kind). It is never called zero-shot
  and never shown next to a zero-shot number without that label.
- **Source.** Corrections come **only from dev** labels. The script refuses `dev-holdout` and `test`.
- **Selection** (unbiased with respect to anything except the error type; all on the 10 fps sample grid,
  sample k = frame 3k, ground truth = the 30 fps label at frame 3k):
  - Bouts are maximal runs of one behavior. An **FP** of behavior b is a predicted bout of b with < 20% of its
    samples labeled b by the expert; an **FN** is an expert bout of b with < 20% of its samples predicted b.
    No gap merging and no minimum length by default, so 0.1 s error bouts are eligible.
  - For each of the 6 strata (3 behaviors x {FP, FN}), candidates are pooled over all dev sequences and K are
    drawn uniformly without replacement (default K = 3 FP + 3 FN per behavior, 18 windows) with
    `numpy.random.default_rng([seed, behavior_index, kind_index])`, seed 20261028. A stratum with fewer than K
    candidates gives all of them; the shortfall is logged.
  - A bout longer than 3 s is clipped to a 3 s window at a uniformly drawn offset. Windows of different strata
    may overlap; overlaps are counted in the output, not removed.
- **What the "user" provides.** Per window: sequence, samples [s0, s1), behavior, fp/fn, the per-sample predicted
  and correct labels (plus majority labels), and median/min/max of every tracking feature over the window. The
  30 fps frame range `frames_30fps` that the scorer maps onto the window is included for exclusion scoring.
- **Where the effect is measured.** (1) **dev-holdout**, the primary number: no corrected window comes from it;
  the revised detectors are scored with `eval/score.py` exactly like the uncorrected ones, both reported.
  (2) **dev minus the corrected windows** (frames in `frames_30fps` of any correction removed from both
  prediction and ground truth before pooling), secondary, since dev was also used for tuning. Scoring with
  exclusion: `eval/score.py --exclude-corrections` (frames dropped from both sides for frame metrics; set to
  "other" in both for bout metrics, so bouts are cut at the window rather than joined across it).
  Full-dev numbers including the corrected windows are not reported as an improvement.
- **Test.** The corrections file, the revised detector code (hashes), and the revision prompt are frozen and
  committed **before** a test run; the revised detectors run on test only inside one of the two planned test
  runs (section 7). No correction is ever drawn from test, and no revision happens after test labels are loaded.
- **Logging.** Every generation is logged to `eval/results/runs.jsonl` (kind `simulate-corrections`): a
  `started` record with the predictions sha256 before labels are opened, then the config, per-stratum candidate
  and selection counts, the selected windows, and the output sha256. Multiple seeds, if run, are all reported.
- **Files generated** (2026-10-28, seed 20261028, K = 3+3, 18 corrections each, all strata filled, 0 overlapping
  windows). Dev-minus-windows scores below are of the *uncorrected* predictions, i.e. the baseline arm.
  - **Use this one:** restricted cascade (the chosen config), `data/runs/R1/pred_dev_t1a_inv_none` (dev macro
    0.4820): `eval/results/corrections/R1_dev_t1a_inv_none_seed20261028_k3+3.json`, run
    `corrections-dev-20261028T070154Z-4df3b7`. Candidates: attack FP 58 / FN 5, investigation FP 33 / FN 61,
    mount FP 52 / FN 3 (all 3 mount FN bouts taken). 11 of 18 windows in mouse008; 6 of 8 sequences contribute;
    lengths 1 to 30 samples (one 0.1 s window). 693 frames excluded; dev minus windows macro 0.4995
    (attack 0.4797, investigation 0.4846, mount 0.5344; `score-dev-20261028T070155Z-a17fdc`).
  - Superseded, kept for the record: `data/runs/R1/pred_dev_t1` is the **unrestricted** tier-1 run (dev macro
    0.4063), generated first by mistake: `R1_dev_t1_seed20261028_k3+3.json`, run
    `corrections-dev-20261028T065145Z-2a476f`; dev minus its 819 frames, macro 0.4264
    (`score-dev-20261028T070125Z-cc64fa`).
- **Scoring dev minus the corrected windows:** `uv run python -m eval.score --split dev --pred-dir <dir>
  --exclude-corrections <corrections.json>`. Both arms (before and after revision) must be scored with the same
  corrections file. The run record logs the file, its sha256, the corrections run id, and frames excluded per
  sequence. Refused on dev-holdout and test.

## 7. Test-run protocol (v1-final)

General rules (unchanged from the draft):
- **At most two test runs**, both reported. A "test run" is any execution that loads test behavior labels or test
  keypoints, or computes any metric (including tracking metrics) on test outputs. A run that crashes before
  loading labels may be restarted; it is still logged as `status: failed`.
- **No peeking.** No one inspects test videos, overlays, frame strips, tier-1 verdict text, tracks, or predictions
  before test run 1 is scored. That includes Etho's own report HTML for a test video. Automated, label-free
  failure checks are allowed and logged: API errors, empty masks, a frame count that differs from the expected
  length, missing files. Product code (`etho/`) never reads labels or keypoints.
- SAM tracks of test videos are cached by input hash; run 2 reuses run 1's tracks. A tracking change would cost
  a second full SAM pass (about $17.5, section 8). Aniket's approval covers one pass, so it would need a new
  estimate and a new approval.

### 7.1 Test run 1: what runs

The frozen config `configs/frozen_test_v1.json` (committed in 7391fca; sha256 is recorded by the scorer) runs
once over all 19 official test sequences (option A). It produces **four prediction arms from one pipeline
execution on one set of SAM tracks**:

| Arm id | What | Role |
|---|---|---|
| `zs_t1` | zero-shot: R1 detectors (`R1_zero_shot.json`) + tier-1 restricted (investigation/none), low effort | **Primary, the headline** |
| `zs_rules` | the same detectors with no tier-1, i.e. the rule decisions logged before adjudication | Cascade ablation |
| `fs_t1` | few-shot: detectors revised from 18 simulated **dev** corrections (`R1_revised_18corrections.json`) + the same tier-1 | Few-shot arm |
| `fs_rules` | the few-shot detectors with no tier-1 | Cascade ablation (few-shot) |

- All four arms share the SAM tracks, features, identity, definitions, overlap priority (mount > attack >
  investigation), tier-1 model, prompt, and thresholds. They differ only in the detector file and in whether
  tier-1 is applied.
- Tier-1 windows that are identical across arms are served from the cache. The zero-shot arm runs first, so its
  cost and timing are uncached.
- Tier-2 isn't used (frozen config: `"tier2": "not used"`). The perception planner runs only if the pipeline
  as frozen runs it; either way, the manifest says which.
- Product code is run from a commit whose `etho/` tree equals the frozen commit's (`git diff 4ada51c -- etho/`
  is empty; it is at the time of writing). The manifest records the commit.

### 7.2 Test run 1: order of operations

1. **Pre-flight (label-free, $0).** Regenerate the dev-holdout predictions of all four arms with the frozen
   config at the run commit, from cached tracks. **[A1]** The comparison is on the **prediction payload** (the
   per-sequence files), separately from `manifest.json`, whose timing and provenance fields legitimately change
   between executions; the scorer logs `payload_sha256` and `manifest_sha256` separately. The new per-sequence
   file hashes must equal those of the logged runs (whose combined hashes were):
   - `zs_t1` d77eadc88c75... (run `c674ac`);
   - `zs_rules` ab3c0c2ac38d... (`07acb5`);
   - `fs_t1` d40ec583c780... (`7ef9fa`);
   - `fs_rules` bcb40445e174... (`362649`).

   Tier-1 calls should all be cache hits. If a hash differs, test run 1 doesn't start until the cause is found
   and logged. The evaluator has already re-scored the *existing* holdout predictions through the test code
   path (`score-dev_holdout-20261028T081858Z-325a87`, purpose `sanity:score-arms-repro`): all four
   reproduce exactly. That checks the scorer, not the regeneration.
2. **Produce everything, read nothing.** Run SAM, features, rules, and tier-1 on the 19 test videos, and write
   the four prediction directories and the tracks directory. Every directory needs:
   - a file for **every** sequence. If tracking failed for a sequence after retries, the file is still written
     (empty or all "other"), and the failure is noted in `manifest.json`. That sequence is scored as "other",
     never dropped;
   - a `manifest.json` with `sample_fps`, `config` (including the frozen config's sha256 and the product git
     commit), `cost` (by stage and provider, uncached and marginal), `wall_clock_s` (per video and per stage,
     with concurrency), `calls` (count, cache hits), and the machine spec;
   - **[A1] fields the scorer now requires on test** (it refuses before the `started` record without them):
     `arm` (= the arm id); `config.frozen_sha256`, `config.frozen_at_commit`, `config.run_commit`, and
     `config.run_git_dirty_paths` (the `git status --porcelain` paths when the runner started; none may be under
     `etho/` (except `etho/app/`), `configs/`, or `scripts/test_run.py`); `frames.<seq>.src_frames` (decoded
     frame count) and `frames.<seq>.n_samples` (null only for a failed sequence); `failed_sequences` (a list, `[]` if none).
     For the speed verdict: `wall_clock_s.per_video.<seq>.{track_s, rules_s, tier1_s}`.
   - **[A1] Label-free check of the outputs**, before step 3, without using a test run:
     `uv run python -m eval.score_arms --split test --preflight-only --frozen-config configs/frozen_test_v1.json
     --targets eval/targets_v1.json --primary zs_t1 --arm ... --compare ...` (same arms and comparisons as step
     3). It runs every check of step 3's preflight, logs a `kind: "preflight"` record, and opens no label file.
3. **Score all arms in one execution** (hashes are logged before any label is read):
   ```
   uv run python -m eval.score_arms --split test --purpose test-1 --confirm-test-run \
     --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
     --arm zs_t1=<dir> --arm zs_rules=<dir> --arm fs_t1=<dir> --arm fs_rules=<dir> \
     --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1 --compare zs_rules:fs_rules
   ```
   The scorer refuses to start, and logs nothing as a test run, if any frozen file's sha256 differs. It also
   refuses if `eval/` or this plan has uncommitted changes, or if a `score` execution under `test-1` already
   loaded labels. **[A1] Pre-label checks** (all label-free; any unexplained problem stops the run before the
   `started` record, logged as `kind: "preflight", status: "refused"`, which does not use up a test run):
   - *Predictions:* a file for every sequence in every arm; npz/jsonl syntax; `sample_fps` declared and dividing
     30, `sample_stride` (if present) equal to 30 / `sample_fps`, file and manifest rates agreeing; label codes in
     {0, 1, 2, 3}; per sequence, `n_samples == ceil(src_frames / 3)`, the file's label count equal to
     `n_samples`, and `src_frames` equal to the expected annotation length (index `frames`); every arm's
     `frames` and `failed_sequences` identical (one execution).
   - *Failures:* a sequence with no `n_samples` must be listed in `failed_sequences`, and its placeholder must be
     empty or all "other". It is then scored as "other", never dropped.
   - *Accepting a mismatch:* only by naming it, `--accept-length-mismatch SEQ=REASON`, which is logged in the
     `started` record. A mismatch that would make the file unscorable without an undeclared truncation (more
     samples than the annotation allows) cannot be accepted.
   - *Freeze coverage:* exactly the arms `zs_t1, zs_rules, fs_t1, fs_rules`, primary `zs_t1` (and the targets
     file's `primary_arm`), all four comparisons of this command; every manifest's `config` matches the frozen
     config's sha256 and `frozen_at_commit`, all arms share one `run_commit`; `git diff <frozen_at_commit>
     <run_commit> -- etho/ ':!etho/app'` is empty (the web app is outside the freeze, see A1 item 4), and no file
     in that frozen tree, nor `scripts/test_run.py`, imports `etho.app` at `run_commit`; the tier-1 `PROMPT` in `etho/adjudication/tier1.py` at `run_commit` hashes
     to `tier1.prompt_sha256` (read with `ast`, not imported); the frozen config and each frozen asset are
     byte-identical at `run_commit`. The runner script's sha256 at `run_commit` is recorded (it is not frozen).
   It then writes one `started` record holding every arm's per-file sha256, payload and manifest hashes, the
   manifest, the preflight report, and the label-free speed computation, **then** opens labels. `eval.score`
   (single-arm) refuses the test split, so the arms can't be scored one at a time.
   - **Per-sequence results are sealed.** They go only to
     `eval/results/<run_id>.per_sequence.sealed.json`, whose sha256 is logged. They are not printed and not
     written to `runs.jsonl` or the result JSON.
   - The seal is lifted only after the test-2 decision (section 7.4) is committed, as a dated amendment saying
     "no test-2" or describing it. Until then, no one opens that file or inspects any per-video output.
4. **Identity check (same test run), on the shared tracks:**
   ```
   uv run python -m eval.identity --split test --tracks-dir <tracks> --purpose test-1 --confirm-test-run \
     --centroid all7 --gate-px 60 --margin-px 10 --min-kp-score 0.5 --step 3
   ```
   - It runs after step 3. Its `started` record hashes the tracks before keypoints are read.
   - It reports identity error (all matches and unambiguous only; overall, per behavior, and per video), swap
     events per minute, coverage, unknown rate, detections, localization, and the alignment lag diagnostic
     (section 4.3).
   - Per-video identity output is not sealed: it needs no behavior predictions, and no test-2 option depends
     on tracking.
5. **Report** (section 7.3). Aniket gets a PushNotification when the SAM pass starts (his condition), and the
   lead reports the numbers once the scorer has finished.

Ordering rules: step 3 comes before step 4; steps 3 and 4 each run at most once under `test-1` (the guard
enforces this); nothing in step 2 is re-run after step 3 starts.

### 7.3 What is reported (every number, including the ones that lose)

For **each of the four arms**:
- precision, recall, and F1 per behavior, each with its 95% sequence-bootstrap CI;
- macro F1 with its CI;
- bout-level F1 at IoU 0.3 and 0.5;
- the confusion matrix;
- one-vs-rest F1 without the exclusivity rule (labeled as such);
- per-sequence mean and median F1;
- duration agreement (MAE, signed error, Pearson r);
- the frequency-matched random floor on test (expected macro .1345; attack .0482, investigation .2338,
  mount .1215).

**Paired differences** with CIs from the same sequence resamples:
- `zs_t1 - zs_rules`, the tier-1 lift and a target;
- `fs_t1 - zs_t1`, the few-shot effect;
- `fs_t1 - fs_rules`;
- `fs_rules - zs_rules`.

Also reported:
- **Targets** (section 10), each verdict printed by the scorer: MET, MET ROBUSTLY, or NOT MET, at full precision.
  **[A1]** F1, the F1 levels, tier-1 lift, and speed are computed by the scorer; cost, identity, and coverage
  verdicts are computed by hand from the logs with the same direction-aware rule, with the arithmetic shown.
- **Cost and time** per section 4.4: per stage, per video, pooled, uncached and marginal, plus the actual spend
  from `ops/spend.jsonl`.
- **Identity**: the full output of step 4.
- **Failures**: any sequence with failed tracking or tier-1, how many windows tier-1 left unresolved, and every
  warning.

**The few-shot arm is reported even if it loses**, labeled "few-shot (18 simulated corrections from dev
labels)". It is never called zero-shot. Wording:
- if the paired `fs_t1 - zs_t1` macro CI lies entirely above 0: "with 18 corrections, macro F1 rose by X [CI]";
- if the CI contains 0: **[A1]** "inconclusive: X [CI]" (not "no measurable change", which reads as evidence
  of no effect);
- if the CI lies entirely below 0: "the corrections lowered macro F1 by X [CI]".

The same rule applies per behavior. Dev evidence on this arm is mixed: holdout +0.0605 [-0.0179, +0.0828], and
dev minus the corrected windows 0.4995 to 0.4461, which is -0.0534. The holdout number is also biased upward
(section 1). So no direction is predicted.

**Headline [A1]:** the `zs_t1` macro F1 of **test-1**, always shown with its three per-behavior F1s and CIs and
the section 1 supervision statement.
- Shown next to: the supervised .793 (labeled "supervised, 70 labeled videos"; a reference, not a target), the
  random floor, and time and cost vs the cited 3-4x manual figure, showing both 3x and 4x.
- The comparison wording rules of section 5 apply.

### 7.4 Test run 2 [A1]

**Test-1 is the headline.** There is no route by which a later run replaces it because of a development change:
after test-1's aggregate results are known, any development they prompt can be steered by them without that
appearing in a written justification, so a "dev-justified" test-2 cannot be treated as held-out. (v1-final's
route (b), "a dev-justified change" that could become the headline, is removed.)

- **Default: test-2 is exploratory.** It is reported and labeled exploratory everywhere, with "decided after
  seeing test-1 results". It is never the headline, never shown in the demo as "the result", and never described
  as held-out.
- **Single exception: a documented implementation defect.** If test-1 was affected by a specific defect that
  can be **demonstrated without test labels** (a crash, truncated or missing outputs, a wrong config or prompt
  actually loaded, a frame-mapping error, a cache-key collision; "attack F1 was low on test" is not a defect),
  the fix is verified on dev, and the defect would have affected dev the same way, then a test-2 with only that
  fix is a **corrected** run. The defect, its label-free evidence, and the fix are written as a dated amendment
  and committed before test-2 runs. **Both results are kept and shown together**, the correction is named
  ("test-1 X; corrected for <defect>: Y"), and this holds regardless of which number is higher. Only in this
  case may the corrected run be cited as the main number, always next to test-1.
- We never pick the better of the two.

Test-2 reuses test-1's cached tracks ($0 SAM). It runs under `--purpose test-2` with the same four arms. Any
arm it drops is listed with the reason. The per-sequence seal (section 7.2) is lifted only after the test-2
decision ("no test-2", "exploratory test-2: <what>", or "corrected test-2: <defect>") is committed.

### 7.5 Abort rules
- If the projected Fireworks spend for test-1 passes the `ops/spend-plan.md` estimate by more than 50%, the
  lead pauses tier-1 and escalates. The test run hasn't started yet, because no labels have been read.
- If SAM fails for a sequence after the pipeline's retries, its file is written empty (section 7.2, step 2).
  The sequence is never left out, and never re-tracked after labels are loaded.

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

LLM costs. The draft's guess (about $0.0055 per call, unmeasured) is replaced by figures from the tier-1 run
manifests. The evaluator has not checked whether any of these runs had cache hits.
- Per window: dev, low effort, unrestricted: 481 windows, $1.498, so $0.00311. Holdout few-shot: 451 windows,
  $1.121, so $0.00248. Holdout zero-shot: 335 windows, $0.341, so $0.00102, which is probably partly cached and
  not used below. Dev, medium effort: $0.0050.
- Windows per video-minute: dev 23.4; holdout zero-shot 20.9; holdout few-shot 28.1.

**Test run 1 estimate** (the lead's `ops/spend-plan.md` gives about $18 Meta and about $15 Fireworks):

| Item | Provider / tag | Basis | Estimate |
|---|---|---|---|
| SAM 3.1, 19 videos, 10 fps, 60 s chunks | Meta / `eval-sam` | 87,374 billed samples at $0.20/1k, plus per-chunk 50-frame minimums | $17.47-18.0 |
| Tier-1, zero-shot arm | Fireworks | 145.6 min x 20.9-23.4 windows/min x $0.00248-0.00311 | $7.5-10.6 |
| Tier-1, few-shot arm | Fireworks | up to 145.6 x 28.1 windows/min x the same per-window cost, minus windows shared with zero-shot (cache) | $0-12.7; the upper bound assumes no sharing |
| Perception planner (if run) | Meta / `eval-llm` | ADR-001: about $0.05-0.07 per video | about $1.3 |
| Scoring and identity (`eval/`) | none | local | $0 |
| **Total** | | | **Meta about $17.5-19.3. Fireworks about $7.5-23.3, central about $15** |

Both provider totals are over the $5 single-job threshold. Aniket's pre-approval (2026-10-28) names **only the
SAM pass (<= $20)**, so the Fireworks share needs its own escalation (section 11). Test run 2 reuses the
cached SAM tracks. Its tier-1 cost is $0 if nothing upstream of tier-1 changed, and otherwise depends on the
change.

## 9. Run logging: `eval/results/runs.jsonl`

Append-only, never edited, including failed, aborted, disappointing, and sanity runs. Each execution writes
**two** records with the same `run_id`: a `status: "started"` record (predictions/tracks sha256, config, git
state) appended **before any label file is opened**, then a final `ok` or `failed` record with the metrics or the
error, and `labels_loaded`. `kind` is `score` or `identity`. The test guard (`eval/common.py`) refuses test
labels unless `--confirm-test-run` and `--purpose test-1|test-2` are given, `eval/` and this file are committed
and clean, `test-2` follows a logged `test-1`, and the same (kind, purpose) hasn't already run. A run may only be
re-executed if it failed before loading labels. One test run = at most one `score` and one `identity`
execution under the same purpose. Fields:
**[A1]** A third kind, `preflight`, is a label-free check by `eval.score_arms` (`--preflight-only`, or a refusal
before the `started` record): `status` `ok` or `refused`, `labels_loaded: false`, the problems found. It is not a
scoring execution and does not count toward the test-run limit.
`run_id`, `time_utc`, `split` (`dev`|`dev_holdout`|`test`), `purpose` (e.g. `tune`, `holdout-check`,
`test-1`, `test-2`, `ablation:<name>`), `git_commit`, `git_dirty`, `split_file` + sha256, `sequences`,
`config` (full: definitions, rule hashes, thresholds, priority, models, efforts, SAM phrases, fps) +
config sha256, `predictions_sha256` (recorded before scoring), `metrics` (per-behavior P/R/F1, macro F1, CIs,
bout metrics, tracking metrics, per-arm cascade metrics), `cost` (by provider/stage/tag; marginal and
uncached), `wall_clock_s` (total and per stage, concurrency), `calls` (count, cache hits), `status`
(`started`|`ok`|`failed`|`aborted`), `notes`. `cost`, `wall_clock_s`, `calls`, and `config` are copied from the
prediction directory's `manifest.json` (written by the pipeline); the scorer doesn't compute them. A full
result JSON is also written to `eval/results/<run_id>.json`.

**Multi-arm executions (v1-final, `eval/score_arms.py`, required on test).** One `score` execution covers all
arms of a test run.
- The `started` record has an `arms` map: per arm, `pred_dir`, `predictions_sha256`, per-file hashes, the full
  `manifest`, and missing files. It also has `frozen_config` (path, sha256, and every file hash it checked)
  and `targets` (path and sha256).
- The final record holds `metrics.arms.<arm>`, `metrics.paired_differences`, `targets_check`, and, on test,
  `per_sequence_sealed` (path and sha256 of the sealed file).
- `eval.score` refuses the test split.

## 9a. Milestones (approved direction, Aniket, 2026-10-28)
- Full dev-sequence scoring before any UI work.
- **Checkpoint by end of Oct 30 ET**: per-behavior F1 (attack, investigation, mount) with macro F1, on dev and on
  dev-holdout (this counts as one logged dev-holdout look), plus speed (wall-clock per video-minute) and spend.
- Identity validation of the brightness rule against keypoints on dev (section 4.3), with the identity error
  rate, as part of the same checkpoint.

## 10. Targets (v1-final, frozen at commit; written before any test run)

Targets apply to the **frozen pipeline arm `zs_t1` on the official test split (all 19 sequences)**, in
**test-1** [A1] (section 7.4). The machine-readable copy is `eval/targets_v1.json`; if the two disagree, this section wins.

**The supervised .793 is a reference, not a target.** No target is set relative to it, and missing it isn't a
failure. A pipeline with no task-specific fine-tuning isn't expected to reach a model trained on 70 labeled
videos.

### 10.1 Evidence the targets are based on (dev and dev-holdout, frozen arms)

| Run | Split | Arm | Attack F1 | Investigation F1 | Mount F1 | Macro F1 [95% CI] |
|---|---|---|---|---|---|---|
| `score-dev-...061615Z-04822e` | dev | zs_rules | .3991 | .2587 | .4417 | .3665 [.2218, .5161] |
| `score-dev-...063139Z-e705b8` | dev | zs_t1 | .4651 | .4716 | .5092 | .4820 [.3262, .6156] |
| `score-dev_holdout-...063741Z-07acb5` | holdout | zs_rules | .4718 | .2048 | .3358 | .3375 [.1723, .4810] |
| `score-dev_holdout-...063741Z-c674ac` | holdout | **zs_t1** | **.5630** | **.5155** | **.4294** | **.5026 [.3330, .6110]** |
| `score-dev_holdout-...071819Z-362649` | holdout | fs_rules | .4328 | .3932 | .4326 | .4195 [.3071, .4914] |
| `score-dev_holdout-...072835Z-7ef9fa` | holdout | fs_t1 | .5427 | .6338 | .5129 | .5631 [.3849, .6084] |
| `score-dev-...070155Z-a17fdc` | dev minus 18 corrected windows | zs_t1 | .4797 | .4846 | .5344 | .4995 |
| `score-dev-...074131Z-0200ed` | dev minus 18 corrected windows | fs_t1 | .5266 | .5156 | .2961 | .4461 |

Paired holdout differences come from `score-dev_holdout-20261028T081858Z-325a87`. That run re-scores the existing
predictions with the same sequence resamples; it makes no new predictions.
- Tier-1 lift `zs_t1 - zs_rules`: +0.1652 [+0.0521, +0.2613].
- Few-shot `fs_t1 - zs_t1`: +0.0605 [-0.0179, +0.0828].

Precision and recall of `zs_t1` on holdout:

| Behavior | Precision | Recall |
|---|---|---|
| Attack | .421 | .848 |
| Investigation | .671 | .419 |
| Mount | .390 | .478 |

The cascade over-predicts attack and mount, and under-predicts investigation.

Dev-holdout looks before the freeze, 7 executions:
- `07acb5` and `c674ac` (zero-shot);
- `5ce4e4`, `362649`, and `7ef9fa` (few-shot iterations);
- `439dac` (joint classifier);
- `03ecc8` (ensemble).

`325a87` above is a re-score of existing predictions, not a new look. Section 1 says what these looks did and
did not influence.

### 10.2 How test differs, and the projected effect

Test differs from dev and dev-holdout in three ways that matter. To project their effect, we applied each
behavior's pooled per-frame recall and false-positive rate (FP per frame not of that behavior) from dev or
dev-holdout `zs_t1` to the test totals disclosed in section 1. This assumes the per-frame rates transfer to
test. It ignores sequence-to-sequence variance and any recording shift between the train and test sessions,
so it is an expectation, not a bound.

1. **Videos are much longer** (median 9.7 min vs 1.7 and 2.9 min) and spend most of their time in "other".
   Precision then depends mostly on the false-positive rate per background frame, not on how well short,
   dense clips are scored.
2. **Attack is sparse and concentrated.**
   - All 12,630 attack frames are in 5 sequences of 17,483-19,710 frames, where attack is 3.9-23.7% of
     frames. The other 14 sequences (168,519 frames) have none, so every attack frame predicted there is a
     false positive.
   - The `zs_t1` attack FP rate per non-attack frame is 8.6% on dev and 8.5% on holdout. Inside the no-attack
     sequences alone it is 9.0% on dev and 5.7% on holdout.
   - Carried to test, this gives attack precision of about 0.27-0.34, against .421 on holdout.
   - Recall is the larger unknown. On the long dev attack sequence (mouse008, 11,407 frames) recall is 0.21,
     and on the long holdout one (mouse070, 12,537 frames) it is 0.94.
   - **We expect attack F1 to fall from holdout's .563**, to about 0.38-0.48 if recall transfers and to about
     0.21 if long-video recall looks like mouse008.
3. **Mount is twice as prevalent** (12.2% of test frames vs 7.0% dev and 6.4% holdout). At the same FP rate,
   precision rises with prevalence, so **we expect mount F1 to rise** from holdout's .429.
   Investigation prevalence on test (23.4%) lies between dev (27.1%) and holdout (18.1%), so it should change
   little.

Projected test F1 for `zs_t1` (method above; the evaluator's calculation, not a measurement):

| Rates taken from | Attack | Investigation | Mount | Macro |
|---|---|---|---|---|
| dev | .375 | .459 | .632 | .489 |
| dev-holdout | .481 | .534 | .517 | .511 |
| dev + holdout pooled | .420 | .488 | .596 | .501 |
| attack, long sequences only: dev / holdout / pooled | .211 / .434 / .420 | | | |
| *zs_rules*, holdout rates (for the tier-1 lift) | .388 | .208 | .444 | .347 |

The point projections cluster around macro 0.49-0.51. The holdout sequence-bootstrap CI is [.333, .611], and
attack rests on 5 test videos, so a realistic range for test macro F1 is about **0.38-0.58**. **[A1]** This range
is a *planning judgment*, not a derived prediction interval: the transfer assumptions above do not establish its
coverage, and it is not used to judge the result.

### 10.3 Targets

A target is **MET** if the point estimate reaches the threshold. **[A1]** It is **MET ROBUSTLY** if the 95%
sequence-bootstrap CI bound on the far side reaches it too: the **lower** bound for higher-is-better targets
(F1, tier-1 lift, coverage), the **upper** bound for lower-is-better targets (identity error, speed, cost).
Thresholds are inclusive (>= / <=). A metric without a CI (speed, cost, coverage as computed) can be MET but
never MET ROBUSTLY. Otherwise it is **NOT MET**. Comparisons use full precision, with no rounding. All verdicts
are reported, including NOT MET. `eval/targets_v1.json` encodes the direction of every target.

**[A1] What counts as reaching a level.** An accuracy level ("minimum credible" or "good") is reached only if
macro F1 **and each of the three per-behavior F1 thresholds** are MET; robustly only if all four are MET
ROBUSTLY. Macro F1 alone never carries the level wording (the scorer prints `f1_level` with the failing
components). The other targets (tier-1 lift, identity, coverage, speed, cost) are separate verdicts, each
reported on its own; there is no single overall "success".

| Metric (test, `zs_t1`) | Minimum credible | Good | Dev/holdout basis |
|---|---|---|---|
| **Macro F1** (attack, investigation, mount) | >= 0.40 | >= 0.50 | holdout .5026; projected .49-.51 |
| Attack F1 | >= 0.25 | >= 0.40 | holdout .5630; projected .21-.48 (expected to fall) |
| Investigation F1 | >= 0.40 | >= 0.50 | holdout .5155; projected .46-.53 |
| Mount F1 | >= 0.40 | >= 0.55 | holdout .4294; projected .52-.63 (expected to rise) |
| Tier-1 lift, `zs_t1 - zs_rules` macro (paired) | >= +0.03 | >= +0.10 | holdout +0.1652 [+0.0521, +0.2613] |
| Identity error rate, all matches, overall (section 4.3) | <= 0.05 | <= 0.01 | dev 0.0019 |
| Identity error rate, mount frames | reported; > 0.10 is flagged as a limitation | | dev 0.0098 |
| Two-animal coverage (both labeled), a retained-output diagnostic (section 4.3 [A1]) | >= 0.95 | >= 0.98 | dev 0.992 |
| **Speed**, real-time ratio of summed per-video processing time, all 19 videos (section 4.4 [A1]) | pooled <= 1.0x, and median per video <= 1.0x | max per video <= 1.0x, and pooled <= 0.5x | 5-min dev video, without tier-1: 0.22x (STATE.md; the evaluator has not seen the raw timing log); tier-1 took 97 s on a 7-min video with 6 slots, which is 0.23x; expected about 0.45x |
| **Cost per video-minute**, uncached (section 4.4) | <= $0.25 | <= $0.20 | SAM $0.120-0.124 (chunk minimums); tier-1 about $0.052-0.073 (section 8); planner, if run, about $0.007-0.009; expected about $0.17-0.21 |

How the thresholds were chosen:
- **Minimum credible:** about 3x the random floor for macro (.1345). It sits below the low end of the
  projections except for attack, where a long-video recall failure like mouse008 would miss it. Below it, the
  accuracy result isn't demo-worthy, and that is stated plainly. **[A1]** "Minimum credible" is an
  **engineering milestone**, not a scientific validation threshold: three times one particular random baseline
  does not establish that the scores are useful for research, and the frequency-matched predictor is a
  legitimate floor but not the strongest no-information predictor.
- **Good:** what the holdout result would look like if it transferred to test, adjusted for the attack and
  mount prevalence effects in section 10.2.
- **Speed:** the minimum credible threshold is Aniket's headline (<= 1x real time). At 1x, Etho's processing
  time is a third to a quarter of the cited 3-4x manual annotation time; per section 4.4 [A1] that is a
  contextual runtime comparison, not labor savings at equal quality. The good threshold of 0.5x is close to the
  dev-based expectation.

The draft's targets (macro >= 0.55 minimum and 0.70 stretch; attack .45, investigation .55, mount .65;
wall-clock <= 0.3x) were set before any Etho run. They are **lowered here because of the dev and holdout
evidence**. That is a disclosed change, made before any test run (section 12).

Not targets, but reported (section 7.3): the few-shot arm and its paired difference. No direction is
predicted, and the wording rules are in section 7.3. Also reported: bout-level F1, duration agreement, and
cost and time vs manual scoring.

## 11. Open questions and unresolved items before test run 1

**Blocking test run 1 (preconditions):**
1. **Codex eval-plan gate** on this exact text (Aniket's condition 2). **[A1]** Run 2026-10-28
   (`ops/codex/ask-20261028-082550-84666.md`, gpt-6-astra, xhigh): "hold test-1 until the reporting rules and
   runner issues below are resolved". Its findings are reconciled in amendment A1 (section 13). Whether A1
   needs a second Codex look before test-1 is the lead's call; the evaluator recommends a short re-check of
   sections 1, 5, 7.2-7.4, and 10.3.
2. **Fireworks spend for test-1 isn't covered by the pre-approval.** Aniket approved the SAM pass (<= $20, Meta).
   Tier-1 on test is a separate job estimated at $7.5-23.3, central about $15 (section 8). It is over the $5
   single-job line and needs its own escalation (CLAUDE.md, escalation 1).
3. **Pre-flight regeneration (section 7.2, step 1).** **[A1] Status:** the lead reports it passed (commit 69ab02c,
   "pre-flight passed"); Codex independently confirmed all 20 holdout payloads match. The evaluator checked
   label-free on 2026-10-28 that the per-sequence files in `data/runs/PF/{zs_t1,zs_rules,fs_t1,fs_rules}` equal
   those of the four logged holdout runs (5 files per arm, all equal). The strict preflight of those directories
   (`score-dev_holdout-20261028T084711Z-05b1b3`, kind `preflight`) passed every check except one: the manifests
   don't yet record `config.run_git_dirty_paths`. `scripts/test_run.py` had uncommitted changes in the working
   tree at the time, which is the case that field exists to catch. Original v1-final text: The holdout predictions were made at
   2f3c448 with a dirty tree. `etho/rules/sandbox.py` and `etho/adjudication/tier1.py` changed between that run
   and the frozen commit 4ada51c.
   - Tier-1 prompt template: sha256 `ada77a63...` at 2f3c448 and `bb53933f...` (frozen) at 4ada51c. By diff, the
     only change is an empty `{features}` slot (`features_in_prompt: false`), so the rendered prompts should
     match. The evaluator has not checked this by re-rendering.
   - The zero-shot holdout tier-1 manifest doesn't record the `allow` set. The few-shot one does
     (`["investigation", "none"]`).
4. **Pipeline outputs needed by the protocol (product code; the lead's call):**
   - (a) the rules-only arms `zs_rules` and `fs_rules`, written from the same execution;
   - (b) a file for every sequence, even when tracking fails;
   - (c) a `manifest.json` with `config` (frozen-config sha256 and git commit), `cost` (by stage, uncached and
     marginal), `wall_clock_s` (per video and per stage, with concurrency), `calls`, and the machine spec.

   The current manifests use ad-hoc keys (`cost_usd`, `wall_s`, `predict_wall_s`), so those fields show up as
   null in `runs.jsonl`. Without (c), the speed and cost targets can't be checked from the log.
   **[A1] Status:** the runner (`scripts/test_run.py`, lead's) now writes (a), (b), and most of (c), including
   `wall_clock_s.per_video` with `track_s`, `rules_s`, `tier1_s` (the scorer's speed computation on the PF
   manifests reproduces the manifest's own median 0.10225x and max 0.14208x; those PF timings had all tier-1
   calls cached, so they are not speed evidence). Still needed for test-1: `config.run_git_dirty_paths`
   (section 7.2, step 2), and in `cost` the uncached-vs-marginal split, cache status, retries, failed-call spend,
   and concurrency (section 4.4).
5. Whether the test pipeline runs the perception planner (section 4.4 says how each case is reported).

**Not blocking; reported as limitations:**
6. **Frame alignment.** Dev shows a consistent best lag of -2 frames (range -4 to 0; section 4.3). The draft
   said to fix a consistent offset in the harness. v1-final does **not**: it is unclear whether this is a
   label offset or a lag between SAM centroids and the keypoints, and all dev numbers use lag 0. It is a
   deviation from the draft and is disclosed. A label-free dev check (F1 sensitivity to a +/-2 frame shift)
   could settle it, but adopting a shift for test would need an amendment before test-1.
7. **The supervised .793** has its metric and split confirmed only through a scout's quotes of the paper. Its
   per-class F1 is unconfirmed. Inter-annotator agreement (MARS Fig. 5 supplements 3-4) hasn't been retrieved,
   so it is reported as "not retrieved", never estimated.
8. The 3-4x manual-scoring figure has no primary source in MARS (section 4.4).
9. The official CalMS21 scorer code wasn't retrieved, so the zero-division convention is ours (section 4.1).
10. Tier-1 accuracy on the windows it judged (section 6, item 1) isn't computed by `eval/` on test, and is
    reported as "not computed" unless a label-free verdict log and scorer exist before test-1.
11. Not implemented: bout count, latency, and ICC agreement (section 4.2); MAP (no per-frame confidence).

**Resolved since the draft:**
- the pooled F1 convention (section 4.1);
- overlap priority mount > attack > investigation, frozen in the config;
- bout IoU: 0.3 and 0.5 are both reported, with 0.3 primary;
- SAM chunking: 60 s chunks, which is ADR-001 option C, recorded in the frozen config.

**[A1] Resolved in the scorer:** video frame count vs annotation length. The runner logs each video's decoded
`src_frames`; `eval.score_arms` compares it with the index `frames` before any label is opened and refuses on an
unexplained mismatch (section 7.2, step 3). On the 5 dev-holdout pre-flight videos all five match (checked
label-free, 2026-10-28). The scorer still pads a short accepted prediction with "other" when scoring.

## 12. Changelog: draft v1 to v1-final (2026-10-28)

The draft v1 is commit fcd187c (sha256 of the file then:
`4820cab1ccdf32b68b780251ded6fc1c630df2a386619c813a451b011576a712`).

- **Targets frozen (section 10).** The draft's guesses were lowered on dev and holdout evidence:
  - macro >= 0.55 / 0.70 became >= 0.40 (minimum credible) / 0.50 (good);
  - attack .45 became .25 / .40; investigation .55 became .40 / .50; mount .65 became .40 / .55;
  - speed <= 0.3x became <= 1.0x (Aniket's headline) / 0.5x;
  - added: tier-1 lift, identity, and coverage targets.

  A projection of the test effect (longer videos, sparse attack, more mount) was added, and the
  supervised .793 is stated as a reference, not a target.
- **Test-run protocol rewritten (section 7):**
  - four arms from one pipeline execution, all hashed in a single `started` record before labels load
    (`eval/score_arms.py`);
  - `eval.score` now refuses the test split;
  - frozen-config hashes are verified at start;
  - per-sequence results are sealed until the test-2 decision is committed;
  - the identity check on test uses frozen parameters;
  - a pre-flight regeneration check, reporting rules for every arm (including a losing few-shot arm), and
    a qualifying vs exploratory test-2 rule. The headline is test-1 unless test-2 qualifies (superseded by A1:
    test-1 is the headline; section 7.4).
- **Disclosure (section 1):**
  - how dev-holdout was actually used (7 looks; it informed the few-shot self-check);
  - the evaluator re-read the test aggregates for the projection.
- **Definitions:**
  - speed and cost per video-minute (section 4.4);
  - frozen identity parameters and the dev alignment result (section 4.3);
  - option A only (section 2);
  - duration agreement implemented and other summary statistics marked not implemented (section 4.2);
  - test ablations are exactly the four arms (section 6).
- **Costs (section 8):** the unmeasured per-call guess was replaced with measured per-window figures and a test-1
  estimate. Fireworks spend was flagged as not covered by the SAM pre-approval.
- **Code (eval/ only):**
  - `eval/score_arms.py`, a multi-arm scorer with paired bootstrap differences, sealing, the frozen-config
    check, and target verdicts;
  - `eval/targets_v1.json`;
  - `eval/tests/test_score_arms.py`;
  - the test-split refusal in `eval/score.py`.

  The full eval suite passes (`uv run pytest -q eval/tests`). Dry run on existing holdout predictions:
  `score-dev_holdout-20261028T081858Z-325a87`. It reproduced all four logged holdout runs exactly, with the same
  prediction hashes.

## 13. Amendments (dated; append only after this plan is committed)

### A1 (2026-10-28, ~08:50 UTC): Codex eval-plan gate reconciliation, before any test run

**Context.** The Codex eval-plan gate (`ops/codex/ask-20261028-082550-84666.md`, gpt-6-astra, reasoning xhigh)
reviewed v1-final (e68f1e7) and recommended holding test-1 until specific issues were fixed. The lead directed
the amendments below. No test label, video, keypoint, or prediction was opened. No model call was made. The
in-place edits are tagged [A1]; v1-final's wording is in e68f1e7.

**What changed, and why.**
1. **Headline (7.3, 7.4, 10).** Test-1 is the headline. v1-final route (b), where a "dev-justified change" could
   make test-2 the headline, is removed: once test-1 aggregates are known they can steer development without
   showing up in a justification. Test-2 is exploratory, with one exception: a specific implementation defect
   that can be shown without test labels. In that case both results are kept, shown together, and the
   correction is named.
2. **Supervision wording (1, 2, 5, 10).** The headline phrase "zero-shot / no labeled training frames" is
   replaced with: "No task-specific model fine-tuning and no labeled examples in any prompt; pipeline settings
   (prompts, thresholds, cascade rules) were selected using labeled development videos." The correction arm
   stays "few-shot (N corrections)". Test-1 is called the "first test prediction evaluation", not first
   test-label access. The claim that test aggregates influenced no pipeline setting is narrowed: they shaped the
   dev set and the 10 fps rate. "Dev-holdout ... not tuned on" (section 2) now defers to the candid section 1
   account. *The product's public one-liner is Aniket's decision and is not changed here.*
3. **Pre-label validation (7.2, `eval/score_arms.py`).** Before any label file is opened, and before the
   `started` record, the scorer checks: files present for every sequence and arm; npz/jsonl syntax;
   `sample_fps` and `sample_stride`; label codes; `n_samples == ceil(src_frames/3)`; file length == `n_samples`;
   `src_frames` == index `frames`; failed-sequence placeholders declared in `failed_sequences` and all
   "other"; identical `frames` and `failed_sequences` across arms. Any unexplained problem refuses the run,
   logged as `kind: "preflight", status: "refused"`. That record is not a test run. A length mismatch can be
   accepted only by name and reason, and never if it would make the file unscorable. `--preflight-only` runs the
   same checks with no labels at all. Codex also asked for chunk continuity; the manifest doesn't record chunks,
   so that isn't checked (the lead could add it).
4. **Freeze coverage (7.2, `eval/score_arms.py`).** The scorer checks:
   - exactly the four named arms, primary `zs_t1`, and all four planned comparisons;
   - each manifest's `arm` and `config` (frozen sha256, `frozen_at_commit`, a single `run_commit`, and
     `run_git_dirty_paths` with nothing under `etho/`, `configs/`, or `scripts/test_run.py`);
   - `git diff <frozen_at_commit> <run_commit> -- etho/ ':!etho/app'` is empty. **The web app (`etho/app/`) is
     outside the freeze**, by the lead's direction (2026-10-28): it will be merged to main before test scoring
     and never runs in the scoring pipeline. `scripts/test_run.py` uses the same pathspec. `etho/app/` paths may
     also be dirty when the runner starts. What makes the exclusion safe is a check the scorer runs: at
     `run_commit`, `git grep` finds no import of `etho.app` (absolute or relative) in the frozen tree or in
     `scripts/test_run.py`. If it finds one, the run is refused. At the time of writing `etho/app/` doesn't exist
     at HEAD and no such import exists;
   - the tier-1 prompt hash at `run_commit` equals `tier1.prompt_sha256` (computed with `ast`; it reproduces
     `bb53933f...` at 4ada51c and at 69ab02c);
   - the frozen config and its assets are byte-identical at `run_commit`.

   Prediction payload hashes (`payload_sha256`) are logged separately from the manifest hash, and the pre-flight
   equality is on payloads.
5. **Claim rules (5, 7.3, 10.2, 10.3, `targets_v1.json`, `score_arms.py`).**
   - No "matches" or equivalence claim on CI overlap; report the observed gap.
   - A paired CI containing 0 is "inconclusive" (the scorer prints `ci_reading`).
   - Lower-is-better targets are MET ROBUSTLY only if the CI upper bound is at or below the threshold
     (direction-aware `_verdict`; directions in `targets_v1.json`).
   - An accuracy level requires macro F1 and all three per-behavior thresholds (`f1_level`). There is no single
     overall "success".
   - "Minimum credible" is an engineering milestone, not scientific validation, and the 0.38-0.58 range is a
     planning judgment.
   - The bootstrap's scope is stated (4.1, `metrics.bootstrap_scope`). The .793 +/- .011 is a std over
     training runs, not a CI.
   - Per-behavior undefined-bootstrap fractions are kept in the result (`ci95_undefined_frac`, `eval/score.py`).
6. **Identity diagnostics (4.3, `eval/identity.py`).** Unknown rate, 3+ detections, and both-labeled coverage
   are "retained-output diagnostics". The exported tracks keep only resident/intruder, so the first two are zero
   by construction. The evaluator confirmed this by reading `etho/tracking/pipeline.py`.
7. **Speed (4.4, 10.3, `score_arms.speed_from_manifest`).** Speed is the per-video sum of the stage wall-clocks
   (track including chunking and SAM, rules, tier-1), not one measured file-to-output interval. All 19 videos
   are in the denominator, failures included. Targets use pooled, median, and max, computed by the scorer from
   the `zs_t1` manifest.
8. **Manual baseline (4.4, 10.3).** The 3-4x MARS figure supports a contextual runtime comparison, not
   demonstrated labor savings at equal quality.

**Not changed.** Targets' numeric thresholds, metrics, splits, arms, the frozen config, and the identity
parameters.

**Codex points not adopted, and why.**
- "Alternatively commit the second configuration before revealing test metrics": not needed, since no test-2
  headline route remains.
- "Decide the planner configuration explicitly": still open item 5 (lead's).
- Complete uncached-vs-marginal cost reconstruction: a runner requirement, listed in section 11 item 4.

**Verification.** `uv run pytest -q eval/tests`: 87 passed (2026-10-28); 90 passed after the `etho/app`
exclusion and its three tests were added. Label-free strict preflight of the
dev-holdout pre-flight outputs: `score-dev_holdout-20261028T084711Z-05b1b3` (refused only for the missing
`run_git_dirty_paths`). Non-strict preflight of existing dev directories passed (scratch log, not in
`runs.jsonl`).

### A2 (2026-10-28, ~18:00 UTC): adds section 14 (second species, Fly vs. Fly). PROPOSED
Adds a new, separate protocol. It changes no CalMS21 item: no metric, split, target, frozen config or test
accounting in sections 1-13. Fly test runs are logged with `split: "flies-test"`, so `eval/common.py`'s CalMS21
test-run accounting never counts them, and the reverse holds too.

## 14. Second species: Fly vs. Fly (Aggression)

**FLIES-PLAN-STATUS: PROPOSED.** This section waits on the Codex eval-plan critique (`bin/codex-ask --gate
eval-plan`). Its findings get reconciled in a dated amendment. After that the marker becomes FROZEN, and
`eval/flies_criteria_v1.json` `"status"` becomes `"FROZEN"`, in one commit **before any fly prediction exists**.
`eval/flies_score.py` refuses the test split while this marker or the criteria file still says PROPOSED. No
test-config hashes are frozen yet, because no pipeline config or predictions exist.

Source: ADR-002 in docs/DECISIONS.md, approved by Aniket 2026-10-28 with conditions (decision log, same date).
Code: `eval/flies_extract.py` (labels, GT tracks, train summary, manifest), `eval/flies_score.py` (scorer, test
guard, criteria), `eval/flies_criteria_v1.json` (pre-registered criteria), tests `eval/tests/test_flies.py`.

### 14.1 Data, license, integrity
- Caltech Fly vs. Fly, **Aggression** subset. CaltechDATA record zrznw-w7386, DOI 10.22002/D1.1893, **CC0**
  (`metadata.rights` = cc-zero). Paper: Eyjolfsdottir, Branson, Burgos-Artizzu, Hoopfer, Schor, Anderson,
  Perona, "Detecting Social Actions of Fruit Flies", ECCV 2014, LNCS 8690, doi:10.1007/978-3-319-10605-2_50.
  Cite both in the README. CC0 doesn't require this; we do it anyway.
- 10 movies, 144x144 px, 30 fps, 54,000 frames (30 min) each, 2 male flies. `Aggression.zip` sha256
  `4c66aab8...8f4498`, matching the value recorded at download. The zip holds no movie11, which ADR-002 had
  mentioned.
- `eval/flies_manifest.json` holds the sha256 of every file used (42 files: per movie the mp4 and the
  actions/feat/track .mat, plus movie6's second annotation and traintest.mat), the windows and the split. On
  test, the scorer re-hashes the files it reads against the manifest and refuses on any difference. No raw
  data, .mat file or frame is committed; `data/` is gitignored.
- **What has been seen of the test movies (6-10), in full.** We have their file names, which show that movie6
  has a `movie6_second_actions.mat`. We have their sha256 hashes, computed over bytes without parsing. And
  `unzip -l` printed every file's uncompressed size: the test `_actions.mat` files are 665-3,316 bytes. Size
  is a weak proxy for the number of bouts; we disclose it and never use it. No test `.mat` was opened, loaded
  or parsed. `eval/flies_extract.py` refuses test movies unless `allow_test=True`, which only the guarded
  test path passes. `traintest.mat`, which holds only ids and names, was read to verify the split.

### 14.2 Label format (from train movies 1-5)
- `movieN_actions.mat`: `behs` = lunge, wing_threat, charge, hold, tussle, unknown. `bouts` is a 2x6 cell
  (row = fly, column = behavior). Each cell is an [n, 3] array `[start, end, flag]`.
- **Index convention: MATLAB 1-based, inclusive.** Python frames `[start-1, end)`. Evidence, measured on train:
  - The GT track's row i is decoded video frame i. Frame-difference vs track-speed correlation peaks at lag 0
    on movies 1-2 (0.77 and 0.87, against 0.69 and 0.67 at lag -1).
  - Wing-angle onset falls at python `start-1` in 50 of 63 wing-threat bouts.
  - The modal last wings-up frame is `end-1` (24 of 61).
  - The lunge's peak displacement lands on `end-1` (897 of 1,530).
  - `charge` has bouts with `end == start`, which is only valid if `end` is inclusive.
- `flag` (third column) is 0 or 2. It is 2 only on a few lunge bouts (movies 2-5) and 2 wing-threat bouts
  (movie4). Its meaning is undocumented. Recorded and ignored: the bout counts either way.
- `tussle` is annotated identically on both flies (a pair action). `charge`, `hold` and `tussle` are not scored
  (ADR-002 b).
- **`unknown`** is per fly. The scorer **excludes every frame where either fly is `unknown`** from every count:
  pair, per fly, frame and bout. There is one exclusion mask per movie window. A scored behavior that
  overlaps `unknown` is excluded with it. On train this happens on 0-55 frames per full movie. In the windows
  it is only movie4's lunges: 2 frames in mid5 and 6 in mid10. Wing threat is never affected. For bout metrics, excluded frames are set negative in both GT and prediction, so a bout is cut
  at the exclusion.
- `movieN_track.mat` (eval only, never product code): `trk.data` [2, 54000, 17] holds pos x/y (px), ori,
  major/minor axis, wing tips/angles/lengths and areas. Fly f in the track is fly f in `bouts` (same tool
  chain; assumed, not independently verified).

### 14.3 Windows, splits, frame mapping
- Windows were fixed by rule (ADR-002 d) before any test label was read. Native python frames, half-open:
  **mid5 = [22500, 31500)** (middle 5 min, 9,000 frames) and **mid10 = [18000, 36000)** (middle 10 min,
  18,000 frames).
- Configurations:
  - **Lunge GO:** both behaviors, mid5, 30 fps tracking.
  - **Lunge NO-GO:** wing_threat only, mid10, 10 fps.
- Splits (official `traintest.mat`, verified):
  - **dev** = train movies 1-2. All tuning happens here.
  - **spare** = movies 3-5. Not tuned on; used only for the wing-threat go/no-go (14.6).
  - **train** = 1-5.
  - **test** = 6-10, **at most two test runs** (`test-1`, `test-2`). Run 2 may reuse cached tracks at $0.
    Every run is reported.
- 10 fps predictions are mapped to 30 fps by the CalMS21 nearest-sample rule (section 3). Window frame i takes
  sample `min(round(i/3), K-1)`, and sample k is native frame `start + 3k`. Both window starts are multiples of
  3, so the 10 fps grid is the absolute frame grid 0, 3, 6, ...

### 14.4 Metrics
- **Primary: pair-level frame F1 per behavior.** A frame is positive if either fly does the behavior; GT is
  the union of both flies' bouts. Precision, recall and F1 are pooled over all scored frames of all movies of
  the split, at 30 fps (the CalMS21 convention, section 4.1). There is no macro F1: the behaviors are reported
  separately.
- **CIs.** 95% percentile CIs come from a bootstrap that resamples whole movies (10,000 resamples, seed
  20261028). With 5 movies the CI is coarse; it is a resampling statement about these movies, not about other
  labs. Resamples where F1 is 0/0 are dropped and their fraction is reported.
- **Random reference (the "floor").**
  - **Prevalence-matched iid:** every scored frame is positive with probability p, the pooled prevalence of the
    scored frames. Expected precision, recall and F1 all equal p. It is checked by simulation (200 draws).
  - **Bout-shuffled (for bout metrics):** each movie's GT bouts keep their count and durations and are placed
    at uniform random starts in the window. We report the mean over 200 draws.
  - Bout-shuffling keeps per-movie prevalence, so its frame F1 is above the iid one. It uses more label
    information and is a stronger floor; it is reported, but the frame-level bars use iid.
- **Bout-level F1 (lunge's main secondary).** Bouts are the maximal runs of the pair masks. Matching is
  **one-to-one, any overlap** (at least 1 shared frame), greedy by temporal IoU, so one giant predicted bout
  matches at most one GT bout. IoU >= 0.3 and 0.5 are also reported. CIs come from the same movie bootstrap.
- **Secondary per-fly (actor) F1.** Computed only if the predictions carry `<behavior>_by_track` and `track_xy`
  for every movie. Each Etho track is mapped to a GT fly by **one permutation per window**: the per-sample 2x2
  Hungarian assignment on centroid distance to the GT track, with the window's majority assignment as the
  permutation (ties go to identity). Identity errors therefore count as per-fly errors. Train note: within a
  window the aggressor is almost always one fly. Movie2 fly0 does 6/6 wing threats and 75/75 lunges in mid5.
  Movie3 fly1 does 4/4 and 67/67. In movie4, fly1 does 9/9 wing threats and 87 of the 92 pair-level lunge
  bouts. So per-fly F1 tests "who is the aggressor"
  plus identity, more than per-bout actor choice.
- **Identity** (report only, no target). Per window:
  - per-sample identity accuracy against the window permutation, on all evaluable samples (both tracks
    present, both GT flies present, not declared unknown), and on samples where the GT flies are at least 1
    body length (mean GT major axis) apart;
  - assignment switches per minute (a one-sample flip counts 2; see the 2026-10-28 DECISIONS line);
  - error episodes per minute (a flip counts 1);
  - % of samples with identity unknown or a track missing;
  - fraction of samples with both tracks present;
  - median matched centroid distance in px, a sanity check for coordinate bugs such as forgetting to divide out
    a 4x upscale.
- **Inter-annotator reference (test run only).** `movie6_second_actions.mat` is scored against the primary
  annotation (pair-level frame and bout F1, same window). This is one movie; it is a reference, not a target.
  Its format and coverage are unverified (not opened). If it fails to parse, the error is recorded and the run
  continues.
- **Cost and time** per window, from the run manifest, as in section 4.4.

### 14.5 Train-window summary (train movies 1-5 only; `eval/results/flies_train_summary.json`)
Pair-level bouts are maximal runs of the union of both flies' masks.

| Window | Movie | wing_threat bouts / frames | lunge bouts / frames | unknown frames |
|---|---|---|---|---|
| mid5 | 1 | 0 / 0 | 2 / 8 | 0 |
| mid5 | 2 | 6 / 1,186 | 75 / 303 | 0 |
| mid5 | 3 | 4 / 88 | 67 / 271 | 0 |
| mid5 | 4 | 9 / 659 | 92 / 393 | 357 |
| mid5 | 5 | 0 / 0 | 4 / 16 | 0 |
| mid5 | **pooled** | **19 / 1,933 (movies 2,3,4)** | **240 / 991** | 357 |
| mid10 | 1 | 0 / 0 | 10 / 40 | 0 |
| mid10 | 2 | 16 / 1,895 | 163 / 670 | 0 |
| mid10 | 3 | 8 / 577 | 135 / 548 | 0 |
| mid10 | 4 | 15 / 1,913 | 131 / 553 | 433 |
| mid10 | 5 | 0 / 0 | 29 / 117 | 0 |
| mid10 | **pooled** | **39 / 4,385 (movies 2,3,4)** | **468 / 1,928** | 433 |

- Durations: lunge pair bouts have a median of 4 frames (133 ms) in every movie and window, and a max of 9
  (adjacent or overlapping lunges merge). Wing-threat pair bouts have a per-movie median of 18.5-129 frames
  and a max of 987 frames in mid10. Movie4 fly1 has two very long full-movie wing-threat bouts (9,765 and
  6,942 frames, starting at frames 35,013 and 47,056), and the first is clipped into mid10. They are kept as
  annotated.
- Other actions in the windows (mid5 pooled): charge 11 bouts / 16 frames, hold 16 / 251, tussle 4 / 238.
- **Random references** (iid expected F1 = prevalence; bout-shuffled mean bout F1, 200 draws):

| Window | Split | wing_threat iid F1 | wing_threat shuffled bout F1 | lunge iid F1 | lunge shuffled bout F1 |
|---|---|---|---|---|---|
| mid5 | dev (1-2) | 0.0659 | 0.2242 | 0.0173 | 0.0525 |
| mid5 | spare (3-5) | 0.0280 | 0.0908 | 0.0254 | 0.0638 |
| mid5 | train (1-5) | 0.0433 | 0.1450 | 0.0222 | 0.0615 |
| mid10 | dev (1-2) | 0.0526 | 0.1807 | 0.0197 | 0.0619 |
| mid10 | spare (3-5) | 0.0465 | 0.1131 | 0.0226 | 0.0473 |
| mid10 | train (1-5) | 0.0490 | 0.1430 | 0.0215 | 0.0523 |

- **Consequence for the go/no-go:**
  - In the dev windows (movies 1-2), wing threat occurs only in movie2: 6 bouts in mid5, 16 in mid10.
  - A movie bootstrap over 2 movies, one of them without the behavior, can't show "clearly above".
  - The wing-threat check therefore needs predictions on the spare movies too (14.6, cost in 14.8).

### 14.6 Pre-registered go/no-go criteria (train only, mechanical, `eval/flies_criteria_v1.json`)
Bars are relative to the reference r of the **same scored frames**, recomputed by the scorer at run time. The
bar form is `max(2r, r + 0.15)`. The relative term matters only when prevalence exceeds 15%. The absolute term
(+0.15 F1) is what "clearly" means at the low prevalences here (2-7%), where "2x chance" alone would allow F1
of 0.05-0.13.

**(b) Lunge go/no-go: decided first.** ADR-002, plus one evaluator guard. GO needs all of the following, on a
scoring run of dev movies 1-2 (or all of train) at mid5, 30 fps:
1. **SAM sees 2 flies in >= 90% of samples.** This is measured as the fraction of window samples where both
   Etho tracks are present (non-NaN `track_xy`), averaged over movies.
2. **Pair-level lunge bout recall >= 0.5**, one-to-one any-overlap, pooled.
3. **Guard (added by the evaluator).** Bout F1 >= max(2r_b, r_b + 0.15), with movie-bootstrap CI lower bound >
   r_b, where r_b is the bout-shuffled reference. Without it, predicting many short bouts would pass the
   recall criterion alone. On dev mid5, r_b = 0.0525, so the bar is 0.2025; at recall 0.5 this needs bout
   precision >= about 0.135.
4. **Evidence:** >= 30 GT lunge bouts. Dev mid5 has 77.

If NO-GO: wing_threat only, mid10 at 10 fps.

**(a) Wing threat go/no-go.** Aniket's rule: "if wing threat isn't clearly above the random floor on the
training split by end of Oct 31 ET, ship it as a qualitative demo clip with no F1 claim." GO (F1 claim) needs
all of the following:
1. A scoring run on **all five train movies** (`--split train`, no subset), in the window of the test
   configuration (mid5 at 30 fps if lunge is GO, else mid10 at 10 fps), with the frozen dev-tuned pipeline.
2. Pooled pair-level frame **F1 >= max(2r, r + 0.15)**. Train mid5: r = 0.0433, bar 0.193. Train mid10:
   r = 0.0490, bar 0.199.
3. Movie-bootstrap 95% **CI lower bound > r**.
4. **Evidence:** >= 15 GT wing-threat bouts across >= 3 movies with wing threat. Train mid5 has 19 in movies
   2-4; mid10 has 39.
5. Scored by **2026-10-31 23:59:59 EDT** (2026-11-01 03:59:59 UTC). The scorer records the time; a later run
   is NO-GO.

- Anything else is NO-GO, including missing evidence or a run that never happened: a qualitative clip and no
  F1 claim for flies.
- Also reported: F1 on spare movies 3-5 alone, from a `--split spare` run on the same predictions. They are
  not tuned on, so this is less optimistic than the pooled number that includes the dev movies. The decision uses the pooled train number (Aniket's "training split").
- At most **two** go/no-go executions on the full train split. The second is allowed only after changes tuned
  on dev movies 1-2. Both are reported, and the decision is the last one before the deadline.
- The spare movies are never used for tuning. Using them would turn them into dev.

### 14.7 Test targets (pre-registered; relative to the test windows' own reference r_test)
There is no comparable published number (14.9), so targets are stated against chance on the same frames.
r_test is the prevalence of the scored test frames, computed by the scorer. Nobody has looked at it.
- **wing_threat, pair-level frame F1:**
  - **credible:** F1 >= max(2r, r + 0.15) AND CI lower bound > r;
  - **good:** F1 >= max(3r, r + 0.30) AND CI lower bound >= max(2r, r + 0.15).
- **lunge (only if GO):** the same two levels on pair-level frame F1, and separately on one-to-one any-overlap
  bout F1, with r_b = bout-shuffled reference.
- For scale only, the train references give illustrative bars. Wing threat mid5: credible 0.193, good 0.343.
  Lunge mid5 bout F1: credible 0.212, good 0.362.
- **Evidence rule.** If the pooled test windows have fewer than 10 GT wing-threat bouts, or fewer than 3 movies
  with wing threat, the verdict is **NOT EVALUABLE**. For lunge the thresholds are 30 bouts and 3 movies. The
  number is reported, with no claim.
- The scorer prints MET / NOT MET mechanically. Per-fly F1, identity and the inter-annotator reference are
  report-only.
- **Wording.** Every fly number states: pair-level scoring (README discloses this, per Aniket's condition);
  middle-window subset, not full movies; thresholds tuned on train movies 1-2; 5 test movies. It may be called
  "no labeled examples in any prompt and no model training", as in section 1. Never "beats".

### 14.8 Cost of the protocol (SAM $0.20 per 1k frames; estimates, nothing spent by this plan)
| Step | Frames | SAM cost |
|---|---|---|
| Dev: movies 1-2, mid5, 30 fps (ADR-002) | 18,000 | $3.60 |
| **Wing go/no-go, lunge GO:** spare movies 3-5, mid5, 30 fps | 27,000 | **$5.40** (job > $5: escalate) |
| **Wing go/no-go, lunge NO-GO:** movies 1-5, mid10, 10 fps | 30,000 | $6.00 (the dev mid5 30 fps tracks don't cover mid10, so movies 1-2 are re-tracked) |
| Test, lunge GO: movies 6-10, mid5, 30 fps | 45,000 | $9.00 |
| Test, lunge NO-GO: movies 6-10, mid10, 10 fps | 30,000 | $6.00 |

- **Lunge-GO path:** $3.60 + $5.40 + $9.00 = **$18.00 SAM**, plus about $0.5-1 LLM. That is 90-95% of the $20
  `ablation` tag, which crosses 80% (escalation), against ADR-002's approved $13.60.
- **NO-GO path:** $3.60 + $6.00 + $6.00 = $15.60.
- Both exceed the approved $13.60. **The spare-movie step is not funded by ADR-002, and Aniket must decide
  it.** Options:
  - fund it;
  - track only movies 3-4, which have wing threat: $3.60 at mid5. This needs `require_full_split: false`
    and a changed evidence rule, fixed before any prediction exists;
  - accept a NO-GO by rule: qualitative clip, $0 extra.
- The evaluator recommends the full split. A check on movies 2-4 alone would drop the two zero-wing-threat
  movies, where false positives are the main risk.

### 14.9 Published baseline
- Eyjolfsdottir et al., ECCV 2014 is the reference: supervised, per fly, full test movies.
- Its per-action numbers were **not retrieved**. Unpaywall and OpenAlex mark the paper closed, with no
  open-access copy. Semantic Scholar's openAccessPdf is empty. The CaltechAUTHORS record s51ck-2s169 has its
  files disabled. vision.caltech.edu dataset/publication URLs return 404 or "moved", and CiteSeerX returns 404.
- **No number is quoted.** If it is retrieved later, quote it with its table reference and the label
  "supervised, full movies, per-fly, not directly comparable" (our numbers are pair-level, from middle windows,
  without training). Never "beats".

### 14.10 Prediction format (for the implementer; full spec in the `eval/flies_score.py` docstring)
One `movieN.npz` per movie in `--pred-dir`, plus optional `manifest.json` (`config`, `cost`, `wall_clock_s`,
`calls`, `notes`; on test, `config.frozen_sha256` = sha256 of the frozen config, which must contain
`"window"`):
- `window` int[2]: exactly `[22500, 31500]` (mid5) or `[18000, 36000]` (mid10);
- `sample_fps` (30 or 10);
- `wing_threat` and `lunge` 0/1 [K]: **pair-level**, K = ceil(window_len / step), sample k = native frame
  `start + k*step`, exact length;
- optional `<behavior>_by_track` 0/1 [2, K], the actor per Etho track;
- optional `track_xy` float [K, 2, 2], centroid per (sample, track, x/y) in **native 144x144 px** (divide out
  any upscale), NaN when absent; needed for identity, per-fly F1 and the lunge coverage criterion;
- optional `identity_unknown` bool [K].

Run: `uv run python -m eval.flies_score --split dev --window mid5 --pred-dir <dir> --criteria
eval/flies_criteria_v1.json`.

### 14.11 Test-run order (flies)
1. Dev tuning on movies 1-2.
2. Lunge go/no-go (dev, mid5, 30 fps).
3. Freeze the pipeline config: `configs/frozen_flies_v1.json` with `"window"` and `{"path", "sha256"}` assets.
4. Track the spare movies (if funded). Run the wing go/no-go on all of train, before the Oct 31 ET deadline.
5. Codex eval-plan critique of this section, amendment, then this marker and the criteria file set to FROZEN,
   committed.
6. Produce test predictions with the frozen config.
7. `uv run python -m eval.flies_score --split test --window <w> --purpose test-1 --confirm-test-run
   --frozen-config configs/frozen_flies_v1.json --pred-dir <dir>`. The predictions hash is logged before any
   label is read; per-movie metrics are sealed.
8. Codex test-results gate. The test-results gate is one of the three gates; whether a fly run needs its own
   gate is the lead's call.
