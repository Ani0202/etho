# Etho on CalMS21 Task 1, test run 1 (test-1): results report

Author: evaluator subagent. Written 2026-10-28 (~16:45-17:10 UTC), right after scoring. Protocol: `docs/EVAL_PLAN.md`
v1-final + amendment A1 (sections 4, 7, 10). This is the **only** scoring of test-1. Every pre-registered arm
is reported, the weak ones included.

- Scoring run: `score-test-20261028T164333Z-473c64` (kind `score`, purpose `test-1`). Its `started` record
  was logged before any label was read; status `ok`; no warnings.
- Identity run: `identity-test-20261028T164348Z-cdc866` (kind `identity`, purpose `test-1`, status `ok`).
- Label-free preflight before scoring: `score-test-20261028T164327Z-0cabb1` (kind `preflight`, `ok`).
- Full results: `eval/results/score-test-20261028T164333Z-473c64.json` and
  `eval/results/identity-test-20261028T164348Z-cdc866.json`. Per-sequence F1 is sealed in
  `eval/results/score-test-20261028T164333Z-473c64.per_sequence.sealed.json` (sha256 `88f361fe...0451`).
  Nobody has opened it. It stays sealed until the test-2 decision is committed (EVAL_PLAN 7.2).

> **Corrected after the Codex test-results gate: see section 11**, which supersedes the speed figures and
> verdicts, the headline status, the rounding policy, and several smaller items below.

**Rounding.** F1, precision, recall, CI bounds and shares are **truncated** to 4 decimals (never rounded up).
Speed and cost are rounded **up**. Paired differences are rounded away from zero toward the unfavorable side.
Full-precision values are in the result JSON. All target verdicts were computed at full precision.

## 1. Supervision statement (attach to every number below)

> **[2026-10-28, after the gate] Headline wording: pending Aniket's decision.** Both versions are in section
> 11.2. "No setting was tuned on test" below is replaced by "No test prediction scores were used to select
> this configuration."

The primary arm `zs_t1` is described as follows (Aniket's approved wording, DECISIONS.md 2026-10-28):

> **Training-free: plain-English behavior definitions, no labeled examples, no model training.**¹
>
> ¹ Pipeline settings were tuned on 13 labeled development videos; test results are on 19 untouched videos.

What the evaluator verified about this wording:
- **"13 development videos" is correct.** The split file `eval/splits/calms21_v1.json` (sha256 `59bfcd47...880b`,
  the frozen hash) lists 8 `dev` and 5 `dev_holdout` sequences, all from the official train split.
  Strictly, settings were *tuned* on the 8 dev videos. The 5 dev-holdout videos were scored 7 times and
  informed the few-shot self-check design (EVAL_PLAN section 1). "Tuned on 13" is therefore the broader
  disclosure, and it does not flatter the result.
- **"19 test videos" is correct** (the 19 official CalMS21 Task 1 test sequences, option A).
- **"Untouched" needs the plan's caveat.** No test video, keypoint, overlay, prediction, or verdict was
  inspected before scoring, and no setting was tuned on test. However, per-sequence **aggregates of test
  labels** (frame and bout counts per behavior) were read before test-1. They shaped the dev-set selection
  rule and the 10 fps decision, and they informed the target projection (EVAL_PLAN section 1, "Test-label
  aggregates informed the split design"). Wherever the footnote appears in long-form material (README, methods
  report), that disclosure should go with it. Whether to change the short footnote is Aniket's call.
- **"No model training"** means that no model was trained or fine-tuned for this task. The foundation models
  are pretrained, and whether CalMS21 was in their pretraining data is unverified (EVAL_PLAN A1).
- The few-shot arms (`fs_*`) are **"few-shot (18 corrections simulated from dev labels)"**, never zero-shot or
  training-free (section 5).

## 2. Headline table: all four arms

Frame-level F1 at 30 fps, pooled over all 262,107 frames of the 19 test videos (CalMS21 convention). Macro =
unweighted mean of attack, investigation, and mount ("other" excluded). 95% CIs come from a sequence-level
bootstrap: 10,000 resamples, seed 20261028, percentile. No sequence failed, and none was excluded.

| Arm | What | Macro F1 [95% CI] | Attack F1 [CI] | Investigation F1 [CI] | Mount F1 [CI] |
|---|---|---|---|---|---|
| **`zs_t1`** (primary) | training-free: R1 detectors + tier-1 (restricted) | **0.5800 [0.4927, 0.6212]** | 0.4492 [0.1729, 0.5595] | 0.6349 [0.5439, 0.6993] | 0.6558 [0.5044, 0.7438] |
| `zs_rules` | same detectors, no tier-1 | 0.3747 [0.2967, 0.4241] | 0.3239 [0.0977, 0.4633] | 0.2397 [0.1822, 0.2812] | 0.5605 [0.3701, 0.6850] |
| `fs_t1` | few-shot (18 simulated dev corrections) + tier-1 | 0.5723 [0.4750, 0.6221] | 0.4613 [0.1684, 0.5883] | 0.6817 [0.6003, 0.7399] | 0.5739 [0.4064, 0.6823] |
| `fs_rules` | few-shot detectors, no tier-1 | 0.4240 [0.3351, 0.4821] | 0.3379 [0.0984, 0.4843] | 0.4263 [0.3737, 0.4648] | 0.5077 [0.3272, 0.6351] |

References (same metric, same 19 sequences; see section 4 for comparability):

| Reference | Macro F1 | Attack | Investigation | Mount |
|---|---|---|---|---|
| Supervised 1D ConvNet on pose, trained on the 70 official train videos (Sun et al. 2021, Table 1) | .793 ± .011 (std over 5 training runs, not a CI) | not confirmed, not shown | not confirmed | not confirmed |
| Frequency-matched random predictor (computed by the scorer on these test frames) | 0.1344 | 0.0481 | 0.2337 | 0.1215 |
| Inter-annotator agreement (MARS, Segalin et al. 2021) | not retrieved | | | |

**Bootstrap scope** (recorded in the result as `metrics.bootstrap_scope`). The interval describes variation
across sampled sequences, *conditional on this fitted pipeline and its realized predictions*. It does not
cover the development choices, provider sampling noise (there was one execution), other labs, setups, or
species. It treats sequences as independent, which is not verified: consecutive sequences such as
mouse075-079 may share animals or sessions. All test attack frames are in 5 sequences (mouse075-079), so
the attack CI rests on 5 videos. In 0.30% of resamples no attack sequence was drawn; those resamples have
attack recall undefined and attack F1 = 0, as pooling implies.

### 2.1 Precision and recall

| Arm | Behavior | Precision [CI] | Recall [CI] | TP | FP | FN | GT frames | Pred frames |
|---|---|---|---|---|---|---|---|---|
| `zs_t1` | attack | 0.3292 [0.0994, 0.4550] | 0.7069 [0.4901, 0.8119] | 8929 | 18190 | 3701 | 12630 | 27119 |
| `zs_t1` | investigation | 0.6974 [0.5903, 0.7755] | 0.5827 [0.4926, 0.6531] | 35709 | 15488 | 25566 | 61275 | 51197 |
| `zs_t1` | mount | 0.6960 [0.4804, 0.8123] | 0.6200 [0.4979, 0.7203] | 19746 | 8623 | 12102 | 31848 | 28369 |
| `zs_rules` | attack | 0.2059 [0.0526, 0.3317] | 0.7592 [0.5734, 0.8348] | 9589 | 36977 | 3041 | 12630 | 46566 |
| `zs_rules` | investigation | 0.7884 [0.6608, 0.8677] | 0.1413 [0.1052, 0.1688] | 8663 | 2325 | 52612 | 61275 | 10988 |
| `zs_rules` | mount | 0.4708 [0.2642, 0.6343] | 0.6925 [0.5622, 0.7806] | 22056 | 24785 | 9792 | 31848 | 46841 |
| `fs_t1` | attack | 0.3479 [0.0985, 0.5069] | 0.6846 [0.5122, 0.7610] | 8647 | 16205 | 3983 | 12630 | 24852 |
| `fs_t1` | investigation | 0.7079 [0.6131, 0.7793] | 0.6573 [0.5751, 0.7244] | 40281 | 16620 | 20994 | 61275 | 56901 |
| `fs_t1` | mount | 0.7178 [0.5053, 0.8203] | 0.4781 [0.3213, 0.6098] | 15228 | 5986 | 16620 | 31848 | 21214 |
| `fs_rules` | attack | 0.2188 [0.0532, 0.3559] | 0.7417 [0.5628, 0.7970] | 9368 | 33437 | 3262 | 12630 | 42805 |
| `fs_rules` | investigation | 0.7253 [0.6294, 0.7934] | 0.3018 [0.2613, 0.3351] | 18498 | 7003 | 42777 | 61275 | 25501 |
| `fs_rules` | mount | 0.4932 [0.2785, 0.6514] | 0.5230 [0.3631, 0.6558] | 16659 | 17114 | 15189 | 31848 | 33773 |

**Attack is over-predicted.** `zs_t1` labels 27,119 frames as attack against 12,630 in the ground truth, and
precision is 0.3292. Of the 18,190 false-positive attack frames, 11,007 are expert-labeled investigation and
5,366 are "other" (confusion matrix, section 2.4). The predicted total attack duration is 904.0 s against
421.0 s, which is 2.15x.

### 2.2 Paired differences (b minus a, the same sequence resamples, 10,000 reps)

Reading rule (EVAL_PLAN 5 [A1]): a CI entirely above 0 means "increased", entirely below 0 means "decreased",
and a CI containing 0 means "inconclusive" (never "no effect").

| Paired difference | Macro [95% CI] | Attack [CI] | Investigation [CI] | Mount [CI] |
|---|---|---|---|---|
| Tier-1 lift, zero-shot: `zs_t1 - zs_rules` | +0.2052 [+0.1611, +0.2352] increased | +0.1252 [+0.0657, +0.1551] increased | +0.3952 [+0.3411, +0.4380] increased | +0.0952 [+0.0275, +0.1626] increased |
| Few-shot effect, full cascade: `fs_t1 - zs_t1` | -0.0077 [-0.0464, +0.0331] **inconclusive** | +0.0121 [-0.0327, +0.0537] inconclusive | +0.0467 [+0.0303, +0.0673] increased | -0.0819 [-0.1713, -0.0001] decreased |
| Tier-1 lift, few-shot: `fs_t1 - fs_rules` | +0.1483 [+0.1101, +0.1772] increased | +0.1234 [+0.0652, +0.1603] increased | +0.2553 [+0.2000, +0.2997] increased | +0.0662 [+0.0222, +0.1205] increased |
| Few-shot effect, rules only: `fs_rules - zs_rules` | +0.0492 [+0.0178, +0.0806] increased | +0.0140 [-0.0093, +0.0339] inconclusive | +0.1865 [+0.1601, +0.2130] increased | -0.0529 [-0.1301, +0.0230] inconclusive |

The mount decrease in `fs_t1 - zs_t1` has a full-precision upper CI bound of -0.0000670. It only just
excludes 0, and 2.48% of resamples are positive.

### 2.3 Secondary metrics (all arms)

**Bout-level P/R/F1** (one-to-one greedy match by temporal IoU):

| Arm | IoU | Attack P/R/F1 | Investigation P/R/F1 | Mount P/R/F1 |
|---|---|---|---|---|
| `zs_t1` | 0.3 | 0.196/0.349/0.251 | 0.499/0.359/0.417 | 0.357/0.549/0.432 |
| `zs_t1` | 0.5 | 0.087/0.155/0.112 | 0.336/0.242/0.281 | 0.253/0.390/0.307 |
| `zs_rules` | 0.3 | 0.091/0.372/0.146 | 0.209/0.085/0.121 | 0.202/0.605/0.303 |
| `zs_rules` | 0.5 | 0.039/0.159/0.062 | 0.103/0.042/0.060 | 0.150/0.451/0.226 |
| `fs_t1` | 0.3 | 0.247/0.570/0.344 | 0.509/0.487/0.498 | 0.328/0.421/0.369 |
| `fs_t1` | 0.5 | 0.151/0.349/0.211 | 0.341/0.327/0.334 | 0.204/0.262/0.229 |
| `fs_rules` | 0.3 | 0.112/0.640/0.191 | 0.283/0.308/0.295 | 0.191/0.477/0.273 |
| `fs_rules` | 0.5 | 0.064/0.364/0.109 | 0.135/0.147/0.141 | 0.128/0.318/0.182 |

Bout-level agreement is much weaker than frame-level agreement. Predicted bouts are fragmented: for example,
`zs_t1` attack bout F1 is 0.251 at IoU 0.3, against a frame F1 of 0.4492.

**Per-sequence F1**, mean / median over the sequences that contain the behavior. These are aggregates only;
the per-sequence values themselves are sealed.

| Arm | Attack (5 seqs) | Investigation (19) | Mount (14) |
|---|---|---|---|
| `zs_t1` | 0.5958 / 0.6223 | 0.5467 / 0.5869 | 0.5426 / 0.6127 |
| `zs_rules` | 0.5650 / 0.5952 | 0.1979 / 0.1913 | 0.5041 / 0.5573 |
| `fs_t1` | 0.6370 / 0.6602 | 0.6051 / 0.6503 | 0.4889 / 0.4835 |
| `fs_rules` | 0.5707 / 0.6038 | 0.3863 / 0.3824 | 0.4585 / 0.4632 |

**Duration agreement**: total scored seconds per behavior per video, over 19 videos.

| Arm | Behavior | MAE (s) | Mean signed error (s) | Pearson r | GT total (s) | Pred total (s) |
|---|---|---|---|---|---|---|
| `zs_t1` | attack | 28.36 | +25.42 | 0.9275 | 421.0 | 904.0 |
| `zs_t1` | investigation | 25.57 | -17.68 | 0.9285 | 2042.5 | 1706.6 |
| `zs_t1` | mount | 23.43 | -6.10 | 0.9401 | 1061.6 | 945.6 |
| `zs_rules` | attack | 60.36 | +59.54 | 0.7900 | 421.0 | 1552.2 |
| `zs_rules` | investigation | 88.23 | -88.22 | 0.9177 | 2042.5 | 366.3 |
| `zs_rules` | mount | 33.04 | +26.30 | 0.8198 | 1061.6 | 1561.4 |
| `fs_t1` | attack | 24.71 | +21.44 | 0.8690 | 421.0 | 828.4 |
| `fs_t1` | investigation | 19.20 | -7.67 | 0.9432 | 2042.5 | 1896.7 |
| `fs_t1` | mount | 27.71 | -18.66 | 0.9007 | 1061.6 | 707.1 |
| `fs_rules` | attack | 53.69 | +52.94 | 0.7792 | 421.0 | 1426.8 |
| `fs_rules` | investigation | 63.17 | -62.76 | 0.9266 | 2042.5 | 850.0 |
| `fs_rules` | mount | 25.21 | +3.38 | 0.8740 | 1061.6 | 1125.8 |

Pearson r is high (0.9275-0.9401 for `zs_t1`), even though attack total duration is overestimated by a factor
of 2.15. Researchers should read r as tracking *relative* differences between videos, not absolute
durations. Bout count, latency, and ICC are not implemented (EVAL_PLAN 4.2), so they are not reported.

### 2.4 Confusion matrices and non-exclusive F1

Rows are ground truth; columns are predictions, in the order [attack, investigation, mount, other], in frames.
- `zs_t1`: attack [8929, 1361, 1473, 867]; investigation [11007, 35709, 5688, 8871]; mount [1817, 3097, 19746, 7188]; other [5366, 11030, 1462, 138496]
- `zs_rules`: attack [9589, 611, 1882, 548]; investigation [21789, 8663, 19271, 11552]; mount [2170, 127, 22056, 7495]; other [13018, 1587, 3632, 138117]
- `fs_t1`: attack [8647, 2041, 848, 1094]; investigation [6959, 40281, 3786, 10249]; mount [5623, 3362, 15228, 7635]; other [3623, 11217, 1352, 140162]
- `fs_rules`: attack [9368, 1395, 977, 890]; investigation [16412, 18498, 13349, 13016]; mount [6556, 350, 16659, 8283]; other [10469, 5258, 2788, 137839]

One-vs-rest F1 *without* the exclusivity rule equals the exclusive F1 in every arm (for `zs_t1`: 0.4492 / 0.6349 /
0.6558, macro 0.5800). That is expected, because the pipeline writes per-sample npz files with exactly one
label per sample, so there are no overlaps for the priority rule to resolve.

MAP is not applicable, because Etho emits no per-frame confidence (EVAL_PLAN 4.1).

### 2.5 Tier-1 activity (label-free, aggregated over all 19 videos; no per-video breakdown)

These counts come from `data/runs/T1/verdicts_test_{zs,fs}_t1.jsonl`.
- **`zs_t1`: 3,241 windows.**
  - Tier-1 returned no errors and no "unsure" verdicts.
  - 3 verdicts fell below the 0.6 confidence threshold and kept the rule label.
  - 1,243 confident verdicts said attack or mount. The restricted cascade lets tier 1 assign only
    investigation or none, so those windows also kept the rule label (1,246 fall-backs in total).
  - The other 1,995 verdicts were applied. They changed the rule label in 1,455 windows:
    - attack to investigation, 450;
    - mount to investigation, 354;
    - other to investigation, 294;
    - attack to other, 228;
    - investigation to other, 85;
    - mount to other, 44.
- **`fs_t1`: 4,214 windows.** 9 verdicts fell below the confidence threshold. 1,403 windows fell back to the
  rule label, and 2,117 labels were changed.
- Tier-1 accuracy on the windows it judged is **not computed** (EVAL_PLAN 11, item 10).

## 3. Pre-registered targets (primary arm `zs_t1`, test-1)

> **[2026-10-28, after the gate]** The speed rows below omitted preprocessing. The corrected speed verdicts are
> in section 11.3; the full corrected summary is in section 11.8.

Thresholds and rules come from `eval/targets_v1.json` (sha256 `ec6b6a6d...ecf4b3`) and EVAL_PLAN 10.3.
Higher-is-better targets are MET ROBUSTLY only if the CI lower bound also reaches the threshold;
lower-is-better targets only if the CI upper bound does. A metric without a CI can be MET, but never MET
ROBUSTLY. An F1 level is reached only if macro F1 and all three per-behavior F1s meet it. All comparisons
use full precision.

| Target | Threshold | Measured | Verdict | Computed by |
|---|---|---|---|---|
| Macro F1, minimum credible | >= 0.40 | 0.580027 [0.492739, 0.621255] | MET ROBUSTLY | scorer |
| Attack F1, minimum credible | >= 0.25 | 0.449269 [0.172948, 0.559578] | MET (CI lower bound is below 0.25) | scorer |
| Investigation F1, minimum credible | >= 0.40 | 0.634985 [0.543960, 0.699354] | MET ROBUSTLY | scorer |
| Mount F1, minimum credible | >= 0.40 | 0.655828 [0.504440, 0.743861] | MET ROBUSTLY | scorer |
| **F1 level "minimum credible"** | all four | | **MET** (not robustly, because of attack) | scorer |
| Macro F1, good | >= 0.50 | 0.580027 | MET (CI lower bound 0.4927 < 0.50) | scorer |
| Attack F1, good | >= 0.40 | 0.449269 | MET (not robustly) | scorer |
| Investigation F1, good | >= 0.50 | 0.634985 | MET ROBUSTLY | scorer |
| Mount F1, good | >= 0.55 | 0.655828 | MET (CI lower bound 0.5044 < 0.55) | scorer |
| **F1 level "good"** | all four | | **MET** (not robustly) | scorer |
| Tier-1 lift, minimum credible | >= +0.03 | +0.205253 [+0.161155, +0.235287] | MET ROBUSTLY | scorer |
| Tier-1 lift, good | >= +0.10 | +0.205253 | MET ROBUSTLY | scorer |
| Identity error, all matches, minimum credible | <= 0.05 | 0.0029449 (485 / 164,692) | MET (no CI computed) | by hand, from the identity run |
| Identity error, good | <= 0.01 | 0.0029449 | MET (no CI) | by hand |
| Identity error on mount frames | flag if > 0.10 | 0.0284 | not flagged | by hand |
| Two-animal coverage (both labeled; retained-output diagnostic), minimum credible | >= 0.95 | 0.972612 | MET (no CI) | by hand |
| Two-animal coverage, good | >= 0.98 | 0.972612 | **NOT MET** | by hand |
| **Speed, minimum credible**: pooled <= 1.0x and median <= 1.0x | | pooled 0.8232x, median 0.8007x | **MET** (no CI) | by hand, see 6.1 |
| **Speed, good**: max <= 1.0x and pooled <= 0.5x | | max 1.4928x, pooled 0.8232x | **NOT MET** (both components fail) | by hand, see 6.1 |
| Cost per video-minute, uncached, minimum credible | <= $0.25 | $0.1923 (most conservative figure) | MET (no CI) | by hand, see 6.2 |
| Cost per video-minute, uncached, good | <= $0.20 | $0.1923 | MET (no CI). Without the planner; adding the planner for new users gives $0.1988-0.2014, which straddles the threshold | by hand |

**Deviation: the scorer's own speed verdict is invalid.** The scorer computes speed from the `zs_t1` manifest
and printed "speed good: MET" (pooled 0.2635x, median 0.2044x, max 0.8799x). The manifest's tier-1 timing
(442.7 s in total) comes from the resumed invocation, which **replayed every `zs_t1` tier-1 call from the
gateway cache** (manifest `calls.tier1_new = 0`). EVAL_PLAN 4.4 requires tier-1 to be uncached for timing, so
that verdict is void. The verdicts in the table above use the first-pass tier-1 timing, reconstructed from the
gateway ledger (section 6.1). This is a hand computation, which departs from the plan's "computed by the
scorer". The reconstructed number is the slower, unfavorable one. The run log still contains the scorer's
mechanical verdict, and this report supersedes it.

**What the targets mean.** "Minimum credible" is an engineering milestone, not scientific validation
(EVAL_PLAN 10.3 [A1]). The macro F1 of 0.580027 is just above the top of the planning range of 0.38-0.58 (a planning judgment, not a prediction interval), and above
both the dev-holdout result (0.5026) and the dev result (0.4820). The plan predicted that attack would fall
(holdout .563, test .4492) and that mount would rise (holdout .429, test .6558); both happened.

## 4. Comparison to published references

**Observed gap** (wording per EVAL_PLAN 5): training-free Etho macro F1 0.5800 (attack 0.4492, investigation
0.6349, mount 0.6558) against .793 for a **supervised** model trained on 70 labeled videos, a gap of 0.2130.
Etho does not match or approach that model, and no equivalence claim is made.

**Share of supervised (requested for the demo):** 0.580027 / 0.793 = **0.7314, i.e. 73.1%**. The macro CI,
translated the same way, is [0.4927 / 0.793, 0.6212 / 0.793] = **[0.6213, 0.7834], i.e. 62.1%-78.3%**.
Caveats that must accompany this figure:
- The translation treats .793 as a fixed constant. Its ± .011 is a standard deviation over 5 training runs,
  not a CI, and it is not propagated.
- It is a share of **macro** F1 only. Per-behavior shares can't be computed, because the baseline's per-class
  F1 is unconfirmed.
- The supervision statement in section 1 must sit next to it. Any demo frame showing it must also show the
  three per-behavior F1s and the CIs (EVAL_PLAN 4.1 reporting rule).

**Is the supervised .793 comparable? What is verified, and what isn't:**
- *Source:* Sun et al., "The Multi-Agent Behavior Dataset: Mouse Dyadic Social Interactions",
  arXiv:2104.02710 (NeurIPS 2021 Datasets and Benchmarks), Table 1: the Task 1 baseline (1D ConvNet on raw
  pose), average F1 .793 ± .011, MAP .856.
- *Metric and split:* EVAL_PLAN 4.1 and 5 quote the paper text (v4, Section E). TP is counted in frames;
  the average is "an unweighted average across behavior classes", "omitting the 'other' category"; and the
  metrics are "computed on the entire test set with all the videos concatenated into one sequence". That is
  frame-level F1, pooled over the official Task 1 test set, macro over attack, investigation, and mount,
  which is what our scorer computes on the same 19 sequences (262,107 frames).
- *Who verified it:* the quotes were retrieved by a scout subagent and forwarded by the lead. The Codex
  eval-plan gate (`ops/codex/ask-20261028-082550-84666.md`, gpt-6-astra) states that it "independently
  confirmed the pooling convention and published .793 ± .011 baseline". **The evaluator has not read the PDF
  itself.** Someone should re-read Table 1 before the number goes into the README or the demo.
- *Remaining uncertainties:*
  1. The official scorer code was not retrieved, so our zero-division and tie conventions are our own. No
     pooled denominator here is zero, so those conventions do not affect the pooled test numbers.
  2. Etho predicts at 10 fps and is scored at 30 fps by nearest-sample upsampling. The baseline predicts
     every frame. The 10 fps ceiling, measured on dev, is 0.994 macro.
  3. Per-class baseline F1s are unconfirmed and are not shown.
  4. Whether Table 1 has other Task 1 rows that would be the better reference was not checked.
- *Random .134:* this is **not** a published number. Our scorer computes it on exactly these test frames as the
  expected F1 of an i.i.d. predictor that matches label frequencies (expected F1 = pooled prevalence):
  0.134490 macro (attack 0.048186, investigation 0.233778, mount 0.121507). It therefore uses the same metric
  and frames by construction. It is a legitimate floor, but not the strongest predictor that uses no
  information (EVAL_PLAN 10.3 [A1]).
- *Inter-annotator agreement:* **not retrieved** (MARS, Figure 5 supplements 3-4). Not estimated. Our test labels
  come from a single annotator, so even when retrieved it is context, not a ceiling.

## 5. The few-shot arms and the correction loop

**How the 18 corrections were produced (verified from `eval/simulate_corrections.py`, the corrections file, and
`eval/results/runs.jsonl`). They were simulated, with no human involved.**
- File: `eval/results/corrections/R1_dev_t1a_inv_none_seed20261028_k3+3.json` (sha256 `dc3cb17d...668363`,
  one of the frozen config's hashed assets). Run `corrections-dev-20261028T070154Z-4df3b7`, generated
  2026-10-28 ~07:01 UTC at commit 2f3c448 (dirty tree), before the freeze (7391fca, 08:11) and before any test
  run.
- Generator: `eval/simulate_corrections.py`, a seeded "simulated user". It makes no model calls and imports no
  product code.
  - It compared Etho's **dev-split** predictions from the chosen zero-shot cascade
    (`data/runs/R1/pred_dev_t1a_inv_none`, dev macro 0.4820) with the **CalMS21 expert labels of the 8 dev
    videos**, on the 10 fps grid.
  - An error bout was a predicted bout of behavior b with less than 20% of its samples labeled b (FP), or an
    expert bout of b with less than 20% predicted as b (FN).
  - For each of the 6 strata (3 behaviors x FP/FN), it drew K = 3 bouts uniformly without replacement, with
    `numpy.random.default_rng([20261028, behavior_index, kind_index])`. Bouts longer than 3 s were clipped to
    a 3 s window at a random offset.
- Result: 18 windows. Candidate pools were attack FP 58 / FN 5, investigation FP 33 / FN 61, and mount
  FP 52 / FN 3 (all 3 mount FNs were taken). 11 of the 18 windows are in mouse008, and 6 of the 8 dev
  sequences contribute. Windows are 1-30 samples (0.1-3 s), 693 frames in total. No windows overlap.
- What the simulated user hands the product, per window: the window, the predicted and the expert labels, and
  feature summaries.
- Revision: Etho's product revision step (`muse-spark-1.3-contributor`, effort high, with a self-check;
  7 calls recorded in `configs/detectors/R1_revised_18corrections.json`) rewrote the three detectors from those
  18 windows. The revised file is frozen (sha256 `1146c85e...29409`).
- **Labels used:** the "corrections" are expert dev labels, taken from CalMS21 annotator 1 by script. No
  correction is drawn from dev-holdout or test.
- **Bias note** (EVAL_PLAN section 1): dev-holdout informed the self-check design, so the few-shot holdout
  number (0.5631) is optimistically biased. The test number is not affected by that bias.

**Result** (reported per EVAL_PLAN 7.3, labeled "few-shot (18 simulated corrections from dev labels)"):
- **Full cascade, the configuration Etho runs:** `fs_t1 - zs_t1`, macro **inconclusive: -0.0077 [-0.0464, +0.0331]**.
  - Investigation: increased by +0.0467 [+0.0303, +0.0673].
  - Mount: decreased by -0.0819 [-0.1713, -0.0001]; the upper bound only just excludes 0.
  - Attack: inconclusive, +0.0121 [-0.0327, +0.0537].
- **Rules-only ablation:** `fs_rules - zs_rules`, macro increased by +0.0492 [+0.0178, +0.0806]. The gain is
  driven by investigation (+0.1865, increased); attack (+0.0140) and mount (-0.0529) are inconclusive. One
  possible reading, which was not tested: tier 1 already recovers investigation (+0.3952 in the zero-shot
  arm), so the detectors' investigation gain adds little once tier 1 runs.

**Claim rule for the product.** The test does **not** show that the correction loop improves accuracy in
the pipeline Etho actually runs (rules + tier-1): the macro CI contains 0. No "accuracy gain from corrections"
may be claimed for the product, the README, or the demo. The only supported, narrow statement is: "in a
rules-only ablation, 18 corrections simulated from dev labels raised macro F1 by +0.0492 [+0.0178, +0.0806], driven
by investigation; with tier-1 adjudication the effect is inconclusive (-0.0077 [-0.0464, +0.0331]), with
investigation up and mount down." The loop can still be *shown* as a feature. Its accuracy effect on test is
not demonstrated.

## 6. Speed and cost, primary arm `zs_t1`, all 19 full-length test videos

### 6.1 Speed

> **[2026-10-28, after the gate]** The figures below omit 10 fps preprocessing (chunks were precomputed). The
> corrected figures, including the separately measured preprocessing, are in section 11.3: pooled 0.8777x,
> median 0.8602x, max 1.5421x; the manual time is 3.41x-4.55x Etho's.

**Definition** (EVAL_PLAN 4.4 [A1]): per-video processing time = tracking wall + rules wall + **first-pass**
tier-1 wall, divided by the video's duration (`src_frames / 30`). It is a sum of stage times, not one
file-to-output interval, and it excludes the video download. Every video is included; none failed.

Sources:
- `track_s` and `rules_s`: the `zs_t1` manifest (`wall_clock_s.per_video`). They come from the runner's log
  `data/runs/T1/pipeline_test.json`.
  - The SAM pass was a separate invocation, 08:38:15-09:08:52 UTC by the ledger: 1,836.8 s wall, against
    1,854.0 s summed `track_s`, which also includes frame counting.
  - Its calls were all first-time calls: 308 uncached `eval-sam` calls, $17.4838.
- `tier1_s`: **not** the manifest value (a cache replay), but `data/runs/T1/zs_t1_first_pass_timing.json`. The
  lead reconstructed it from `ops/spend.jsonl`.
  - The evaluator re-checked it against the ledger: 3,241 uncached Fireworks calls with no errors, $9.811516,
    from process start (~13:18:05, approximate) to the last mouse089 call at 14:46:57.198, i.e. 5,332.2 s.
  - Each video's interval runs from the previous video's last call to this video's last call. It therefore
    includes strip rendering, and for the first video also process start-up and the rules stage. Adding
    `rules_s` on top double-counts up to ~0.3 s per video. That is conservative.
- Execution:
  - Videos ran **one after another** in both passes.
  - Within a video, SAM ran 60 s chunks on up to **8 parallel workers** (`track_video` default `workers=8`;
    mouse071 has 14 chunks), and tier-1 used **6 concurrent Fireworks slots** (`FW_CONCURRENCY = 6`).
  - The ledger shows no other model calls during either pass. Other CPU load on the VM (web-app work in
    parallel worktrees) was not measured.
  - Machine: 4 CPUs, 11.7 GB RAM (manifest `machine`).

| Video | Duration (s) | Tracking (s) | Rules (s) | Tier-1 first pass (s) | Windows | Total (s) | x real time |
|---|---|---|---|---|---|---|---|
| mouse071 | 793.7 | 91.2 | 0.32 | 510.8 | 290 | 602.3 | 0.7589 |
| mouse072 | 605.5 | 242.5 | 0.31 | 374.4 | 254 | 617.2 | 1.0195 |
| mouse073 | 638.5 | 526.2 | 0.31 | 426.7 | 287 | 953.2 | 1.4928 |
| mouse074 | 627.5 | 83.6 | 0.31 | 358.8 | 215 | 442.7 | 0.7055 |
| mouse075 | 600.5 | 80.2 | 0.31 | 210.0 | 137 | 290.5 | 0.4838 |
| mouse076 | 657.0 | 85.9 | 0.32 | 357.5 | 201 | 443.7 | 0.6754 |
| mouse077 | 630.2 | 78.5 | 0.32 | 224.2 | 135 | 303.1 | 0.4809 |
| mouse078 | 649.1 | 102.0 | 0.31 | 382.6 | 225 | 485.0 | 0.7472 |
| mouse079 | 582.8 | 72.5 | 0.31 | 378.3 | 233 | 451.1 | 0.7742 |
| mouse080 | 206.0 | 45.4 | 0.30 | 174.9 | 116 | 220.7 | 1.0714 |
| mouse081 | 164.4 | 35.1 | 0.30 | 78.0 | 44 | 113.4 | 0.6898 |
| mouse082 | 237.2 | 42.3 | 0.30 | 170.3 | 103 | 212.8 | 0.8975 |
| mouse083 | 162.7 | 34.2 | 0.30 | 129.2 | 72 | 163.8 | 1.0066 |
| mouse084 | 187.5 | 45.9 | 0.30 | 103.9 | 62 | 150.2 | 0.8007 |
| mouse085 | 640.0 | 89.9 | 0.32 | 423.9 | 262 | 514.1 | 0.8033 |
| mouse086 | 287.2 | 40.0 | 0.31 | 175.5 | 109 | 215.8 | 0.7512 |
| mouse087 | 397.2 | 53.8 | 0.31 | 312.9 | 188 | 367.0 | 0.9240 |
| mouse088 | 294.2 | 52.8 | 0.30 | 280.5 | 146 | 333.6 | 1.1340 |
| mouse089 | 375.8 | 52.1 | 0.30 | 259.8 | 162 | 312.1 | 0.8306 |
| **All 19** | **8,736.9 (145.6 min)** | **1,854.0** | **5.88** | **5,332.2** | **3,241** | **7,192.1** | |

- **Pooled (sum/sum): 0.8232x real time. Median per video: 0.8007x. Max: 1.4928x (mouse073). Min: 0.4809x.**
- **5 of 19 videos ran slower than real time:** mouse072 1.0195x, mouse073 1.4928x, mouse080 1.0714x,
  mouse083 1.0066x, mouse088 1.1340x.
- Tier-1 accounts for most of the time: pooled 0.6104x for tier-1 and 0.2123x for tracking. mouse073's
  tracking took 526.2 s, against 72-102 s for the other long videos, because one chunk took 516.8 s
  (`max_chunk_wall_s` in its `tracking.json`). The cause, provider latency or retries, is not recorded.
- The plan's dev-based expectation was about 0.45x. Test videos are longer and generate more tier-1 windows
  (22.3 per video-minute), so the measured speed is slower than expected.

**Against the cited manual figure.** Segalin et al., "The Mouse Action Recognition System (MARS)", eLife 2021
(https://elifesciences.org/articles/63720), Introduction: "Scoring for social behaviors often takes human
annotators 3–4× the video's duration to annotate." MARS gives no source for that figure. For these
145.6 min of video, it means 436.8-582.4 annotator-minutes (7.28-9.70 h). Etho's summed processing time was
7,192.1 s = 119.9 min (2.00 h), so the cited manual time is 3.64x to 4.85x Etho's machine time (3 / 0.8232
to 4 / 0.8232). **This is a contextual runtime comparison, not demonstrated labor savings at equal quality**
(EVAL_PLAN 4.4 [A1]): human review and correction time was not measured, and Etho's accuracy is below the
supervised model.

**The `fs_t1` timing is not speed evidence.** Its manifest gives 5,248.8 s of tier-1 time, 0.8137x pooled,
0.8114x median, and 1.4488x max. That covers only the resumed pass, and that pass benefited from cache hits:
1,170 of 4,214 windows were shared with `zs_t1` or judged in the crashed attempt before the crash. It mixes the
crashed and resumed passes and is reported only for completeness. The manifests of the rules-only arms
(0.2129x pooled) include no tier-1 time.

### 6.2 Cost

All test videos were first-time calls. Prices: SAM 3.1 at $0.20 per 1k frames; Fireworks Muse Spark Open at
$1.25 per M input, $0.15 per M cached input, and $4.25 per M output tokens. The perception planner was **not
run** on test: the frozen config fixes the SAM phrase "mouse", and the ledger shows no planner calls during
the passes. So every figure below is **"without planner"**.

| Item | $ | Basis |
|---|---|---|
| SAM 3.1, 19 videos (Meta, tag `eval-sam`) | 17.4838 | ledger: 308 uncached `eval-sam` entries, 08:38-09:08 UTC; equals manifest `cost.sam_usd`. The manifest's `uncached_equivalent_usd.sam` (17.4748, computed per video from `n_samples`) is $0.009 lower; the higher, billed figure is used |
| Tier-1, `zs_t1` first pass (Fireworks, tag `eval-llm`) | 9.811516 | ledger: 3,241 calls, as billed (including Fireworks' cached-input discount) |
| Tier-1, `zs_t1`, no cached-input discount | 10.506621 | manifest `uncached_equivalent_usd.tier1`, recomputed from tokens at the full input price |
| **Total for 19 videos, as billed** | **27.2953**, i.e. $1.4366 per video on average | |
| **Per video-minute**, as billed / manifest uncached-equivalent / most conservative (billed SAM + undiscounted tier-1) | **$0.1875 / $0.1922 / $0.1923** | ÷ 145.615 video-minutes |
| **Per hour of video**, same three bases | **$11.25 / $11.53 / $11.54** | x 60 |
| Split per video-minute, as billed | SAM $0.1201, tier-1 $0.0674 | |

- **One-time cost, not per video (listed separately).** Synthesizing the zero-shot detectors took 4 calls to
  `muse-spark-1.3-contributor` (9,148 input and 48,957 output tokens), recorded in
  `configs/detectors/R1_zero_shot.json`.
  - At contributor prices ($0.10 / $0.20 per M) that is **$0.0107** uncached. The run booked $0.0026,
    because the other calls were cache hits.
  - At Standard prices for user uploads (`muse-spark-1.3`, $1.25 / $4.25 per M), the same tokens cost
    **$0.2195**.
  - The few-shot revision took 7 calls, $0.0191 at contributor prices and $0.3746 at Standard prices.
- **"New-user" figure, adding the planner.** The dev-measured planner cost is about $0.05-0.07 per video
  (ADR-001; the evaluator has not re-measured it). Over 19 videos that adds $0.0066-0.0092 per video-minute,
  so the most conservative total becomes $0.1988-0.2014 per video-minute. That straddles the $0.20 "good" cost
  threshold.
- **Actual test-1 spend:**
  - Meta: $17.4838 for SAM. Aniket's approval was <= $20.
  - Fireworks: $19.7951 in total. That is $9.8115 for `zs_t1`, $0.8075 for the crashed partial `fs_t1` attempt
    (which includes the $0.013187 booked for the ambiguous ReadTimeout call), and $9.1760 for the resumed
    `fs_t1` pass, all summed from the ledger. Aniket's approval was <= $25.
  - Scoring and identity cost $0 and made no model calls.
- **Manifest defects to know about** (product runner, `scripts/test_run.py`, reported rather than fixed):
  - The `zs_t1` manifest's `cost.tier1_usd` is 0.0 and `per_video_min_usd` is 0.1201, because the resumed
    pass was a cache replay.
  - `failed_or_maybe_billed_calls` is 0 in every manifest, because the runner counts only errors since its own
    start, so the crashed attempt's ambiguous call is missing.
  - The run log copies these manifest values unchanged.

## 7. Identity check (`identity-test-20261028T164348Z-cdc866`)

Settings: frozen parameters `--centroid all7 --gate-px 60 --margin-px 10 --min-kp-score 0.5 --step 3`, run on
the shared test tracks.

How the input was staged: the identity tool needs `<seq>.npz` files in one directory, so the evaluator copied
`data/runs/T1/<seq>/tracks.npz` byte for byte (checked with `cmp`) to `data/runs/T1/tracks_test/<seq>.npz`. The
tracks hash recorded before keypoints were read is `db7abf17...70df0`.

The reference is the MARS keypoints, which are pose-estimator output, not human ground truth. These are
agreement figures.

| | Identity error, all (errors / matched mouse-samples) | Identity error (unambiguous) | Ambiguous frac | Coverage, both labeled* | Both matched correctly |
|---|---|---|---|---|---|
| Overall | **0.00295** (485 / 164,692) | 0.00239 | 0.00112 | 0.9726 | 0.9662 |
| Attack frames | 0.00227 (16 / 7,072) | 0.00213 | 0.00099 | 0.9793 | 0.9769 |
| Investigation frames | 0.00023 (9 / 39,208) | 0.00023 | 0.00006 | 0.9984 | 0.9895 |
| Mount frames | 0.02840 (450 / 15,846) | 0.02285 | 0.01092 | 0.8054 | 0.7523 |
| Other frames | 0.00010 (10 / 102,566) | 0.00010 | 0.00002 | 0.9960 | 0.9922 |

\* Coverage, unknown rate, and detections are *retained-output diagnostics*. The exported tracks keep only the
resident and intruder labels, so the unknown rate (0.0000) and "3+ detections" (0.0000) are zero by
construction.

- Errors by animal: resident 371 of 84,123, intruder 114 of 80,569.
- Swap events: 375, i.e. 2.58 per video-minute; 347 (2.39 per minute) on unambiguous matches only.
- Detections per grid sample: 0 tracks 1.42%, 1 track 1.32%, 2 tracks 97.26%.
- Median matched centroid distance: resident 26.5 px, intruder 21.9 px.
- Per video (not sealed, because it involves no behavior predictions):
  - Identity error is at most 0.0242 (mouse085); the next highest is mouse086 at 0.0165.
  - Coverage (both labeled) is lowest on mouse073 (0.8279) and mouse072 (0.8746). These are the same two videos
    that had slow tracking.
- Mount is the weak spot: coverage 0.8054 and error 0.0284, which is still below the 0.10 flag.
- Alignment diagnostic (it does not change scoring; lag 0 was frozen):
  - Resident: the best lag is negative on every video, -1 to -4 frames (mostly -2 or -3).
  - Intruder: the best lag is 0 on 4 videos (mouse075-078) and -2 to -5 on the rest.
  - This is consistent with the dev finding (median -2). The margin to the runner-up lag is 0.0-1.3 px.
  - The cause (a label offset or SAM centroid lag) is unresolved. It is a disclosed limitation.

## 8. Run incident and provenance

- **Tier-1 crash and resume.** Tier-1 crashed once, at 15:03 UTC, in the `fs_t1` arm, on a single
  Fireworks `ReadTimeout`.
  - The gateway booked this ambiguous failure at the estimate ($0.013187) and did not retry it, by design.
    The frozen tier-1 code does not catch it.
  - `zs_t1` had already completed: 3,241 windows.
  - The identical command was re-run under the same run id T1 and frozen config. Completed calls replayed from
    the gateway cache at $0, and no labels were read in between.
  - Records: crash log `data/runs/T1/logs/t1_test_attempt1_crash.log` (sha256 `688be86f...e119`); decision in
    `docs/DECISIONS.md` (2026-10-28 15:05 UTC).
  - The evaluator's view: this is still a single execution of the frozen pipeline. `zs_t1` predictions are
    exactly the cached verdicts of the first pass, and `fs_t1` combines cached verdicts with new calls. Because
    of the replay, `zs_t1` timing and cost must come from the first pass (section 6).
- **Freeze checks passed before labels were read** (preflight `0cabb1`, repeated inside the scoring run):
  - all four arms, the primary arm, and all four comparisons;
  - every manifest matches the frozen config sha256 `fae189fd...4abc` and `frozen_at_commit` 4ada51c;
  - `run_commit` 6aadd51 is shared by all arms, and its only dirty path is `ops/spend.jsonl`;
  - `git diff 4ada51c 6aadd51 -- etho/ ':!etho/app'` is empty;
  - the tier-1 prompt hash is `bb53933f...5445481`;
  - the frozen assets are byte-identical;
  - every `src_frames` equals the index's annotation length;
  - `failed_sequences` is `[]`.
- **Provenance gap: the tracking stage.** The manifests record only the last runner invocation (the tier-1
  resume at 6aadd51). The SAM tracking stage ran earlier (08:38-09:09 UTC), and its commit and dirty paths are
  not recorded. By commit timestamps, HEAD on main was 4718187 (committed 08:37:48). That commit's runner
  refuses to start if the `etho/` working tree differs from 4ada51c, and the evaluator verified
  `git diff 4ada51c 4718187 -- etho/ ':!etho/app'` is empty. The evaluator inferred the commit; it was not
  recorded.
- **Timing of the freeze.** The SAM pass (label-free) started at 08:38, before amendment A1 (814d273, 08:49) and
  the scorer's `etho/app` exclusion (a59db83, 08:54) were committed. A1 changed no threshold, frozen config,
  arm, or identity parameter. The targets file's numbers are unchanged since e68f1e7 (08:25).
- **Help-text discrepancy.** The `eval/score_arms.py` help text lists three `--compare` pairs, but the scorer
  requires the fourth, `zs_rules:fs_rules`, as EVAL_PLAN 7.2 does. All four were passed. The evaluator will
  fix the docstring; this does not affect results.

## 9. Exact commands

```
uv run python -m eval.score_arms --split test --purpose test-1 --preflight-only \
  --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
  --arm zs_t1=data/runs/T1/zs_t1 --arm zs_rules=data/runs/T1/zs_rules \
  --arm fs_t1=data/runs/T1/fs_t1 --arm fs_rules=data/runs/T1/fs_rules \
  --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1 --compare zs_rules:fs_rules
# -> preflight ok (score-test-20261028T164327Z-0cabb1)

uv run python -m eval.score_arms --split test --purpose test-1 --confirm-test-run \
  --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
  --arm zs_t1=data/runs/T1/zs_t1 --arm zs_rules=data/runs/T1/zs_rules \
  --arm fs_t1=data/runs/T1/fs_t1 --arm fs_rules=data/runs/T1/fs_rules \
  --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1 --compare zs_rules:fs_rules
# -> score-test-20261028T164333Z-473c64 (sealing is automatic on test; --seal not needed)

mkdir -p data/runs/T1/tracks_test
for d in data/runs/T1/mouse0*_task1_annotator1; do cp -p $d/tracks.npz data/runs/T1/tracks_test/$(basename $d).npz; done
uv run python -m eval.identity --split test --tracks-dir data/runs/T1/tracks_test --purpose test-1 --confirm-test-run \
  --centroid all7 --gate-px 60 --margin-px 10 --min-kp-score 0.5 --step 3
# -> identity-test-20261028T164348Z-cdc866
```

The speed and cost figures in section 6 were computed by hand from `data/runs/T1/zs_t1/manifest.json`,
`data/runs/T1/zs_t1_first_pass_timing.json`, and `ops/spend.jsonl`, using short read-only Python snippets
(sums and ratios). The tier-1 aggregates in section 2.5 come from the verdict files. No model API was called,
and no prediction, config, label, or split was edited.

## 10. What happens next (not decided here)

- **Test-2 decision.** EVAL_PLAN 7.4: test-1 is the headline. A test-2 is exploratory unless a documented
  implementation defect, demonstrable without test labels, is found. The tier-1 crash was handled within test-1
  and produced complete outputs, so the evaluator sees no defect that would qualify for a corrected test-2.
  The lead and Aniket decide. The per-sequence seal is lifted only after that decision is committed.
- **Codex test-results gate**: the input is `eval/results/test1_gate_input.md`.

---

## 11. Corrections after the test-results gate (2026-10-28, ~17:15-17:45 UTC)

This section is dated and was added after the Codex `test-results` gate. The gate output is
`ops/codex/ask-20261028-165452-293961.md` (gpt-6-astra, reasoning xhigh; sha256 `c5026195...20ece6`). Sections
1-10 above are kept exactly as first written, apart from short pointer lines added to sections 1 and 3 and
6.1. **Where this section and sections 1-10 disagree, this section wins.**

What was *not* done: nothing was rescored. No label, keypoint, or test video was opened, the sealed
per-sequence file was not opened, no model API was called, and no prediction, config, label, or split was
edited. The one new measurement (preprocessing time, 11.3) is label-free and was made by the lead.

**The gate's overall view.** "Retain test-1's accuracy result, but require corrections before approving the
public claims."
- Codex re-verified all 76 prediction-file hashes and the four manifests against the scoring record.
- It recomputed every arm's macro F1 from the recorded confusion matrices, and every value matched.
- It found "no issues ... in those F1 calculations or the inspected pooled-metric and paired-bootstrap
  implementation."
- It agrees with the verdicts for accuracy, tier-1 lift, identity, and coverage.
- It agrees that voiding the scorer's cache-replay speed verdict was correct.
- It found the crash and resume defensible: "one scored evaluation, resumed after an inference failure", not one
  uninterrupted process. It adds that neither the crash nor the timing defect justifies a replacement
  test-2 headline or a rerun of accuracy.
- Its two High findings are the speed omission (11.3) and the headline wording (11.2).

### 11.1 Demo numbers (corrected; use these, with the wording as given)

These three statements are approved by the evaluator for the demo, subject to Aniket's headline decision in
11.2. Each must appear with its caveat.

1. **Accuracy relative to the published baseline.**
   > Etho achieved **73.1%** of the published raw-pose 1D ConvNet baseline's macro F1: **0.580 versus 0.793,
   > a 0.213 gap** (CalMS21 Task 1 test set, frame-level F1).

   - Show the per-behavior F1s beside it: attack 0.449 (baseline 0.664), investigation 0.635 (0.814), mount
     0.656 (0.900). The baseline per-class values are from Sun et al. Table 8, verified (11.4).
   - Interval: 62.1%-78.4% (the macro CI divided by 0.793, rounded outward; section 4's 62.1%-78.3% was
     truncated). This treats 0.793 as fixed, so it is not a full uncertainty interval for the
     comparison.
   - 0.793 is *not* the best published result. On the same test set, the paper's Table 2 lists 0.829
     (baseline plus task-programming features) and 0.864 (MABe 2021 Task 1 top-1 entry). Etho's macro is
     67.1% of 0.864.
   - Attach the supervision statement (11.2).
2. **Speed, including preprocessing.**
   > Summed processing time (10 fps chunking, tracking, rules, and adjudication, one video at a time) was
   > **0.88x the video's duration** pooled over the 19 test videos (median 0.86x per video; 5 of 19 videos
   > took longer than real time, max 1.54x). Published estimates put manual scoring of social behavior at
   > 3-4x video duration (Segalin et al., eLife 2021).

   - Caveat (mandatory): *this is a runtime comparison, not demonstrated labor savings.* Human review and
     correction time were not measured, and Etho's accuracy is below supervised models.
   - Preprocessing was measured separately, after the run (11.3). Video download is excluded.
   - The cited manual time is 3.41x-4.55x Etho's summed processing time (3 / 0.8777 to 4 / 0.8777).
3. **Cost.**
   > Model API cost for this fixed benchmark pipeline was **$11.25 per hour of video** ($11.54 without
   > input-cache discounts), excluding the planner, one-time detector synthesis, hosting, and human review.

   - This is from the gateway ledger at logged rates. It is not an invoice.

Supporting measurement, not a test-set result: the live app on a fresh **4-minute dev clip** (mouse021, 60-300
s, re-encoded so nothing was cached).
- End to end, including upload validation and re-encode, the planner, tracking, tier-1, and report, it took
  191.2 s for 240 s of video = **0.80x real time**, costing **$0.7456**, about $11.18 per hour.
- Source: `docs/DECISIONS.md`, 2026-10-28 17:05 UTC, measured by the lead.
- The evaluator checked the cost against the ledger: one job (`j8864b898266d`), 87 calls, $0.745642, between
  17:01:19 and 17:03:59 UTC. The 191.2 s wall time was not independently checked.
- Caveats:
  - The clip is from a **dev** (tuning) video, so it says nothing about accuracy.
  - The app uses seeded detectors (no synthesis call) and is configured differently from the frozen test
    pipeline (for example, app tier-1 concurrency is 8, per commit d3c3844).
  - Per the lead's note, a 60 s clip would stay above 1x, because tier-1 latency dominates.

### 11.2 Headline and footnote: PENDING ANIKET'S DECISION

The lead is escalating this. The evaluator does not adopt either version. Until Aniket decides, public
material should not use the "untouched" footnote.

| | Version A (Aniket's approved wording, used in section 1) | Version B (proposed by the Codex gate) |
|---|---|---|
| Headline | "Training-free: plain-English behavior definitions, no labeled examples, no model training." | "Plain-English behavior definitions, no task-specific model training or fine-tuning, and no labeled examples supplied to models in the primary arm." |
| Footnote | "Pipeline settings were tuned on 13 labeled development videos; test results are on 19 untouched videos." | "Pipeline settings were selected using 13 labeled development videos. Predictions were evaluated once on all 19 official test videos. Before evaluation, test-label summaries informed development-set selection, sampling rate, and targets." |

The gate's reasons (High #2):
- "No labeled examples" can be read as "no labeled data influenced the system", which is false, because
  labeled dev data selected the settings.
- "Untouched" is wrong, because test-label aggregates informed the dev selection, the sampling rate, and the
  targets. Relegating that to long-form material "gives readers the wrong impression".
- "Training-free" is defensible only as "no task-specific weight training or fine-tuning".

**Correction adopted now** (a factual narrowing, not headline wording): in section 1, "no setting was tuned on
test" is replaced by **"No test prediction scores were used to select this configuration."** The evaluator
agrees with the gate that, since test-label aggregates shaped the dev set and the 10 fps choice, the broader
sentence was not supported.

### 11.3 Speed: preprocessing was missing (gate High #1); corrected verdicts

**The defect.** EVAL_PLAN 4.4 [A1] defines `track_s` as including chunking. The test run reused 10 fps / 60 s
chunks that `scripts/fetch_test_videos.py` had cut beforehand (line 18 calls the same `prepare_chunks`, in
parallel across videos). `prepare_chunks` (`etho/tracking/pipeline.py:62`) returns cached chunks when they
exist. So each video's recorded `prep_s` was about 0.0005-0.001 s, a cache lookup. Section 6.1 therefore
omitted a stage that the registered metric includes. The evaluator missed this before the gate.

**The fix.**
- The lead measured preprocessing separately, after the run, with `scripts/time_prep.py` (commit efa9dd1; sha256
  `814ca5c8...3b08`). The script runs the same `prepare_chunks(src, tmp)` call (stride 3, 10 fps, 60 s chunks)
  **uncached** (a fresh temporary directory per video), **one video at a time**, on this VM.
- Output: `data/runs/T1/prep_timing.json`, copied byte for byte to **`eval/results/test1_prep_timing.json`** (sha256
  `71635ea7...54a86`).
- Total 475.6 s for 19 videos (9.9-40.1 s each), producing the same 87,374 samples. The per-video counts equal
  the manifests' `n_samples` (checked).
- Disclosures:
  - The measurement is **not contemporaneous** with test-1 (the file was written 17:13:44 UTC; the run started
    ~17:05:48).
  - The lead reports the machine was otherwise idle. The evaluator can only confirm that **no gateway call
    overlapped it**: the last ledger entry before it is the live-app job ending at 17:03:59, and the next is
    after 17:13:44. Other CPU load was not measured.
  - It does not reproduce the original chunking conditions: the original ran in parallel with downloads.
- The gate's alternative: from file timestamps during that parallel download-and-chunk job, Codex estimated about
  1,790 s of preprocessing. That would put pooled speed at about 1.03x, which would fail the minimum pooled
  target. The lead attributes the gap to the parallel download-and-chunk job, and the evaluator considers the
  sequential uncached stopwatch measurement the better estimate of the registered stage.
- **The minimum-credible speed verdict below therefore depends on accepting a non-contemporaneous
  measurement.** It should be described that way.

Per-video processing time = **prep (measured later)** + tracking + rules + first-pass tier-1, over the
video's duration. Ratios are rounded up.

| Video | Duration (s) | Prep (s, measured later) | Tracking (s) | Rules (s) | Tier-1 first pass (s) | Total (s) | x real time |
|---|---|---|---|---|---|---|---|
| mouse071 | 793.7 | 40.1 | 91.2 | 0.32 | 510.8 | 642.4 | 0.8095 |
| mouse072 | 605.5 | 29.5 | 242.5 | 0.31 | 374.4 | 646.7 | 1.0682 |
| mouse073 | 638.5 | 31.5 | 526.2 | 0.31 | 426.7 | 984.6 | 1.5421 |
| mouse074 | 627.5 | 31.1 | 83.6 | 0.31 | 358.8 | 473.8 | 0.7551 |
| mouse075 | 600.5 | 29.8 | 80.2 | 0.31 | 210.0 | 320.3 | 0.5334 |
| mouse076 | 657.0 | 34.2 | 85.9 | 0.32 | 357.5 | 477.9 | 0.7275 |
| mouse077 | 630.2 | 32.2 | 78.5 | 0.32 | 224.2 | 335.2 | 0.5320 |
| mouse078 | 649.1 | 35.4 | 102.0 | 0.31 | 382.6 | 520.4 | 0.8017 |
| mouse079 | 582.8 | 30.0 | 72.5 | 0.31 | 378.3 | 481.2 | 0.8257 |
| mouse080 | 206.0 | 11.1 | 45.4 | 0.30 | 174.9 | 231.7 | 1.1252 |
| mouse081 | 164.4 | 9.9 | 35.1 | 0.30 | 78.0 | 123.3 | 0.7502 |
| mouse082 | 237.2 | 15.0 | 42.3 | 0.30 | 170.3 | 227.9 | 0.9609 |
| mouse083 | 162.7 | 10.0 | 34.2 | 0.30 | 129.2 | 173.8 | 1.0681 |
| mouse084 | 187.5 | 11.2 | 45.9 | 0.30 | 103.9 | 161.3 | 0.8602 |
| mouse085 | 640.0 | 37.9 | 89.9 | 0.32 | 423.9 | 552.0 | 0.8625 |
| mouse086 | 287.2 | 17.1 | 40.0 | 0.31 | 175.5 | 232.9 | 0.8109 |
| mouse087 | 397.2 | 25.4 | 53.8 | 0.31 | 312.9 | 392.4 | 0.9879 |
| mouse088 | 294.2 | 19.4 | 52.8 | 0.30 | 280.5 | 353.0 | 1.2001 |
| mouse089 | 375.8 | 24.8 | 52.1 | 0.30 | 259.8 | 336.9 | 0.8966 |
| **All 19** | **8736.9** | **475.6** | **1854.0** | **5.88** | **5332.2** | **7667.7** | pooled **0.8777** |

**Pooled 0.8777x, median 0.8602x, max 1.5421x (mouse073); 5 of 19 videos above 1x** (mouse072 1.0682x,
mouse073 1.5421x, mouse080 1.1252x, mouse083 1.0681x, mouse088 1.2001x). Preprocessing adds 0.0545x pooled.

**Corrected speed verdicts**, mechanically from `eval/targets_v1.json` `speed_x_realtime` at full precision.
There is no CI, so no "robustly":
- **Minimum credible** (pooled <= 1.0 and median <= 1.0): pooled 0.877626 <= 1.0 and median 0.860181 <= 1.0,
  so **MET**. This is conditional on the separately measured preprocessing (see above).
- **Good** (max <= 1.0 and pooled <= 0.5): max 1.542032 > 1.0 and pooled 0.877626 > 0.5, so **NOT MET**.

These replace the speed rows of section 3 and the ratios in sections 6.1 and 10. Against the cited manual
figure, Etho's 7,667.7 s (127.8 min) for 145.6 min of video means the manual time is 3.41x-4.55x Etho's summed
processing time. The earlier 3.64x-4.85x is withdrawn. The section 6.1 caveat stands: this is a contextual
runtime comparison, not labor savings. The gate's interim wording, recorded for completeness, was: "With
video chunks precomputed, summed tracking, rules, and adjudication time was approximately 0.82 times video
duration. Preprocessing time is excluded; the registered complete-processing speed target has not been
established." The preprocessing measurement above supersedes it.

### 11.4 References: baseline, random floor, cost wording (gate Medium #4 and #5)

**The supervised baseline, now verified by the evaluator.** On 2026-10-28 the evaluator downloaded
https://arxiv.org/html/2104.02710v4 (local copy in the evaluator's scratchpad, sha256 `23545b53...d14efd`) and read it.
- **Table 1** ("Class-averaged results on Task 1 (attack, investigation, mount) ... mean and standard deviation
  over 5 runs"): 1D Conv Net, Average F1 .793 ± .011, MAP .856 ± .010.
- Section 4.2: "using raw pose trajectories".
- **Table 8** ("Per-class results on Task 1"), Baseline: **attack F1 .664 ± .031, investigation .814 ± .005,
  mount .900 ± .004**. Their mean, 0.7927, is consistent with .793.
- Table 2: Baseline .793; "Baseline w/ task prog" .829 ± .004, which also uses the unlabeled set; "MABe 2021
  Task 1 Top-1" .864 ± .011.
- Appendix E: "an unweighted average across behavior classes ... omitting the 'other' category", and "for
  Task 1, the metrics should be computed on the entire test set with all the videos concatenated into one
  sequence (each frame is weighted equally for each behavior)".
- Section 3.1: "There are 70 videos in the public training set".
- This resolves the "evaluator has not read the PDF" and "per-class baseline unconfirmed" items of section 4.
  **Per-behavior comparison:** Etho attack 0.4493 vs .664, investigation 0.6350 vs .814, mount 0.6558 vs .900.
  The per-behavior shares are 67.7%, 78.0%, and 72.8%. Each baseline value carries its ± std over 5 runs.
- The gate's caution, adopted: 0.793 must not stand for "supervised performance" in general or for the state of
  the art. Etho also differs in inputs (video vs pose) and in development procedure, so the gap cannot be
  attributed to training alone.

**Random reference, relabeled.**
- Use the label "**Frequency-matched random reference: approximately 0.1345 macro F1, calculated from test-set
  label prevalences.**"
- It is not a universal chance floor. With the same prevalences, the evaluator recomputed the gate's
  alternatives: uniform guessing over the 4 classes gives macro F1 **0.16198** (attack 0.0808, investigation
  0.2416, mount 0.1635), and uniform guessing over the 3 scored behaviors gives **0.17904**. (Expected F1 for
  a class with prevalence p and guess rate q is 2pq / (p + q).)
- Do not write "N times chance".

**Cost wording.** Use the 11.1 item 3 sentence: model API cost at logged rates, excluding the planner,
one-time detector synthesis, hosting, and human review. The planner-inclusive "new-user" figure in section 6.2
is an **extrapolation**, not a measured new-user run. The ledger is not independent invoice verification. The
arithmetic was confirmed by the gate: $27.295316 for 145.615 video-minutes, $11.246911 per hour, or $11.533326
without the cache discount.

### 11.5 Smaller corrections (gate Low #6)

- **Changed windows.** Section 2.5 says 2,117 changed labels in `fs_t1`. That is **wrong. The correct count is
  1,829.** The 288 difference is verdicts of `none` on windows whose rule label was `other`. These map to the
  same label (`etho/adjudication/tier1.py` `apply`, `none` to `other`), and the first count wrongly treated
  them as changes. By type: attack to investigation 521, attack to other 382, other to investigation 352,
  investigation to other 277, mount to investigation 271, mount to other 26. Applied but unchanged: 982.
  Fall-backs: 1,403, which is unaffected. The `zs_t1` count of 1,455 was computed correctly.
- **Non-exclusive ablation.** Section 2.4's sentence is replaced by: **"Pre-priority behavior masks were not
  retained; a true nonexclusive ablation was not computed."** The runner applies the mount > attack >
  investigation priority before it writes the per-sample labels (`scripts/test_run.py` `labels_from`). The
  scorer's one-vs-rest numbers on those exclusive files are therefore identical to the exclusive F1 by
  construction, and they say nothing about the priority rule.
- **Rounding.** The rounding note at the top of this report ("truncate every bound downward") is withdrawn. Truncating both CI ends
  downward is not uniformly conservative, and it made the borderline mount upper bound (-0.000067) read as
  -0.0001. Below, points use conventional rounding and **CIs are rounded outward**, with 6 decimals where a
  bound is within 0.001 of zero. Full precision is in the result JSON.

| Arm | Macro F1 [95% CI] | Attack F1 [CI] | Investigation F1 [CI] | Mount F1 [CI] |
|---|---|---|---|---|
| `zs_t1` | 0.5800 [0.4927, 0.6213] | 0.4493 [0.1729, 0.5596] | 0.6350 [0.5439, 0.6994] | 0.6558 [0.5044, 0.7439] |
| `zs_rules` | 0.3748 [0.2967, 0.4242] | 0.3240 [0.0977, 0.4634] | 0.2398 [0.1822, 0.2813] | 0.5606 [0.3701, 0.6851] |
| `fs_t1` | 0.5724 [0.4750, 0.6222] | 0.4614 [0.1684, 0.5884] | 0.6817 [0.6003, 0.7400] | 0.5740 [0.4064, 0.6824] |
| `fs_rules` | 0.4240 [0.3351, 0.4822] | 0.3380 [0.0984, 0.4844] | 0.4263 [0.3737, 0.4649] | 0.5077 [0.3272, 0.6352] |

| Paired difference | Macro [95% CI] | Attack [CI] | Investigation [CI] | Mount [CI] |
|---|---|---|---|---|
| Tier-1 lift, zero-shot: `zs_t1 - zs_rules` | +0.2053 [+0.1611, +0.2353] increased | +0.1253 [+0.0657, +0.1552] increased | +0.3952 [+0.3411, +0.4381] increased | +0.0952 [+0.0275, +0.1627] increased |
| Few-shot effect, full cascade: `fs_t1 - zs_t1` | -0.0077 [-0.0464, +0.0332] inconclusive | +0.0121 [-0.0327, +0.0538] inconclusive | +0.0467 [+0.0303, +0.0674] increased | -0.0819 [-0.1713, -0.000067] decreased (nominal) |
| Tier-1 lift, few-shot: `fs_t1 - fs_rules` | +0.1483 [+0.1101, +0.1773] increased | +0.1234 [+0.0652, +0.1604] increased | +0.2554 [+0.2000, +0.2998] increased | +0.0662 [+0.0222, +0.1206] increased |
| Few-shot effect, rules only: `fs_rules - zs_rules` | +0.0492 [+0.0178, +0.0807] increased | +0.0140 [-0.0093, +0.0340] inconclusive | +0.1866 [+0.1601, +0.2131] increased | -0.0529 [-0.1301, +0.0231] inconclusive |

- **The few-shot mount decrease is nominal and borderline.** `fs_t1 - zs_t1` mount -0.0819 [-0.1713,
  -0.000067] excludes 0 by 0.000067, and 2.48% of resamples are positive. It is one of 16 paired intervals
  reported (4 comparisons x macro + 3 behaviors), with no multiplicity adjustment. It should be read as a
  nominal, borderline result, not a robust finding. The same applies to every per-behavior paired interval in
  2.2.

### 11.6 Provenance shortcomings (gate Medium #3; already in section 8, restated)

The gate classes these as "provenance/reporting shortcomings, not evidence of favorable result selection".
1. **SAM began before the gate reconciliation was committed.**
   - The frozen config (7391fca, 08:11) and the numeric targets (e68f1e7, 08:25) preceded SAM inference
     (08:38:15).
   - Amendment A1 (814d273, 08:49) and the scorer's `etho/app` exclusion (a59db83, 08:54) came after it started.
2. **The tracking stage's commit and dirty state were not recorded.** The runner overwrote them with those of
   the final invocation. 4718187 is *inferred* from commit timestamps.
3. **The tier-1 failure has no structured failed-attempt record.** It is preserved only in the traceback
   (`data/runs/T1/logs/t1_test_attempt1_crash.log`), the DECISIONS.md entry, and the spend ledger (one entry
   with `error`, $0.013187). The manifests' `failed_or_maybe_billed_calls` is 0, and
   `pipeline_test.json` `failed_calls` is empty.

The gate's other checks:
- All 3,241 successful primary-arm request keys appear in the cache replay.
- No request key had more than one successful uncached response during the test window.
- The timed-out request was requested again on restart.

Items the gate lists as unverified, and still unverified:
- foundation-model exposure to CalMS21;
- independence of the 19 sequences;
- that nobody accessed unlogged or sealed outputs. The evaluator can only state that it did not.

### 11.7 Other changes made with this correction

- **The `eval/score_arms.py` help text** now lists all four `--compare` pairs, including
  `--compare zs_rules:fs_rules`. Docstring only; no behavior changed. `uv run pytest -q eval/tests` passes.
  The sha256 of `eval/score_arms.py` recorded in the gate input refers to the version *before* this edit.
- `eval/results/test1_gate_input.md` is left as it was reviewed.

### 11.8 Corrected verdict summary (primary arm `zs_t1`, test-1)

| Target | Verdict |
|---|---|
| F1 level "minimum credible" | MET (not robustly: attack CI lower bound 0.1729 < 0.25) |
| F1 level "good" | MET (not robustly) |
| Tier-1 lift, minimum and good | MET ROBUSTLY (+0.2053 [+0.1611, +0.2353]) |
| Identity error, minimum and good | MET (0.0029449; no CI) |
| Two-animal coverage, minimum / good | MET / **NOT MET** (0.972612) |
| **Speed, minimum credible** | **MET**, *conditional on the separately measured preprocessing*: pooled 0.8777x, median 0.8602x |
| **Speed, good** | **NOT MET**: max 1.5421x, pooled 0.8777x |
| Cost per video-minute, minimum / good | MET / MET ($0.1923 most conservative; no CI; without planner). With the planner extrapolation it would be $0.1988-0.2014, straddling the $0.20 threshold |
