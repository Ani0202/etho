# Etho state

_Updated 2026-10-28 04:36 UTC (00:36 ET, Wed Oct 28). Phase 0 and Phase 1 are done; waiting on
Aniket at the checkpoint._

## Phase 2 status (updated 2026-10-28 ~08:20 UTC / 04:20 ET)

### Done and measured (dev / dev-holdout, frame-level F1, CalMS21 definition)
| Pipeline | dev macro | holdout macro | holdout attack / inv / mount |
|---|---|---|---|
| zero-shot rules (Muse Spark writes detectors from the MARS definitions) | 0.366 | 0.337 | .472 / .205 / .336 |
| + tier-1 strips (Fireworks Open, low, restricted to investigation/none) | **0.482** | **0.503** | .563 / .515 / .429 |
| + tier-2 video (Baseten Open, high, on disagreements) | 0.487 | not run | adds +0.005 for $2.30, so not used |
| few-shot: + 18 simulated corrections (revision with self-check) + tier-1 | pending | **0.563** | .543 / .634 / .513 |

Random floor 0.139 (dev). Supervised CalMS21 baseline 0.793 (same metric; test split).

Other results:
- **Speed, uncached, end to end** (5-min video, chunk + SAM + decode + rules): 65 s = 0.22x real time.
  Tier-1 adds about 0.2x on long videos (174 windows on a 7-min video in 97 s at 6 concurrent calls).
- **Identity vs keypoints (dev):** error 0.19%; both mice labeled in 99.2% of samples.
- **Mount occlusion:** once a track exists it survives the mount (spike test with preceding footage).
- **Cost:** SAM $0.12/video-min; tier-1 about $0.07/video-min.
- **Built:** a fast decoder (about 0.7 ms/mask, bit-exact) and a perception planner (Muse Spark proposes
  phrases; SAM image check + 5 s video probe; under 4 cents per video).

### Next
1. A tier-1 prompt with per-tile features (zero-shot lever).
2. Report generation (ethogram, metrics, methods paragraph).
3. Freeze the EVAL_PLAN targets, then the Codex eval-plan gate.
4. Test-split SAM pass (pre-approved up to $20).
5. Web app.
6. Fly vs. Fly once Aniket approves ADR-002.

### Waiting on Aniket
ADR-002 (second species) approval.

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
