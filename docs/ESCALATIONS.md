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
