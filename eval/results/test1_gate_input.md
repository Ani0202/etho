# Codex gate `test-results`: input for a blind review of Etho test run 1

**For the reviewer.** You are reviewing the results of the first and only scoring of the CalMS21 test split
(test-1), for the project Etho. Your job is to check that every number and claim in the report (Part D) is
supported by the logged runs and the pre-registered protocol. In particular, check that nothing is rounded,
framed, or selected in the project's favor. Do not re-run any scorer: the test guard allows one `score` and one
`identity` execution per test run, and both are used. Every file named here is on this VM, read-only for you.
`data/` is git-ignored, so its files are named with sha256 hashes.

Please answer in particular:
1. Do the verdicts in report section 3 follow `eval/targets_v1.json` and EVAL_PLAN 10.3 [A1]? Check
   MET / MET ROBUSTLY direction, level rules, and full precision.
2. Is it right to void the scorer's mechanical speed verdict (it was computed from a cache-replay tier-1
   timing) and substitute a ledger-reconstructed first-pass timing? Is that reconstruction sound and
   conservative? See report 6.1, `data/runs/T1/zs_t1_first_pass_timing.json`, and `ops/spend.jsonl`.
3. Is the tier-1 crash and resume (report section 8) compatible with "one execution" under EVAL_PLAN 7.2?
   Does anything about it qualify as a defect under 7.4?
4. Are the comparisons to the supervised .793 and the random .134, the "share of supervised" figure, and the
   supervision wording (report sections 1 and 4) supported and correctly caveated?
5. Is the few-shot / correction-loop wording (report section 5) consistent with the paired CIs and with
   EVAL_PLAN 7.3?
6. Is the cost per video-minute and per hour (report 6.2) correctly derived, including what is excluded
   (planner, one-time synthesis)?
7. Is anything that the protocol requires missing from the report? Is any claim stronger than its evidence?

## Part A. Pre-registered protocol and targets (committed before test-1)

- Plan: `docs/EVAL_PLAN.md`, v1-final (e68f1e7, 2026-10-28 08:25 UTC) + amendment A1 (814d273, 08:49) + a59db83
  (08:54; scorer excludes `etho/app` from the freeze). sha256 at scoring time
  `b71d04c446612896904d713860c3c74cbc0ef33f6a1d522130b7a8059ba56702`. Relevant sections:
  - 4.1 metric and bootstrap;
  - 4.4 speed and cost definitions;
  - 5 comparison rules;
  - 6a correction protocol;
  - 7 test protocol;
  - 10 targets.
- Targets file `eval/targets_v1.json`, sha256 `ec6b6a6dda046f5b0480c6e4ec04aab6aa89cc9b99bfd3d4a9d38ff6aeccf4b3`.
  Numbers unchanged since e68f1e7; A1 changed only the wording and the direction-aware rule. Full content:

```json
{
 "version": "v1-final + amendment A1 (2026-10-28)",
 "source": "docs/EVAL_PLAN.md section 10 (this file is the machine-readable copy; the plan is authoritative)",
 "applies_to": "CalMS21 official test split (all 19 sequences), arm zs_t1 = frozen pipeline arm (configs/frozen_test_v1.json zero_shot_arm), test-1 (the headline run)",
 "rule": "Higher-is-better: MET if the point estimate is >= the threshold; MET ROBUSTLY if the 95% sequence-bootstrap CI lower bound is also >= it. Lower-is-better: MET if the point is <= the threshold; MET ROBUSTLY if the CI upper bound is also <= it. A metric without a CI can be MET, never MET ROBUSTLY. Anything else is NOT MET. No rounding: compared at full precision.",
 "level_rule": "An accuracy level (minimum_credible or good) is reached only if macro F1 AND each of the three per-behavior F1 thresholds are MET (robustly only if all four are MET ROBUSTLY). Every other target (tier-1 lift, speed, cost, identity, coverage) is a separate verdict; there is no single overall 'success'. 'Minimum credible' is an engineering milestone, not a scientific validation threshold.",
 "primary_arm": "zs_t1",
 "f1": {
  "minimum_credible": {"macro": 0.40, "attack": 0.25, "investigation": 0.40, "mount": 0.40},
  "good": {"macro": 0.50, "attack": 0.40, "investigation": 0.50, "mount": 0.55}
 },
 "tier1_lift_macro": {
  "baseline_arm": "zs_rules",
  "arm": "zs_t1",
  "direction": "higher",
  "minimum_credible": 0.03,
  "good": 0.10,
  "statistic": "paired difference of pooled macro F1 (arm minus baseline_arm); CI from the same sequence resamples"
 },
 "speed_x_realtime": {
  "arm": "zs_t1",
  "direction": "lower",
  "statistic": "per video: summed wall-clock of the stages run on that video (track incl. chunking and SAM, rules, tier-1) / video duration (src_frames / 30); all 19 videos, failures included. pooled = sum of processing / sum of durations; median and max over the 19 per-video ratios. Computed by eval.score_arms from the zs_t1 manifest.",
  "minimum_credible": {"pooled": 1.0, "median": 1.0},
  "good": {"max": 1.0, "pooled": 0.5}
 },
 "checked_by_hand": {
  "note": "not evaluated by eval.score_arms; verdicts use the same rule above, with the arithmetic shown in the report",
  "identity_error_rate_overall": {"direction": "lower", "minimum_credible": 0.05, "good": 0.01, "source": "eval.identity test-1 run, all matches"},
  "two_animal_coverage_both_labeled": {"direction": "higher", "minimum_credible": 0.95, "good": 0.98, "source": "eval.identity test-1 run (retained-output diagnostic)"},
  "cost_per_video_min_usd_uncached": {"direction": "lower", "minimum_credible": 0.25, "good": 0.20, "source": "pipeline manifests and ops/spend.jsonl (EVAL_PLAN 4.4)"}
 },
 "not_a_target": "the supervised .793 (Sun et al. 2021, Table 1) is a reference, not a target"
}
```

- Split file `eval/splits/calms21_v1.json`, sha256 `59bfcd47f5801757efe73cbe7276d1990b15fa2f945cf96e5bf4f001ede2880b`
  (frozen; it holds 8 dev, 5 dev_holdout, and 19 test sequences).

## Part B. Frozen configuration and hashes

`configs/frozen_test_v1.json` (committed 7391fca, 08:11 UTC), sha256
`fae189fd0bb581a5827302ed43b642746a22ae173911524b06d50ce7e54d4abc`. `frozen_at_commit` 4ada51c. Full content:

```json
{
 "name": "etho-calms21-test-v1",
 "frozen_at_commit": "4ada51ca8f3a95b6b51f7ce8a9ead478e5625b8d",
 "zero_shot_arm": {
  "tracking": {
   "sam_model": "sam-3.1",
   "phrase": "mouse",
   "sample_fps": 10,
   "src_stride": 3,
   "chunk_s": 60,
   "mask_encoding": "one_bit",
   "identity": "etho/tracking/identity.py (ADR-001 rules)"
  },
  "definitions": {
   "path": "configs/calms21_definitions.json",
   "sha256": "ec48048e19168d76ff48006b36f05488a1978b17788e7b4127481e71abc39f4c"
  },
  "detectors": {
   "path": "configs/detectors/R1_zero_shot.json",
   "sha256": "0b396e11e288b8a9d9714f1787db229c06c26a5920911762267150bf8f349252",
   "model": "muse-spark-1.3-contributor",
   "effort": "high",
   "feature_stats_from": "dev (label-free)"
  },
  "overlap_priority": [
   "mount",
   "attack",
   "investigation"
  ],
  "tier1": {
   "model": "accounts/fireworks/models/muse-spark-open",
   "effort": "low",
   "frames_per_strip": 6,
   "window_s": 3.0,
   "confident": 0.6,
   "allow": [
    "investigation",
    "none"
   ],
   "prompt_sha256": "bb53933f626c470fb6dc6472b802034cc9669a75f19322cd2f8ecd6153445481",
   "features_in_prompt": false
  },
  "tier2": "not used"
 },
 "few_shot_arm": {
  "description": "same as zero-shot arm, but detectors revised from 18 simulated dev corrections (correction loop with self-check)",
  "detectors": {
   "path": "configs/detectors/R1_revised_18corrections.json",
   "sha256": "1146c85ebdd3c7bcb2aa1bc7a395ff89de0384666cc2460b156cc13f8bd29409"
  },
  "corrections": {
   "path": "eval/results/corrections/R1_dev_t1a_inv_none_seed20261028_k3+3.json",
   "sha256": "dc3cb17d4ac13282bac9e1d399b549bc0b2c0984343fd045b7ded17754668363"
  }
 },
 "dev_results_for_reference": {
  "zero_shot": {
   "dev": 0.482,
   "dev_holdout": 0.5026
  },
  "few_shot_18": {
   "dev_holdout": 0.563
  }
 }
}```

Frozen assets, re-hashed by the scorer before labels were read (`frozen_config.files_checked` in the `started`
record; `mismatches: []`):

| File | sha256 |
|---|---|
| configs/calms21_definitions.json | ec48048e19168d76ff48006b36f05488a1978b17788e7b4127481e71abc39f4c |
| configs/detectors/R1_zero_shot.json | 0b396e11e288b8a9d9714f1787db229c06c26a5920911762267150bf8f349252 |
| configs/detectors/R1_revised_18corrections.json | 1146c85ebdd3c7bcb2aa1bc7a395ff89de0384666cc2460b156cc13f8bd29409 |
| eval/results/corrections/R1_dev_t1a_inv_none_seed20261028_k3+3.json | dc3cb17d4ac13282bac9e1d399b549bc0b2c0984343fd045b7ded17754668363 |
| tier-1 prompt (`etho/adjudication/tier1.py` PROMPT, read via ast at run_commit) | bb53933f626c470fb6dc6472b802034cc9669a75f19322cd2f8ecd6153445481 |

Execution provenance:
- Product run commit: `run_commit` 6aadd5175d91a0c0bca977206c38995a0b24b822, the same in all four manifests.
  `run_git_dirty_paths` = [`ops/spend.jsonl`].
- `git diff 4ada51c 6aadd51 -- etho/ ':!etho/app'` is empty; so is `git diff 4ada51c 4718187` on the same paths
  (4718187 is the inferred commit of the SAM pass, report section 8).
- The runner script `scripts/test_run.py` at 6aadd51 has sha256
  `3a68473a33d585bb6a379e6bfb6bd3ecb58d0e0be2a06c463b69d64bc898dfe3`. It is unchanged in the working tree,
  and it is not frozen.
- Scoring was done at commit 4fa2dc5b8e7acbca37d22e8aa319a67b864306ab. The only dirty paths were
  `ops/spend.jsonl` and `eval/results/runs.jsonl`.
- Eval code at scoring time:

| File | sha256 |
|---|---|
| eval/score_arms.py | 75f661b2ac7a182a9b54e7d271e066f5d35b59e96696a93feaff96c5c5b5c39a |
| eval/score.py | d0a74f686694ce2c1db52f8396343c08d3f601e0e4b1a260f225c8cd73837898 |
| eval/identity.py | 215fb43b2ea36356f9a8e706548c08224f3086dd48414fb222b97f57b322b3ce |
| eval/common.py | aa0af2df91896bd18bf618cf348ee9346846b72faa1573829bf2366c47188d6c |

Run artifacts (`data/` is not committed; verify on this VM):

| File | sha256 |
|---|---|
| data/runs/T1/zs_t1/manifest.json | 7eff1dc0de781fa438601ca2d866886b60d339b1f0f1d3bc0a0476212601df68 |
| data/runs/T1/zs_rules/manifest.json | 7965213b720cf81372590d27d82b3585aa374bea98b64361df21ce2b407418ea |
| data/runs/T1/fs_t1/manifest.json | 4806a9fa6e0613594a34bedfe2387d83ae54b4f2ffe69351098b3670f1edcd4d |
| data/runs/T1/fs_rules/manifest.json | e35bbd120bb52de71bdf533c209d84aa05025a262035c53c5e8a9e2a0eaedcba |
| data/runs/T1/zs_t1_first_pass_timing.json | 550c03b9ad62b36e00d6b6dc527fdcf9f572e1ae075fc8580469fc6111e32a60 |
| data/runs/T1/pipeline_test.json | 6cfe8aa0fdc230d5a3b3829cb919d2e309f8831e08f7254a9a1504e3d51929db |
| data/runs/T1/verdicts_test_zs_t1.jsonl | 2b60a4226013fe73651035074193d57c6475642baf42980c73a5f9597dc28f90 |
| data/runs/T1/verdicts_test_fs_t1.jsonl | 1b6feb6aba97c9e61b1b5c8c5e96a4054f4939857fc359df88ee1cbf4fb11639 |
| data/runs/T1/logs/t1_test_attempt1_crash.log | 688be86f6deb1da7237664aa97e9755cb7e4173170332d81b4027ff19955e119 |
| data/runs/T1/logs/t1_test.log | 494ae907fe8b42f59f13c393ca988fe0cbf4ae2a0364fb83a372cd0ef2249f03 |

Hashes of the prediction payloads (per-sequence files only), as logged in the `started` record of
`score-test-20261028T164333Z-473c64`:
- `zs_t1` 576500a2c374ef5c04fb64ae693fe9e0236f5f7f3778fd10264693511aa7fc9b
- `zs_rules` ae50a628a65da3d3199790cd0b00e38b0741c1640d07281bc323233646c6c78f
- `fs_t1` e2fd985ef71a4e27d3186f31f0c95268ef186eb40442497507eae5ff43e2f9c8
- `fs_rules` f551e8b2c7ddd379ad41d6a055c44c958222b5833095787ee5b0dbd83dd5ac90
- combined payload 15ab77ed8dc5e530e5fa8eee1ce06eb6f0113927946392fe5b2969b4d63f497a

Identity tracks: `tracks_sha256` db7abf17693012d7e5ca874b807d3e0519549b581a5a0c4dc12cbc1ef9d70df0. The input
directory `data/runs/T1/tracks_test/` holds byte copies of `data/runs/T1/<seq>/tracks.npz`.

Results (committed):

| File | sha256 |
|---|---|
| eval/results/score-test-20261028T164333Z-473c64.json | 334af3053d91aa38bd53632500da4ecffa0150fab7e5c8cc38b732bb1e67368f |
| eval/results/identity-test-20261028T164348Z-cdc866.json | b3bee4b3e849e34f2f21936e8699ef93847a3ce20989a4a79102e7bcf588bf75 |
| eval/results/score-test-20261028T164333Z-473c64.per_sequence.sealed.json (**sealed: do not open**, EVAL_PLAN 7.2) | 88f361fe033bd0c642a5d57192264708b113ff56c375b2d4534a0987b0dc0451 |

`eval/results/runs.jsonl` holds five test records: preflight `0cabb1` ok; score `473c64` started, then ok;
identity `cdc866` started, then ok.

## Part C. Exact commands run by the evaluator (2026-10-28, ~16:43 UTC)

```
uv run python -m eval.score_arms --split test --purpose test-1 --preflight-only \
  --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
  --arm zs_t1=data/runs/T1/zs_t1 --arm zs_rules=data/runs/T1/zs_rules \
  --arm fs_t1=data/runs/T1/fs_t1 --arm fs_rules=data/runs/T1/fs_rules \
  --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1 --compare zs_rules:fs_rules
# preflight ok (score-test-20261028T164327Z-0cabb1): 19 sequences x 4 arms; failed_sequences []; accepted []; speed computed: True (ok)

uv run python -m eval.score_arms --split test --purpose test-1 --confirm-test-run \
  --frozen-config configs/frozen_test_v1.json --targets eval/targets_v1.json --primary zs_t1 \
  --arm zs_t1=data/runs/T1/zs_t1 --arm zs_rules=data/runs/T1/zs_rules \
  --arm fs_t1=data/runs/T1/fs_t1 --arm fs_rules=data/runs/T1/fs_rules \
  --compare zs_rules:zs_t1 --compare zs_t1:fs_t1 --compare fs_rules:fs_t1 --compare zs_rules:fs_rules
# exit 0, no warnings; run score-test-20261028T164333Z-473c64

mkdir -p data/runs/T1/tracks_test
for d in data/runs/T1/mouse0*_task1_annotator1; do s=$(basename $d); cp -p $d/tracks.npz data/runs/T1/tracks_test/$s.npz; cmp $d/tracks.npz data/runs/T1/tracks_test/$s.npz; done
uv run python -m eval.identity --split test --tracks-dir data/runs/T1/tracks_test --purpose test-1 --confirm-test-run \
  --centroid all7 --gate-px 60 --margin-px 10 --min-kp-score 0.5 --step 3
# exit 0; run identity-test-20261028T164348Z-cdc866
```

Scorer console output for the scoring run, verbatim (per-sequence lines are suppressed by the seal):

```
run score-test-20261028T164333Z-473c64 split=test purpose=test-1 primary=zs_t1

=== arm zs_t1 (data/runs/T1/zs_t1)

19 sequences, 262107 frames @30fps; frame-level, 30 fps, pooled over all frames of all sequences (CalMS21 Sec. E); macro = unweighted mean of attack/investigation/mount F1

behavior             P       R      F1   F1 95% CI (seq bootstrap)    GT fr  pred fr
attack           0.329   0.707   0.449   [0.173, 0.560]               12630    27119
investigation    0.697   0.583   0.635   [0.544, 0.699]               61275    51197
mount            0.696   0.620   0.656   [0.504, 0.744]               31848    28369
macro F1                         0.580   [0.493, 0.621]            

bout-level (IoU match): IoU>=0.3: attack R=0.349 P=0.196, investigation R=0.359 P=0.499, mount R=0.549 P=0.357; IoU>=0.5: attack R=0.155 P=0.087, investigation R=0.242 P=0.336, mount R=0.390 P=0.253

per sequence F1 (attack / investigation / mount; '-' = behavior absent in GT):

=== arm zs_rules (data/runs/T1/zs_rules)

19 sequences, 262107 frames @30fps; frame-level, 30 fps, pooled over all frames of all sequences (CalMS21 Sec. E); macro = unweighted mean of attack/investigation/mount F1

behavior             P       R      F1   F1 95% CI (seq bootstrap)    GT fr  pred fr
attack           0.206   0.759   0.324   [0.098, 0.463]               12630    46566
investigation    0.788   0.141   0.240   [0.182, 0.281]               61275    10988
mount            0.471   0.693   0.561   [0.370, 0.685]               31848    46841
macro F1                         0.375   [0.297, 0.424]            

bout-level (IoU match): IoU>=0.3: attack R=0.372 P=0.091, investigation R=0.085 P=0.209, mount R=0.605 P=0.202; IoU>=0.5: attack R=0.159 P=0.039, investigation R=0.042 P=0.103, mount R=0.451 P=0.150

per sequence F1 (attack / investigation / mount; '-' = behavior absent in GT):

=== arm fs_t1 (data/runs/T1/fs_t1)

19 sequences, 262107 frames @30fps; frame-level, 30 fps, pooled over all frames of all sequences (CalMS21 Sec. E); macro = unweighted mean of attack/investigation/mount F1

behavior             P       R      F1   F1 95% CI (seq bootstrap)    GT fr  pred fr
attack           0.348   0.685   0.461   [0.168, 0.588]               12630    24852
investigation    0.708   0.657   0.682   [0.600, 0.740]               61275    56901
mount            0.718   0.478   0.574   [0.406, 0.682]               31848    21214
macro F1                         0.572   [0.475, 0.622]            

bout-level (IoU match): IoU>=0.3: attack R=0.570 P=0.247, investigation R=0.487 P=0.509, mount R=0.421 P=0.328; IoU>=0.5: attack R=0.349 P=0.151, investigation R=0.327 P=0.341, mount R=0.262 P=0.204

per sequence F1 (attack / investigation / mount; '-' = behavior absent in GT):

=== arm fs_rules (data/runs/T1/fs_rules)

19 sequences, 262107 frames @30fps; frame-level, 30 fps, pooled over all frames of all sequences (CalMS21 Sec. E); macro = unweighted mean of attack/investigation/mount F1

behavior             P       R      F1   F1 95% CI (seq bootstrap)    GT fr  pred fr
attack           0.219   0.742   0.338   [0.098, 0.484]               12630    42805
investigation    0.725   0.302   0.426   [0.374, 0.465]               61275    25501
mount            0.493   0.523   0.508   [0.327, 0.635]               31848    33773
macro F1                         0.424   [0.335, 0.482]            

bout-level (IoU match): IoU>=0.3: attack R=0.640 P=0.112, investigation R=0.308 P=0.283, mount R=0.477 P=0.191; IoU>=0.5: attack R=0.364 P=0.064, investigation R=0.147 P=0.135, mount R=0.318 P=0.128

per sequence F1 (attack / investigation / mount; '-' = behavior absent in GT):

paired differences (b minus a; CI from the same sequence resamples):
  zs_rules:zs_t1: macro +0.2053 [+0.1612, +0.2353] above zero (attack +0.1253, investigation +0.3952, mount +0.0952)
  zs_t1:fs_t1: macro -0.0077 [-0.0464, +0.0331] inconclusive (CI contains 0) (attack +0.0121, investigation +0.0467, mount -0.0819)
  fs_rules:fs_t1: macro +0.1483 [+0.1101, +0.1773] above zero (attack +0.1234, investigation +0.2554, mount +0.0662)
  zs_rules:fs_rules: macro +0.0492 [+0.0179, +0.0807] above zero (attack +0.0140, investigation +0.1866, mount -0.0529)

targets (Higher-is-better: MET if the point estimate is >= the threshold; MET ROBUSTLY if the 95% sequence-bootstrap CI lower bound is also >= it. Lower-is-better: MET if the point is <= the threshold; MET ROBUSTLY if the CI upper bound is also <= it. A metric without a CI can be MET, never MET ROBUSTLY. Anything else is NOT MET. No rounding: compared at full precision.):
  minimum_credible: level MET (needs every component); macro 0.5800 vs 0.4 MET ROBUSTLY; attack 0.4493 vs 0.25 MET; investigation 0.6350 vs 0.4 MET ROBUSTLY; mount 0.6558 vs 0.4 MET ROBUSTLY
  good: level MET (needs every component); macro 0.5800 vs 0.5 MET; attack 0.4493 vs 0.4 MET; investigation 0.6350 vs 0.5 MET ROBUSTLY; mount 0.6558 vs 0.55 MET
  tier-1 lift minimum_credible: +0.2053 vs +0.03 MET ROBUSTLY
  tier-1 lift good: +0.2053 vs +0.1 MET ROBUSTLY
  speed minimum_credible: MET; pooled 0.2635x vs <= 1.0 MET; median 0.2044x vs <= 1.0 MET
  speed good: MET; max 0.8799x vs <= 1.0 MET; pooled 0.2635x vs <= 0.5 MET

per-sequence metrics sealed in eval/results/score-test-20261028T164333Z-473c64.per_sequence.sealed.json (do not open before the test-2 decision is committed; EVAL_PLAN section 7)

logged to eval/results/runs.jsonl; full result eval/results/score-test-20261028T164333Z-473c64.json
```

Identity console output, verbatim:

```
run identity-test-20261028T164348Z-cdc866 split=test
87374 grid samples, 145.61 video min; params {'gate_px': 60.0, 'margin_px': 10.0, 'min_kp_score': 0.5, 'step': 3, 'centroid': 'all7', 'lags': [-10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 'min_speed_px': 1.0}

                id err id err unamb  ambig cov both lab cov both ok unknown*  n eval
overall         0.0029       0.0024  0.001       0.9726      0.9662   0.0000  164692
attack          0.0023       0.0021  0.001       0.9793      0.9770   0.0000    7072
investigation   0.0002       0.0002  0.000       0.9984      0.9895   0.0000   39208
mount           0.0284       0.0228  0.011       0.8054      0.7523   0.0000   15846
other           0.0001       0.0001  0.000       0.9960      0.9923   0.0000  102566
* 'cov both lab', 'unknown', and detections are retained-output diagnostics: the exported tracks hold only retained resident/intruder labels, so unknown and 3+ detections are 0 by construction

swap events: 375 (2.575/min); unambiguous only: 347 (2.383/min)
alignment (best lag, median px at best / at 0, margin):
  mouse071_task1_annotator1   resident: L=-2 18.5/24.6 +0.1   intruder: L=-2 23.2/26.7 +0.6
  mouse072_task1_annotator1   resident: L=-2 17.5/23.4 +0.5   intruder: L=-2 25.2/27.5 +0.2
  mouse073_task1_annotator1   resident: L=-2 19.4/28.0 +0.7   intruder: L=-2 21.9/26.1 +0.3
  mouse074_task1_annotator1   resident: L=-2 20.5/25.9 +1.1   intruder: L=-2 24.6/28.4 +0.7
  mouse075_task1_annotator1   resident: L=-2 22.2/26.6 +0.7   intruder: L=0 15.3/15.3 +0.2
  mouse076_task1_annotator1   resident: L=-1 20.7/25.4 +0.5   intruder: L=0 13.1/13.1 +0.9
  mouse077_task1_annotator1   resident: L=-2 24.1/29.0 +0.4   intruder: L=0 16.0/16.0 +0.4
  mouse078_task1_annotator1   resident: L=-1 22.7/28.2 +0.2   intruder: L=0 15.8/15.8 +1.2
  mouse079_task1_annotator1   resident: L=-3 26.0/30.6 +0.2   intruder: L=-2 18.8/20.7 +0.1
  mouse080_task1_annotator1   resident: L=-2 19.4/25.2 +0.6   intruder: L=-2 22.6/26.2 +0.2
  mouse081_task1_annotator1   resident: L=-4 19.6/25.3 +0.7   intruder: L=-5 29.1/35.2 +0.1
  mouse082_task1_annotator1   resident: L=-2 19.4/28.7 +0.1   intruder: L=-3 24.5/29.8 +0.6
  mouse083_task1_annotator1   resident: L=-3 20.3/28.2 +0.3   intruder: L=-3 22.7/29.9 +0.3
  mouse084_task1_annotator1   resident: L=-3 18.5/22.4 +0.4   intruder: L=-3 19.3/23.7 +0.3
  mouse085_task1_annotator1   resident: L=-2 17.5/21.5 +0.1   intruder: L=-2 22.4/25.9 +1.0
  mouse086_task1_annotator1   resident: L=-3 17.5/21.3 +0.7   intruder: L=-2 25.4/30.2 +0.9
  mouse087_task1_annotator1   resident: L=-3 24.1/31.6 +0.0   intruder: L=-2 14.9/18.3 +0.9
  mouse088_task1_annotator1   resident: L=-4 24.3/35.1 +1.0   intruder: L=-2 21.1/26.5 +1.3
  mouse089_task1_annotator1   resident: L=-3 25.8/30.3 +0.4   intruder: L=-2 20.7/23.3 +0.0

logged to eval/results/runs.jsonl; full result eval/results/identity-test-20261028T164348Z-cdc866.json
```

Hand computations (speed, cost, tier-1 aggregates) were done with read-only Python over:
- `data/runs/T1/zs_t1/manifest.json` (`wall_clock_s.per_video.<seq>.{track_s, rules_s}`, `frames`, `cost`);
- `data/runs/T1/zs_t1_first_pass_timing.json` (`seqs.<seq>.wall_s_approx`);
- `ops/spend.jsonl`: Fireworks entries from 2026-10-28T13:18 to the last mouse089 call at 14:46:57.198 give
  3,241 uncached calls, 0 errors, and $9.811516. `eval-sam` uncached entries from 08:38:15 to 09:08:52 give
  308 entries and $17.4838;
- `configs/detectors/*.json` (token usage of the synthesis calls).

No model API was called by the evaluator.

## Part D. The report (`eval/results/test1_report.md`, verbatim)

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

**Rounding.** F1, precision, recall, CI bounds and shares are **truncated** to 4 decimals (never rounded up).
Speed and cost are rounded **up**. Paired differences are rounded away from zero toward the unfavorable side.
Full-precision values are in the result JSON. All target verdicts were computed at full precision.

## 1. Supervision statement (attach to every number below)

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
