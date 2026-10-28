# Etho state

_Updated 2026-10-28 19:00 UTC (15:00 ET)._

## Status (updated 2026-10-28 17:35 UTC / 13:35 ET)

### Test run 1: DONE, scored once (`eval/results/test1_report.md`, sections 1-10 plus corrections in section 11)
| Arm | Macro F1 [95% CI] | Attack | Investigation | Mount |
|---|---|---|---|---|
| **zs_t1** (primary, training-free + tier-1) | **0.580** [0.493, 0.621] | 0.449 | 0.635 | 0.656 |
| zs_rules | 0.375 | 0.324 | 0.240 | 0.561 |
| fs_t1 (18 corrections simulated from dev labels) | 0.572 | 0.461 | 0.682 | 0.574 |
| fs_rules | 0.424 | 0.338 | 0.426 | 0.508 |

- **Baselines:**
  - 73.1% of the raw-pose 1D ConvNet baseline (.793; per class .664/.814/.900). Better published methods exist (.829 and .864).
  - Frequency-matched random reference: 0.1345.
- **Paired differences:**
  - Tier-1 lift: +0.205, robust.
  - fs_t1 vs zs_t1: inconclusive, so no accuracy claim for the correction loop.
- **Identity error:** 0.29%. Coverage is 0.973, so the "good" level is not met.
- **Speed including preprocessing:** 0.88x real time pooled, median 0.86x, max 1.54x. Preprocessing was measured
  after the run; the "good" speed target is not met. Tier-1 is the bottleneck.
- **Cost:** $11.25 per hour of video (model API at logged rates, excluding the planner).
- **Spend:** Fireworks $19.80 for tier-1 (approved up to $25). The run crashed once on a timeout and was resumed
  from cache; the decision is logged.
- **Codex test-results gate (Astra):** done, `ops/codex/ask-20261028-165452-293961.md`. The accuracy result stands.
  The corrections are applied in report section 11. The headline wording is escalated.

### Also done today (to 19:00 UTC)
- **Sandbox:** the rule sandbox is isolated with bwrap and merged.
- **Live app:** a fresh 4-min clip ran at 0.80x. With tier-1 concurrency 16 (now the app default) the fresh-clip sweep gave 0.60x end to end.
- **Headline wording:** approved and applied in the README and the report header. The evidence card and accuracy/speed lines are approved (ESCALATIONS ~13:50 and ~14:35 ET).
- **License:** Apache-2.0 LICENSE and NOTICE. The SAM-derived decoder files stay under the SAM License.
- **Demo plan:** v2 approved, script ~431 words with sources, clip mouse075 0-300 s. CalMS21: no test-2 (A3); per-video results unsealed.
- **Fly vs. Fly:**
  - tracking merged (dev movies 1-2, $3.71);
  - definitions from Chen 2002;
  - eval plan frozen (F1), and Aniket's claim bar added (ADR-004 / F2: CI lower > r, F1 >= 0.40, shown next to the simple baseline).
- **Hosting (ADR-003, approved):** guards built on a branch, and Meta file delete is live-verified. The Claude review says merge after fixes and NO-GO for go-live as written; the fixes are in progress.

### In progress
- Fly rule synthesis and tuning on dev movies 1-2, then `configs/frozen_flies_v1.json` (implementer, worktree).
- Hosting review fixes (implementer, worktree), then merge. Codex review queued for tomorrow.

### Next
1. Fly: lunge gate (dev 1-2), then decide, then track train 3-5 for the branch, then the wing gate by Oct 31 23:59 ET; test only if GO.
2. App: an optional fix for discarded tier-1 verdicts (dev-tested only, never claimed in the test numbers).
3. Oct 30 ET checkpoint report.
4. README: results, baselines with the in-sample caveat, fly pair-level disclosure, "How this was built".

### Waiting on Aniket
- Official Rules: max video length, hosting/format, required content, form fields.
- Sudo commands in docs/HOSTING.md, after the hosting merge. Go live only after Nov 3.

## Day-by-day plan (ET)

| Day | Goal |
|---|---|
| Wed Oct 28 | Long-video SAM test on 1 dev seq. Fast decoder and tracking cleanup, plus per-frame features. Minimal perception planner. |
| Thu Oct 29 | Rule synthesis with a sandbox. Full dev SAM pass (about $2.50). First rules-only dev F1, logged to `eval/results/runs.jsonl`. |
| Fri Oct 30 | Adjudication tier 1 (Fireworks strips) and tier 2 (Baseten clips). Tune effort and thresholds on dev; cost and accuracy per tier. |
| Sat Oct 31 | Correction loop (fix bouts → revised rule → accuracy delta). Report: ethogram, metrics, methods paragraph, audit trail. Freeze EVAL_PLAN targets (Codex critique first). |
| Sun Nov 1 | Web app (upload, definitions, results) with live-demo guards: precomputed showcase, size and rate limits, daily cap. Second species if the license is OK. Test run 1 after spend approval. |
| Mon Nov 2 | Polish and fix bugs. Etho Local (Glimmer distillation) only if everything above is green. **Feature freeze at end of day.** |
| Tue Nov 3 | Record the demo video (`demo-reserve` unlocks). README, "How this was built". Test run 2 if justified (cached tracks). |
| Wed Nov 4 | Pre-submission audit (reviewer + Codex), final README. Internal target 12:00 PM ET. Aniket submits by 9:00 PM ET. |
