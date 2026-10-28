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
**Codex view:** pending. The lead should run `bin/codex-ask` on the options and evidence, blind to this choice.
