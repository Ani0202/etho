# Etho state

_Updated 2026-10-28 04:36 UTC (00:36 ET, Wed Oct 28). Phase 0 and Phase 1 are done; waiting on
Aniket at the checkpoint._

## Done (verified this session)

- **Repo skeleton:** uv project, `make check` (ruff + pytest, 43 tests, no network), plus
  `docs/DECISIONS.md` and this file.
- **Gateway (`etho/gateway/`):**
  - providers: Meta Chat Completions and Responses, Files API, SAM 3.1 (image and streamed
    video), Baseten, Fireworks, and Together (disabled without a key);
  - policy: explicit effort on every call, validated per provider; Glimmer effort goes in the
    system prompt; tags required; `-contributor` models need `public_data=True`; `demo-reserve` is
    locked until Nov 3 ET;
  - money: hard caps and Meta tag budgets, enforced with cross-process reservations;
    50%/80% alerts go to `ops/alerts.jsonl`;
  - plumbing: disk cache keyed by request hash (a hit costs $0 and is still logged); retries with
    Retry-After; every call logged to `ops/spend.jsonl`.
- **CLIs:** `bin/spend`, `bin/muse`, `bin/open`, `bin/fw`, `bin/sam`.
- **`.claude/settings.json`:**
  - allow and deny lists per the kickoff;
  - a PreToolUse hook (`.claude/hooks/guard-bash.py`) blocks direct provider calls, force pushes,
    and shell reads of secrets (tested).
- **Smoke tests:**
  - Meta, Baseten, and Fireworks (text and image) all work.
  - Glimmer returned 404, as expected.
  - SAM image and video work.
  - Codex replied `ready`.
- **CalMS21:**
  - license CC BY 4.0;
  - all 89 Task 1 sequences have video;
  - labels and keypoints extracted to `data/calms21/seq/` (`eval/calms21_extract.py`).
- **Split draft:** `eval/splits/calms21_v1.json` (dev 8 seqs, dev-holdout 5, test all 19), plus a
  draft `docs/EVAL_PLAN.md` (targets TBD, not frozen).
- **Spike:** `docs/SPIKE.md`. The recommendation is a **conditional GO**, revised after Codex's
  blind critique.
  - Single-phrase `"mouse"` SAM tracking keeps stable, correct identities.
  - **The intruder disappears under the resident for the whole mount.**
  - Color phrases fail on this grayscale video.
  - Mask decode, not SAM, dominates tracking time.
  - Baseten video works for coarse descriptions only.
- **Codex gateway review:** found 7 issues (double-billing on ambiguous retries, unbounded
  estimates, reservation expiry, per-key locking, truncated SAM streams, CLI fps and memory). All
  are fixed, with 9 regression tests (52 tests total).
- **Spend to date:** $0.35 total (Meta $0.33, Baseten $0.02, Fireworks $0.003).

## In progress

- ADR-001 (tracking identity: one `"mouse"` call + intensity identity; chunking as the fallback)
  is written in `docs/DECISIONS.md`. Its blind Codex critique is still running.

## Next (Phase 2, pending Aniket's checkpoint answers)

1. **Long-video tracking test:** one full dev sequence through SAM (about $0.25). Measure ID
   persistence over minutes, request latency, and frame alignment with mask centroids.
   Also test intruder re-seeding through mounts with a SAM box/point prompt ($0.04).
2. **Go/no-go checkpoint (Codex's condition, adopted):** a rules-only (+ tier 1) dev run on full
   sequences, with per-behavior P/R/F1 and coverage during each behavior, **before** any UI work.
3. **Tracking module:**
   - a fast mask decoder (decode once, then a compiled port);
   - track cleanup: arena ROI, two persistent tracks, identity from intensity, gap bridging;
   - per-frame features, with masks stored as RLE only.
4. **Perception planner:** propose a phrase, verify it on sampled frames, and fall back to generic
   nouns.
5. **Rule synthesis:** English definition → Python scorer over features (sandboxed) → bouts. Then
   the first rules-only dev F1.
6. **Adjudication cascade:**
   - tier 1: Fireworks frame strips;
   - tier 2: Baseten clips (crop around the mice; the spike suggests full-frame video arrives as
     low-resolution thumbnails);
   - report the cost and accuracy of each tier.
7. **Correction loop, report, then the web app.**

## Blocked / waiting on Aniket

See `docs/ESCALATIONS.md`: test-set spend approval (later), the MABe22 license, and demo claim framing.

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
