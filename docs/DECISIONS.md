# Decisions

One line per reversible decision; ADRs (context, options, choice, evidence, reversal) for
architectural or methodological ones. Newest at the bottom.

- 2026-10-28: Gateway uses httpx directly (no provider SDKs): one retry/cost/cache path for all four OpenAI-compatible providers.
- 2026-10-28: Cap enforcement reserves each call's worst-case cost (in-flight files under `ops/inflight/`, flock-protected) and settles with actual usage, so parallel workers can't jointly overshoot a cap. Stale reservations expire after 2h.
- 2026-10-28: Meta tag budgets are enforced as refusals at 100% (moving money between tags needs an ADR anyway); 80% crossings and 50%/80% provider-cap crossings are written to `ops/alerts.jsonl` for escalation.
- 2026-10-28: `demo-reserve` tag is refused by the gateway before 2026-11-03 05:00 UTC (Nov 3 00:00 ET).
- 2026-10-28: Together AI hard cap set to $5 (it's Aniket's own money; CLAUDE.md gives no cap). Disabled anyway unless TOGETHER_API_KEY is set.
- 2026-10-28: `-contributor` models require `public_data=True` at the call site; the gateway refuses otherwise.
- 2026-10-28: Cost for calls that return no usage is booked at the worst-case estimate, not $0. A SAM stream that fails mid-way is also booked at its estimate.
- 2026-10-28: Use Meta's `meta-sam-parser` (PyPI, SAM License: use/modify/redistribute allowed, must acknowledge SAM in publications) to decode SAM 3.1 masks instead of reimplementing the codec.
- 2026-10-28: Local videos sent to SAM are uploaded via the Files API (cached by sha256) rather than inlined as base64.

## ADR-001 (2026-10-28): Per-animal identity from SAM 3.1 (CalMS21; default for two-animal video)
**Context.** Tracking has to output a "resident" track and an "intruder" track per video, with no labels and inside the `eval-sam` tag ($40).
Evidence so far is docs/SPIKE.md: 2 train clips, 400 frames at 10 fps. Test sequences go up to ~20k frames (~11 min, ~6.7k samples at 10 fps), and nothing that long has been run yet.
**Options.**
- **A.** One generic call ("mouse"). Keep the two most persistent tracks inside an arena ROI. Label each track by its mean mask intensity. Bridge short gaps.
- **B.** One call per animal with attribute phrases ("black mouse" / "white mouse").
- **C.** A or B, run as overlapping chunks (60 s chunks, 1 s overlap), with identities stitched across chunks by intensity.
- **D.** Self-host the open SAM 3.1 weights on a GPU.
**Choice: A, one request per video. C is the pre-planned fallback. B is rejected for attribute phrases. D is not pursued.**
The track interface stays chunk-agnostic, so moving to C is a config change, not a rewrite.
Identity rule:
1. Split the tracks' intensities at the midpoint between the two track medians (per video, unsupervised).
2. Relabel a track segment only if its per-frame intensity sits on the other side of that midpoint for >= 5 consecutive samples.
3. Hold or interpolate gaps of <= 10 samples (1 s). Longer gaps become a "partner not visible" feature.
4. When the two animals look alike (no intensity gap), fall back to SAM track persistence and flag it in the report.
Product code never reads CalMS21 keypoints. They are used only by `eval/` to score identity.
**Evidence.**
- A: 0 swaps in 400 frames; identity agreed with keypoints on 742/746 detections; intensities ~51 vs ~143 (measured, SPIKE §2).
- Spurious tracks (33 and 16 frames) were all removable by ROI plus persistence (measured, clip B only).
- B: returned nothing on 200/200 frames of clip B and only frames 154-199 of clip A (measured).
- Cost at 10 fps, dev x2 + holdout + test A: A = $24.34, B = $48.67, which exceeds the tag (EVAL_PLAN §8).
- C overhead is ~10/600 frames (~1.7%), about +$0.40 on that plan. Stitching is unverified.
- D: availability of open weights, setup time, and the GPU price (~$8/hr H100 per CLAUDE.md) are unverified. It needs an escalation (dedicated GPU) and possibly a new paid service, and costs at least half a day of work.
- Each test video fits in a single request: max ~6.7k samples vs a 15k-frame limit. The limit is unverified beyond the spike note.
**Perception planner.** `muse-spark-1.3-contributor` on public data (Standard on uploads), effort `medium`.
1. From 4 sampled frames it proposes up to 3 short noun phrases plus an arena ROI.
2. Each phrase is checked with SAM image calls on 5 spread-out frames ($0.0125 per phrase). The planner inspects the overlays and the count of detections inside the ROI.
3. Image detection is more permissive than video detection (SPIKE failure 1). So the top phrase must also pass a 50-sample video probe ($0.01): the expected animal count in >= 90% of samples.
4. If it fails, the planner re-prompts with a more generic noun.
5. Attribute phrases are never accepted on the image check alone. Distinct-noun pairs (MABe22 "beetle" / "ant") may run B-style only if the video probe passes. That case is untested.
Cost: ~$0.05-0.07 per video, ~$1.3 for test A, tagged `eval-sam` / `eval-llm`. This is an estimate.
For eval, planner outputs on test are logged and never inspected by a person (EVAL_PLAN §7).
**Measure next.** This uses the first dev SAM pass, which is already budgeted, so it adds no cost. Run the long (>= 10k-frame) dev attack sequence first.
1. Identity accuracy and swaps per minute vs keypoints (EVAL_PLAN §4.3), two-animal coverage, distinct raw SAM IDs per video, and re-entry after full occlusion.
2. Request wall-clock vs video length (time to first frame, total), plus errors and timeouts.
3. Decode cost in s/mask on the single-decode path, peak RSS, and whether decoding keeps up with the stream.
**Reversal triggers.**
- **A to C** on any dev sequence, after post-processing, if any of these happens:
  - identity accuracy < 0.95, or > 1 swap per 5 min;
  - the request fails twice or times out;
  - latency > 2x video length (C parallelizes the work).
- **C to escalate D** only if C still misses the 0.95 identity target on dev, or SAM API throughput can't finish test A by Nov 1 ET.
- **Decode:** if it measures > 0.2 s/mask (projected > ~4 h wall-clock for test on 4 cores), the compiled codec port becomes the top Phase 2 item. The pipeline shape stays the same.
**Reverse.** Set the `tracking.chunk_s` / `tracking.phrases` config and re-run the dev SAM pass ($2.47 in the `eval-sam` tag). Cached tracks of unchanged videos are reused.
**Risks.**
- Rules: none for A or C. D triggers escalation items 6 and possibly 1.
- Budget: B on test A breaks the `eval-sam` tag.
- Integrity: the phrase and the thresholds above are frozen in the test config.
**Codex view** (`ops/codex/ask-20261028-045338.md`, gpt-6-astra xhigh; given options and evidence without the choice, though it saw the ADR via its startup reads, which it disclosed):
- It picks A as the baseline. Test E (box/point re-seed, forward and backward propagation) next. Keep C conditional on demonstrated failures. Reject B and D for now.
- Keep biological identity separate from SAM IDs, and allow "unknown".
- Use persistence/ROI as evidence, not a hard "two tracks" rule.
- Never interpolate posture through occlusion.
- Route missing-track intervals to adjudication; otherwise mount bouts can't be recovered.
- Key catch (verified): every spike gap precedes a track's first detection. So recovery after losing an *established* track is untested.
- The intensity identity resolver itself is unvalidated: the spike scored majority-nearest-reference identity.
- **Lead's reconciliation:** agree on all points. E and a "mount with preceding footage" clip go first in Phase 2. Identity becomes {resident, intruder, unknown}, and gaps are kept as explicit missingness.
- 2026-10-28: (Aniket) Speed is a headline requirement: end-to-end ≤ 1x real time per video-minute. Mask decode and SAM parallelism are now P0, ahead of scoring.
- 2026-10-28: (Aniket) Test-split SAM pass pre-approved up to $20, once targets are committed, the eval-plan Codex gate is done, and config is frozen. MABe22 dropped from anything public; second species to be Fly vs. Fly (if licensed) via an ADR.
- 2026-10-28: SAM masks requested as `one_bit` from now on: 28x less wire data, and binary masks are all the features need.

## ADR-002 (2026-10-28): Second species = Caltech Fly vs. Fly (Aggression), with measured F1. PROPOSED: needs Aniket's approval
**Context.** Aniket dropped MABe22 (no license). He wants Fly vs. Fly with a measured F1 on 1-2 behaviors, plus an identity method that works for identical-looking flies. Spend comes from the Meta `ablation` tag ($20), which is shared with the Etho Local labels. No second-species data is downloaded into the repo or committed until he approves.
**(a) Dataset.** Options: Fly vs. Fly Aggression; Fly vs. Fly Courtship / BoyMeetsBoy; zebrafish dyads (Sci Data 2026); MABe22 kept local-only.
- **Choice: Aggression.zip**, CaltechDATA zrznw-w7386 (DOI 10.22002/D1.1893). Verified: `metadata.rights` = cc-zero; 10 labeled 30-min movies, 144x144 px, 30 fps, 2 flies; official split in `traintest.mat` (train 1-5, test 6-10). movie11 is excluded (not in the split; its contents are unchecked).
- **Fallback: Courtship.zip.** Same record and license; its contents are unverified.
- The zebrafish set has no frame-level labels. It could only give a qualitative demo, not a measured F1, so it does not meet Aniket's ask.
**(b) Behaviors. Choice: wing_threat (primary) and lunge (conditional).**
- **wing_threat:** bouts are 25-490 frames (movie1), so they survive 10 fps. Risk: wings are translucent, and SAM masks of a ~15x25 px fly may leave them out, which weakens the area/elongation signal (unverified). Tier-1 adjudication on upscaled crops covers this.
- **lunge:** bouts are 3-4 frames, about 1 sample at 10 fps. Timing error alone would cap frame-level F1 there, so lunge needs 30 fps. Its signal (a centroid velocity spike toward the partner) needs only the mask centroid, which should hold up at 144 px.
- **charge / hold / tussle:** not scored. Their counts are untallied, and hold/tussle are contact-heavy, which is where identity is weakest.
- **Lunge go/no-go is decided on train movies only.** Go needs both: SAM sees 2 flies in >= 90% of samples, and the synthesized rule reaches bout-level recall >= 0.5.
**(c) Identity.**
- **Primary F1 is pair-level.** A frame is positive if either fly does the behavior; ground truth is the union of both flies' bouts. No identity is needed.
- **Secondary per-fly (actor) F1.** The actor is picked per bout from features (the fly whose velocity spikes toward the other; the fly whose mask widens), not from appearance.
- **Track continuity.** Within a request, SAM track persistence. Across the parallel 60 s chunks (ADR-001 C, needed for the <=1x speed target), Hungarian matching on the overlap frames. Cost = distance from a constant-velocity predicted centroid + normalized area difference. Body size is a tiebreak only (unverified). When centroids are within ~1 body length, identity is `unknown`, never guessed.
- **Ground-truth tracks.** `*_track.mat` and `*_feat.mat` are read **only in eval/**; product code never reads them. Eval fixes **one permutation per window** (Etho tracks to fly1/fly2, by majority Hungarian on centroid distance). It then reports per-frame identity accuracy, swaps per minute, and % unknown.
**(d) fps, subset, cost** (SAM: $0.20 per 1k sampled frames). Windows are fixed by rule before any test label is read: the middle 5 min of each movie (frames 22500-31499).
- **Dev:** train movies 1-2 at 30 fps, 2 x 9,000 frames = **$3.60**. This includes the ADR-001 50-sample video probe at native and 4x-upscaled resolution (~$0.02). All prompts and thresholds are tuned here; movies 3-5 are spare dev.
- **Test, lunge go:** movies 6-10 at 30 fps, 5 x 9,000 frames = **$9.00**. wing_threat is scored from the same tracks.
- **Test, lunge no-go:** wing_threat only, 10 fps, middle 10 min (frames 18000-35999), 5 x 6,000 = **$6.00**.
- **LLM:** planner, rule synthesis (`-contributor`, high) and report, about $0.5-1 on Meta (estimate). Tier-1 adjudication bills to Fireworks.
- **Total:** worst case about **$13.6 of $20 `ablation` (68%)**, leaving ~$6 for Etho Local. Test runs at most twice; run 2 reuses cached tracks ($0). The test pass is over $5, so it needs `ops/spend-plan.md` and this approval.
- **Time:** about 1 implementer-day (mat loader in eval/, video adapter, reuse of the existing features and rules). Starts after the Oct 30 CalMS21 checkpoint. Test run by Nov 1 ET, before the Nov 2 freeze.
**(e) Metric.**
- Frame-level F1 per behavior at native 30 fps, consistent with CalMS21. 10 fps predictions are held over native frames. Frames labeled `unknown` are excluded (their semantics are unverified).
- Secondary: lunge bout-level F1 (any overlap), per-fly F1, and a prevalence-matched random floor.
- **Baseline:** Eyjolfsdottir et al., ECCV 2014. Supervised, per-fly, full test movies, frame- and bout-level F1. The per-action table has **not been retrieved**, so quote no number until it is read. Label it "supervised, full movies, not directly comparable", since our numbers come from windows. Never say "beats".
- Targets and protocol go into `docs/EVAL_PLAN.md` (evaluator-owned) and are committed before the first test run.
**(f) Commits. Nothing second-species is committed before Aniket approves.**
- After approval, commit only code and config, eval loaders, a data manifest (DOI, file names, sha256, window frame ranges), derived metrics and plots, and a README citation. CC0 requires none; cite the dataset and paper anyway.
- Never commit raw video, .mat files, or frames (CLAUDE.md). Data stays in a gitignored `data/`, after a `df -h` check (701 MB zip).
- Demo clips are fine under CC0.
**(g) Risks / reversal.**
- **Risks.**
  - SAM *video* may lose the flies at 144 px; only the image endpoint is verified. Mitigation: upscale 4x before upload (billing is per frame, not per pixel).
  - Translucent wings may weaken wing_threat.
  - Lunge no-go leaves only one behavior.
  - The window subset differs from the paper's protocol.
  - This spend competes with Etho Local.
- **Rules:** CC0 is clean. Train-movie labels are used only for dev thresholds, as on CalMS21, so "zero-shot" holds. State it as "thresholds tuned on train movies 1-5".
- **Reverse:** switch to Courtship (same pipeline, dev re-run $3.60), or drop to a qualitative clip with no F1 claim. Nothing is committed before approval, so undoing it means deleting local data.
- Codex blind critique (`bin/codex-ask`) is pending: the lead runs it before the test pass.
**Approval needed from Aniket:**
1. Use Fly vs. Fly Aggression (CC0) as the second species. Score wing_threat, plus lunge if it passes on train, with pair-level frame F1 on test movies 6-10 (fixed middle windows).
2. Spend up to ~$13.6 from `ablation`: dev $3.60, test $9.00 or $6.00. This leaves ~$6 for Etho Local.
3. Commit only code, a manifest, and derived metrics, never raw data.

Fallback is Courtship.zip. Zebrafish has no labels, so no measured F1 is possible.
- 2026-10-28: SAM mask decoding: numba port of meta-sam-parser's codec (`etho/tracking/mask_codec.py`, derived under the SAM License, copy in `third_party/meta-sam-parser/LICENSE`). Bit-exact with the reference, which the differential fuzzing also confirmed, including its strict one_bit canonical checks (restored incrementally). Measured: one_bit 0.66-0.94 ms/mask (~170-250x), lossless 1.5-2.9 ms/mask (~55-100x). The line parser no longer decodes, so each mask is decoded once.
- 2026-10-28: (dev-tuned) Tier-1 adjudication may only confirm investigation or reject a window (label "none"); it never promotes to attack/mount. R1 dev: rules 0.366 -> tier-1 all labels 0.406 -> restricted 0.482 macro F1. Reason: low-effort tier-1 attack/mount verdicts were 21-26% accurate vs GT window majority; investigation 58%, none 85%; confidence uninformative. To be confirmed on dev-holdout.
- 2026-10-28: ADR-001 swap trigger needs a metric fix: eval/identity.py counts a one-sample flip as two swap events (1.8/min on dev with only 0.19% identity error). Use the identity error rate (target <= 0.05, TBD) as the trigger instead.
