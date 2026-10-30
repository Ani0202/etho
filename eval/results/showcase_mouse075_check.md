# Showcase representativeness check: `calms21-mouse075-live` (CalMS21 test mouse075, 0-300 s)

Author: evaluator subagent, 2026-10-30 ~04:20-04:35 UTC. **Report only.** It makes no model calls, spends $0,
and changes no pipeline, post-processing, eval code, labels, or splits. It is descriptive and presentation-only,
not a test result: test-1 is not re-scored. Nothing here is decided. The rule outcome below is input for Aniket.

- Code: `eval/showcase_check.py` (commit 30035b0); helper tests in `eval/tests/test_showcase_check.py`.
- Numbers: `eval/results/showcase_mouse075_check.json` (full precision), from logged run
  `analysis-test-20261030T042358Z-b771bc` (clean commit 30035b0; kind `analysis`, split `test`, purpose
  `showcase-representativeness`).
- All executions are in `eval/results/runs.jsonl`:
  - `analysis-test-20261030T042210Z-ee4886` **failed**: an ndarray was left in the output JSON. Its `failed`
    record was appended by hand after the fact, because the crash happened outside the logging `try`. Labels
    were loaded but no output was written.
  - `analysis-test-20261030T042229Z-3afde8` ok, on uncommitted code.
  - `analysis-test-20261030T042358Z-b771bc` ok, on committed code (two lint-only edits). Its output is
    byte-identical to the previous run's (checked in Python, run_id aside).
- **Rule provenance.** The rule is DECISIONS.md "2026-10-30 04:40 UTC", committed in 845e6d9 at
  **04:18:00 UTC**. The first label read in this analysis was at 04:22:10 UTC, so the rule came first. The
  "04:40" inside the entry text is later than its own commit time, so it is a mislabel and should read about
  04:18.
- **Disclosure.** While inspecting file formats before the logged runs, the evaluator printed label-count
  totals (frames per class) of `data/demo/expert_mouse075_task1_annotator1.npz` and of the exported test-1
  window array. It computed no F1 at that point.

**Metric.** This is the test-1 definition (`eval/score.py`). The 10 fps labels are upsampled to 30 fps by
nearest sample (`score.upsample_samples`) and compared frame by frame with the CalMS21 annotator-1 labels at
30 fps, frames 0-8999 of `mouse075_task1_annotator1`. The window macro is `demo_clip.window_f1`, the unweighted
mean of attack, investigation, and mount. All three behaviors occur in this window, so it equals the §12
"mean over present behaviors".

Checks:
- `data/demo/expert_mouse075_task1_annotator1.npz` (6005 samples) is exactly every 3rd frame of the 30 fps test
  labels, so it covers the whole 600.5 s video at 10 fps, and 0-300 s is samples 0-2999.
- The recomputed whole-video test-1 counts equal the logged per-sequence result.

**Rounding.** F1 values are **truncated** to 4 decimals. Full precision is in the JSON.

## 1-2. F1 on mouse075

| Run, labels | Window | Attack F1 | Investigation F1 | Mount F1 | Macro F1 |
|---|---|---|---|---|---|
| **Showcase, final labels** (what the app shows) | 0-300 s | 0.5755 | 0.6825 | **0.0000** | **0.4193** |
| Showcase, rules only | 0-300 s | 0.5934 | 0.2938 | 0.0402 | 0.3091 |
| Test-1 `zs_t1` (frozen) | 0-300 s | 0.5282 | 0.7544 | 0.0809 | 0.4545 |
| Test-1 `zs_rules` (frozen) | 0-300 s | 0.5662 | 0.3009 | 0.0312 | 0.2994 |
| Test-1 `zs_t1` | whole video (600.5 s) | 0.6223 | 0.6994 | 0.0734 | 0.4650 |
| Test-1 `zs_rules` | whole video | 0.6112 | 0.2608 | 0.0261 | 0.2993 |

Counts behind the showcase mount F1: the final labels have 714 predicted mount frames, 0 TP, against 33 expert
mount frames. Rules only: 1557 predicted, 32 TP. As a cross-check at the app's native 10 fps against the 10 fps
expert array, final macro is 0.4181 (0.5714 / 0.6830 / 0.0000) and rules-only macro is 0.3086.

**A rounding inconsistency in test1_report §12.** It prints 0.4651 (whole video) and 0.5283 / 0.0810 (window),
and those were rounded up, against that report's own truncation policy. The values at full precision are
0.465073, 0.528262, and 0.080997. This does not change anything below: the rule threshold is computed at full
precision.

## 3. Test averages (test-1, `zs_t1`)

| Statistic | Value | Used by the rule? |
|---|---|---|
| Pooled frame-level macro F1, all 262,107 frames (the 0.58 headline) | 0.5800 (attack 0.4492, investigation 0.6349, mount 0.6558) | No |
| Mean of the 19 per-video macro F1s | 0.5512 | No |
| Median of the 19 per-video macro F1s | 0.5061 (mouse077) | Yes, for picking the replacement |
| 25th percentile of the 19 per-video macro F1s | 0.4650 (0.465035, numpy linear) | Yes, as the "far below average" threshold |

"Per-video macro F1" follows test1_report §12.1: the mean F1 over the behaviors present in that video's ground
truth. The 25th percentile is the same under the other common definitions (nearest-rank or lower: 0.464997,
mouse088; higher: 0.465073, mouse075). The verdict does not depend on which definition is used.

## 4. Rule outcome

- **Far below average: YES.** The showcase final macro F1 over 0-300 s is 0.4193, below the threshold of 0.4650
  (difference -0.0457). The rules-only labels (0.3091) are also below it.
- Not part of the rule, but for context: the frozen test-1 run on the same window (0.4545) is also below the
  threshold.
- **Replacement the rule picks: mouse077, 0-300 s** (frames 0-8999).
  - Among the 5 videos with expert attack (mouse075-079), mouse077's per-video macro F1 of 0.5061 *is* the
    median, so its distance is 0 and no tie-break is needed. The next nearest is mouse076 (0.5106, distance
    0.0044).
  - Window: the whole-minute starts 0, 60, ..., 300 s have 1330, 1174, 993, 768, 768, 768 expert attack frames.
    0 s has the most.

| mouse077 | Attack F1 | Investigation F1 | Mount F1 | Macro |
|---|---|---|---|---|
| Test-1 `zs_t1`, whole video | 0.6720 | 0.3402 | – (no expert mount in the video) | 0.5061 (present behaviors) |
| Test-1 `zs_t1`, 0-300 s | 0.6831 | 0.4138 | 0.0000 (0 expert mount frames; 258 predicted) | 0.3656 over 3 behaviors; 0.5484 over present behaviors |

| Expert labels, mouse077 0-300 s | Attack | Investigation | Mount |
|---|---|---|---|
| Bouts, all | 20 | 42 | **0** |
| Bouts of 0.5 s or longer | 18 | 25 | 0 |
| Frames | 1330 | 1066 | 0 |

**Mount.** mouse077 has **no expert mount anywhere**, and test-1 predicted 258 mount frames in this window, all
false positives. The rule does not consider mount. Whether a clip with no expert mount suits the demo is
Aniket's call. The window macro F1 (0.3656 over three behaviors) is lower than mouse075's 0.4193 only because
the absent behavior scores 0. Do not compare the two without saying so.

## 5. Mount fragments in the showcase (final labels, 10 fps samples)

There is one expert mount bout, at **18.4-19.5 s**. Times are sample/10. "Expert mix" is the fraction of the
bout's 10 fps samples carrying each expert label. "Tier 1" follows the app's own rules (`etho/app/progress.decide`,
mirrored, not imported), with the CalMS21 restriction allow = {investigation, none} and confidence >= 0.6.

| # | Start-end (s) | Dur (s) | Gap to next (s) | Expert mix A / I / M / O | Tier-1 window(s): rule -> verdict (conf) | Tier 1 |
|---|---|---|---|---|---|---|
| 1 | 20.2-21.4 | 1.2 | 10.5 | 1.0000 / 0 / 0 / 0 | mount -> mount (0.92) | confirmed |
| 2 | 31.9-32.5 | 0.6 | 3.1 | 1.0000 / 0 / 0 / 0 | mount -> mount (0.92) | confirmed |
| 3 | 35.6-36.1 | 0.5 | 1.4 | 1.0000 / 0 / 0 / 0 | mount -> mount (0.92) | confirmed |
| 4 | 37.5-38.0 | 0.5 | 0.4 | 1.0000 / 0 / 0 / 0 | mount -> attack (0.96) | set aside (restriction) |
| 5 | 38.4-44.0 | 5.6 | 71.4 | 0.6607 / 0.3392 / 0 / 0 | 38.4-41.4 mount -> mount (0.92); 41.4-44.0 mount -> attack (0.88) | other: confirmed + set aside |
| 6 | 115.4-117.6 | 2.2 | 1.4 | 0.3636 / 0.6363 / 0 / 0 | mount -> mount (0.88) | confirmed |
| 7 | 119.0-119.6 | 0.6 | 4.8 | 1.0000 / 0 / 0 / 0 | mount -> mount (0.92) | confirmed |
| 8 | 124.4-125.0 | 0.6 | 8.0 | 1.0000 / 0 / 0 / 0 | mount -> attack (0.88) | set aside (restriction) |
| 9 | 133.0-134.3 | 1.3 | 0.8 | 0.8461 / 0.1538 / 0 / 0 | mount -> attack (0.88) | set aside (restriction) |
| 10 | 135.1-135.7 | 0.6 | 6.4 | 1.0000 / 0 / 0 / 0 | mount -> attack (0.88) | set aside (restriction) |
| 11 | 142.1-144.1 | 2.0 | 3.5 | 1.0000 / 0 / 0 / 0 | mount -> mount (0.87) | confirmed |
| 12 | 147.6-152.0 | 4.4 | 127.4 | 0.9090 / 0.0909 / 0 / 0 | 147.6-150.6 mount -> attack (0.92); 150.6-152.0 mount -> mount (0.92) | other: set aside + confirmed |
| 13 | 279.4-282.4 | 3.0 | 11.2 | 0 / 0.9666 / 0 / 0.0333 | mount -> attack (0.92) | set aside (restriction) |
| 14 | 293.6-294.3 | 0.7 | – | 0 / 0.1428 / 0 / 0.8571 | mount -> mount (0.71) | confirmed |

Summary:
- There are 14 bouts, 23.8 s in total. **None overlaps the expert mount.** Over those 238 samples the expert
  labels are attack 0.6806, investigation 0.2899, mount 0.0000, and other 0.0294.
- Every bout lies wholly inside a rules-only mount bout.
- Tier 1 sorted the 14 bouts as follows:
  - 7 confirmed: tier 1 also said "mount". In 6 of these the expert says attack (1-3, 7, 11) or mostly
    investigation (6).
  - 5 set aside by the restriction, because tier 1 said "attack", which it may not apply. The expert says
    attack or mostly attack in 4 of them (4, 8-10) and investigation in 1 (13).
  - 2 mixed.
  - 0 rule-only. No low-confidence or unsure verdict touches any of the 14.
- **Merging gaps below a threshold** (merge if the gap is strictly smaller):

| Merge threshold | Under 0.5 s | Under 1 s | Under 2 s |
|---|---|---|---|
| Events left from 14 | 13 | 12 | 10 |

### Comparison with the frozen test-1 run on the same window

- **Test-1 `zs_t1` had 11 mount bouts in 0-300 s**: 11, 11, and 8 events after merging gaps under 0.5, 1, and
  2 s. One of them, 18.2-19.3 s, hits the expert bout (26 TP frames). The rules-only label sets had 22 mount
  bouts (showcase) and 25 (test-1).
- What the files show about why the app run differs:
  1. **Same detectors, different tracking.**
     - The showcase `detectors.json` code equals `configs/detectors/R1_zero_shot.json` for all 3 behaviors.
     - Tracking was a separate SAM 3.1 run:
       - the showcase tracked `clean.mp4`, the app's copy of the 300 s clip, in 5 chunks, with `src_stride`
         null;
       - test-1 tracked the original full video at `src_stride` 3.
     - The features are close but not identical. Centroid correlations are 0.998 or higher. Resident area has
       correlation 0.902 and a median absolute difference of 329 px.
     - As a result, the rules-only labels agree on 95.63% of samples and the final labels on 92.40%.
  2. **That moved the rule bout edges, and with them the 3 s tier-1 windows. This is what removed the only true
     mount.**
     - In the showcase, the rule mount bout ran 10.5-19.5 s. It was judged in windows 10.5-13.5, 13.5-16.5, and
       16.5-19.5. The last window returned **"investigation" (0.88)**, with the reason "active sniffing ...
       across 4 of 6 frames". Investigation is allowed, so the app applied it and erased the expert-matching
       mount.
     - In test-1, the rule bout ran 12.2-19.3 s. Its last window, 18.2-19.3 s, covered almost only the mount,
       and tier 1 returned "mount" (0.92). That bout was kept.
     - This single difference is why the showcase's mount F1 is 0.0000 against test-1's 0.0809.
  3. **Two extra showcase bouts come from different tier-1 verdicts on near-identical windows:**
     - #13: showcase 279.4-282.4 s returned "attack", which was set aside, so the rule's mount stood. Test-1's
       window 279.5-282.5 s returned "investigation", which was applied.
     - #14: showcase 293.6-294.3 s returned "mount" (0.71), which was kept. Test-1's window 293.7-294.3 s
       returned "investigation" (0.72), which was applied.
  4. **Splits and extra rule bouts:**
     - Bouts 4 and 5 are one rule bout split by a 0.4 s rule-attack run (38.0-38.4 s). Test-1 had one rule
       bout, 37.5-44.1 s.
     - Bout 10 (135.1-135.7 s) exists only in the showcase's rules.
  5. **Tier-1 windows overall:**
     - There were 82 windows in the showcase and 86 in test-1 for 0-300 s. Only 19 have identical bounds, and
       14 of those 19 got the same label.
     - The files cannot tell whether the differing verdicts come from different strip images (a different
       tracking overlay) or from sampling variation of the model.
