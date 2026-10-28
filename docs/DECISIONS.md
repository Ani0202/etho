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

## ADR-002 (2026-10-28): Second species = Caltech Fly vs. Fly (Aggression), with measured F1. APPROVED 2026-10-28 with conditions (see the decision log line)
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
- 2026-10-28: Zero-shot variants tried on dev and checked on dev-holdout (dev / holdout macro F1, rules only unless noted). None beat the R1 independent detectors + tier-1 restricted (0.482 / 0.503), which become the frozen zero-shot config for the test run:
  - tier-1 with per-tile tracker numbers in the prompt: 0.441 dev (+tier-1), worse than 0.482, and +41% cost;
  - one joint classify() for all behaviors: 0.316 / 0.332, worse than 0.366 / 0.337;
  - majority vote over 5 independent syntheses per behavior: 0.379 / 0.319, mixed;
  - tier-1 medium effort: 0.471 dev, no gain;
  - tier-2 video on disagreements: +0.005 dev for $2.30.
- 2026-10-28: Correction loop keeps a revision only if it handles more of the user's corrected windows AND shifts the detector's positive rate by <= 20% (2 feedback rounds, else keep the original). With 18 simulated corrections: holdout cascade 0.503 -> 0.563, but dev minus corrected windows 0.4995 -> 0.446. Mixed: report both, no robustness claim.
- 2026-10-28: Test-run pre-flight passed ($0): scripts/test_run.py regenerated all four dev-holdout arms from configs/frozen_test_v1.json; predictions identical to the logged R1 arrays, and all 786 tier-1 windows were cache hits (prompt byte-identical). Planner is NOT run on test: the frozen phrase is "mouse".
- 2026-10-28: Web app (etho/app): one FIFO worker (one job at a time); a `JobGateway` wrapper forces the app tag on every call and prefixes `caller` with `job:<id>|` so each ledger row is attributable (the pipeline modules hard-code their own tags, which would otherwise leak); the report module's fixed behavior trio is retargeted per job at runtime by `behaviors.report_behaviors` instead of editing the module; tier 1 may only reject ("none") for behaviors outside the CalMS21 trio; daily-cap estimate uses the spec's $0.02/s SAM + $0.02/s tier-1 rates (about 10x and 17x above measured) plus $0.06 per behavior, so the default $5 cap admits videos up to roughly 100 s; all rates are config.
- 2026-10-28: Web app review fixes: estimate rates now $0.003/s SAM + $0.003/s tier-1 + $0.06/behavior + $0.05 (supersedes the spec rates above; a 5-min upload fits under the $5 cap); per-job spend ceiling in `JobGateway`; uploads are re-encoded to a clean 10 fps mp4 (hardened ffmpeg flags) in the validate stage because the pipeline's own chunking call is frozen; showcases are read-only and a visitor who revises gets a private symlinked copy (a normal project), so no per-visitor state or cookies; client key is `request.client.host` only (IPv6 on /64), proxies via uvicorn `--forwarded-allow-ips`; revisions record `base_version` so a failed revision leaves no gap; project dirs expire after 48 h.
- 2026-10-28: Web app speed (etho/app only; benchmark modules untouched). Detector cache keyed sha256(name, definition, subject, partner, FEATURE_DOC) under data/app/detector_cache; lookup order is the benchmark seed (definition text + subject/partner exactly match configs/calms21_definitions.json -> configs/detectors/R1_zero_shot.json), then the cache, then synthesis; a reused detector must still run in the sandbox on the new video, else it is rewritten (and the rewrite is cached). Per-behavior provenance (seed/cache/fresh/revised) is stored in detectors.json, result.json (`detector_sources`), the report audit and the results page. Chunk length = clamp(duration/8, 10, 60) s; the cut runs in a thread during the planner and track_video reuses it (identical args); tier 1 and the overlay find the chunks via `tuning` (a shim over tier1.prepare_chunks plus `app_chunk_s` in tracking.json). Tier-1 concurrency 8 for app jobs (env ETHO_APP_TIER1_CONCURRENCY; 40 gave 429). Report and overlay run concurrently (jobs.PARALLEL_GROUPS); stages record start_s/end_s, and the total is job end minus job start, never the sum of stages. Reverse: set tier1_concurrency=6, PARALLEL_GROUPS=(), chunk_min_s=chunk_max_s=60.
- 2026-10-28: App chunk length is fixed at 60 s, the same as the benchmark (it was adaptive, down to 10 s, for speed). Measured on dev mouse016 against the keypoints:
  | Chunks | Both mice labeled | Identity error | Both labeled during mount |
  |---|---|---|---|
  | 60 s | 99.8% | 0% | 100% |
  | 10 s | 86.4% | 0% | 15.6% (new chunks start with the intruder hidden) |
  | 30 s | 85.5% | 2.0% | 100% |
  Short uploads are therefore slower (a 60 s clip is about 1.5x real time); videos of 2 min or more parallelize across chunks and stay under 1x. Accuracy over speed.
- 2026-10-28 13:15 UTC: (fallback, no reply in 2 h) Headline wording adopted for all written material: "No labeled examples in any prompt and no model training; pipeline settings tuned on 13 labeled development videos (disclosed)." Correction-loop arm is "few-shot (18 corrections)". Reversible if Aniket prefers other wording. The frozen `etho/report` text ("zero labeled training frames") is changed after test run 1.
- 2026-10-28: (Aniket) Headline wording, replacing the 5fb5078 fallback: "Training-free: plain-English behavior definitions, no labeled examples, no model training." Footnote: "Pipeline settings were tuned on 13 labeled development videos; test results are on 19 untouched videos." The few-shot arm is described as using 18 corrections simulated from dev labels.
- 2026-10-28: (Aniket) ADR-002 APPROVED with conditions: starts after test scoring, the live-app timing test and the rule sandbox; up to $13.60 from `ablation`; if wing threat is not clearly above the random floor on train by end of Oct 31 ET it ships as a qualitative clip with no F1 claim; README discloses pair-level scoring and cites the dataset.

## ADR-003 (2026-10-28): Public hosting of the web app during judging. APPROVED 2026-10-28 with changes (see the decision log line and ESCALATIONS)
**Context.** The app runs on localhost only (`bin/app`; `server.py:462` defaults to 127.0.0.1). Uploads use Standard models (`server.py:218`), and a measured 60 s upload costs $0.23-0.36. Judging starts after the Nov 4 9 PM ET submission and **its length is unknown**, so anything public has to run unattended for an unknown number of days and fail closed. The video (25%) is the primary artifact. CLAUDE.md does not list a hosted demo as a requirement; I did not re-read the Official Rules for this (unverified). `demo-reserve` ($35) opens on Nov 3 05:00 UTC and also has to pay for the recording.
**What the current guards bound (worst case, code as it is).**
- **Daily cap:** $5 per UTC day (`config.py:38`, `guards.py:45-54`). It covers every app-attributed ledger row on all providers (`jobgw.py:206-212`). The day rolls over at 00:00 UTC, which is 7 PM ET after Nov 1, so a scripted client can spend about $10 within a few hours either side of rollover.
- **Per-job ceiling:** `min(max(2x est, est + $0.50), headroom)` (`jobs.py:325-330`). It is checked before each call but not reserved (`jobgw.py:99-104`), so calls already in flight overshoot it: up to 8 syntheses, 8 tier-1 calls (`config.py:34-35`) and 5 SAM chunks. I estimate about $1 per job (unmeasured). Only one worker runs, so only one job overshoots at a time.
- **Result:** about **$6 per UTC day**, with **no limit on the number of days**. The only multi-day stops are the gateway refusing `demo-reserve` at $35 (fail-closed) and the $140 Meta / $90 Fireworks caps. At $5/day an attacker empties `demo-reserve` in about 6 days.
- **Rate limit:** 3 jobs/h per IP, held in memory (`config.py:46`, `guards.py:57-62`). A restart clears it, and anyone with a few IPs gets around it. It is a courtesy, not a spend bound.
**Gaps for public exposure.**
1. **No access control.** The `gate` middleware (`server.py:105-144`) checks only size, rate, queue and cap. Anyone with the URL can spend up to the cap.
2. **No cumulative app cap.** `SpendGuard` reads only `spend_today` (`guards.py:43,46`; `jobgw.py:226-228`).
3. **Wrong tag by default.** The tag is `dev` (`config.py:23`). Judging needs `ETHO_APP_TAG=demo-reserve`, which the gateway refuses until Nov 3 05:00 UTC.
4. **No kill switch or read-only mode.** The nearest thing is `ETHO_APP_DAILY_CAP_USD=0` plus a restart. That refuses uploads before the body is read (`server.py:123`) and refuses revisions (`server.py:386`), and showcases still render. But the upload form stays visible and returns 429.
5. **Retention is partial.** The window is 48 h (`config.py:54`), but cleanup only runs at startup and on a submit at most every 10 min (`server.py:189-191`, `jobs.py:171-193`), so an idle server keeps uploads past 48 h. It deletes only `data/app/jobs/<id>`. The upload's chunk mp4s in `cache/chunks` (`backend.py:41`) and its responses in `cache/gateway` (`gateway/config.py:67-68`) stay forever. The video also goes to Meta's Files API (log line 14), and we have not checked Meta's retention or whether we can delete it there. The UI has no privacy notice (`static/index.html`).
6. **Model-written code runs next to the secrets.** The rule sandbox runs that code, and a visitor can steer it through definition text (up to 2,000 chars, `validate.py:170`). It runs as the same Unix user that can read `~/.config/etho/secrets.env`, the git push credentials and the Claude/Codex logins. Today it has an AST filter, CPU/AS rlimits and a stripped env (`sandbox.py:79-81,104-144`), with no filesystem or network isolation. **bwrap (in progress) is a hard prerequisite for any live mode.** ffprobe/ffmpeg on untrusted files have hardened flags (`validate.py:59-137`) but are otherwise unconfined.
7. **No supervision.** `bin/app` is a bare `exec` (`bin/app:5`). Nothing restarts it and nothing alerts on failure. The lead session, which sends PushNotifications, may not be running during judging.
8. **Capability links.** Project URLs carry 48-bit random ids (`jobs.py:113-114`), and `/api/jobs/{id}/cancel` has no ownership check (`server.py:244-246`). This is acceptable if the notice says links are shareable.
9. **Shared detector cache.** Detectors written from one visitor's upload go into the shared cache (first writer wins) and are reused for another visitor's identical definition. In public live mode the cache should be lookup-only.
**Options.**
- **A. No public hosting.** Video, a README that runs locally, and showcases shown in the video. Judge value rests entirely on the video, and judges cannot open the audit trail. $0, no exposure, no effort, no ops.
- **B. Public read-only showcase.** The real app with upload and revise turned off, serving precomputed CalMS21 runs (CC BY 4.0, attributed in `result.json`): ethogram, overlay, strips, detector code and the per-call audit.
  - $0/day: with cap 0, no route can reach a model.
  - No untrusted code or ffmpeg path runs, though the secrets are still on the box. There are no uploads, so no privacy issue.
  - Effort about 3 h: a `read_only` flag that hides and refuses those routes, with tests; a tunnel; a systemd unit.
  - Ops risk: a crash leaves a dead link.
- **C. Public live app with uploads.** Fix gaps 1-7 and 9:
  - an access code given to judges in the submission (middleware);
  - $3/day plus a $20 cumulative cap from `demo-reserve`, which works out to about $4 per UTC day worst case including overshoot;
  - bwrap around the sandbox and ffmpeg;
  - a retention timer that also clears the job's `cache/chunks` and `cache/gateway` entries;
  - a notice: "public or synthetic video only; sent to Meta and Fireworks; deleted after 48 h";
  - supervision and a health check.
  - Few judges have two-animal lab video, so offer a sample clip. Repeat uploads of it are gateway-cache hits (~$0), and the audit discloses `cache_hit`.
  - Effort about 1 implementer-day before the Nov 2 freeze. This competes with Fly vs. Fly. The change touches the spend guards, so `bin/codex-review` applies, and routine Codex calls stop Nov 1.
- **D. Hybrid B + C with a kill switch.** B is the public face, and live upload needs the access code. A kill switch file (e.g. `ops/app-live-off`, checked per request, no restart) and an auto-off date (`ETHO_APP_LIVE_UNTIL`) control live mode. Hitting any cap or crashing the worker leaves B serving. Effort is C plus about 1 h.
**Hosting candidates.** Any public URL is escalation item 3, and anything paid is item 6.
- **This VM + Cloudflare named tunnel.** Free, no inbound port. It needs Aniket's Cloudflare account and a domain on Cloudflare DNS; buying one would be a paid item. Quick Tunnels are out: the URL changes on every restart, there is no uptime guarantee, and **they do not support SSE** (Cloudflare docs), which the progress page uses.
- **This VM + Tailscale Funnel.** Free, a stable `*.ts.net` URL, no domain needed.
  - Unverified: whether SSE works, and whether the real client IP reaches uvicorn. If it doesn't, every visitor shares one 3-jobs/h bucket. That is safe for spend but bad for access.
  - Bandwidth limits are unpublished (one anecdote says ~7 Mbps), so lower `max_upload_mb` from 200 (`config.py:25`) to about 50.
- **Separate small host.** It would hold the same keys and keep its own ledger, so neither box's gateway caps would see the other's spend. That needs a cap split (an ADR plus approval) and about half a day. Not worth it.
- **Hugging Face Spaces.** Rejected. Free-tier disk is ephemeral (unverified for the current tier), so `ops/spend.jsonl` and the caps would reset on every restart. It would also publish the code before Aniket flips the repo.
- **Shared-box mitigation for any VM option.** Run the app as a separate Unix user (needs sudo):
  - a secrets file holding only the Meta and Fireworks keys;
  - write access only to `ops/spend.jsonl` and `ops/inflight/`, so there is still one ledger and one set of caps;
  - no access to `.git`, `~/.claude` or the Codex credentials.
**Choice (proposed): D on this VM behind Tailscale Funnel, or a Cloudflare named tunnel if Aniket already has a domain there. Staged, with B as the floor.**
1. **By the Nov 2 freeze ($0, mock gateway):** read-only mode, kill switch, access code, cumulative cap, retention fix, notice, systemd unit and separate user. **Gate:** if bwrap for the sandbox and ffmpeg is not merged and tested by then, ship B only and leave live mode off.
2. **Nov 3:** record the demo on localhost, not through the public URL. Put B up publicly so the URL can go into the submission.
3. **At submission:** live mode on (`demo-reserve` tag, $3/day, $20 cumulative), which leaves $15 for the recording and Aniket's own live runs. Auto-off on a date Aniket picks.
**Evidence.**
- Every file:line above was read this session.
- The $0.23-0.36 job cost is the lead's measurement and was not re-run.
- The ~$1 overshoot is an estimate and was not measured.
- The Cloudflare facts (no SSE on Quick Tunnels, a named tunnel needs a Cloudflare DNS domain) come from Cloudflare docs, via search on Oct 28.
- The Funnel limits are only "non-configurable" per Tailscale docs; the numbers are unverified.
**Cost and time.** A: $0. B: $0 and about 3 h. D: B plus about 5-6 h of implementer and reviewer time, and at most $20 of `demo-reserve` over the whole window. No change to any gateway cap.
**Risks.**
- **Rules:** none for A or B. C and D invite non-public uploads (item 4); the notice and access code reduce this but do not prevent it.
- **Budget:** bounded by the cumulative cap.
- **Integrity:** none. The app touches no eval data, and its outputs are never quoted as benchmark numbers.
- **Ops:** nobody may be watching during judging, so the design fails closed. Meta credits or keys may be revoked after the event (unverified); live jobs would then fail and B would keep serving.
**Reverse.**
- Touch the kill switch: live mode goes off and B stays up.
- Stop the tunnel: this is option A.
- Delete `data/app/jobs/` and the jobs' cache entries.
- No migration is needed at any step.
**Codex view:** pending. The lead may run `bin/codex-ask` on this ADR before Nov 1 (routine budget).
**Approval needed from Aniket:**
1. Public hosting at all? If yes: B only, or D (live upload behind an access code given to judges in the submission)?
2. Host: this VM via Tailscale Funnel (your account), or a Cloudflare named tunnel (do you already have a domain on Cloudflare)?
3. Live budget from `demo-reserve`: $3/day and $20 in total (leaving $15 for the recording), or a different split?
4. Live window: from submission until what date (auto-off)?
5. The notice wording, 48 h retention, and uploads sent to Meta (Standard mode) and Fireworks: OK?
6. OK to create a separate Unix user for the app (sudo) and keep only the Meta and Fireworks keys in its secrets file?
- 2026-10-28 15:05 UTC: Test run 1 tier-1 crashed at 15:03 in the fs_t1 arm on one Fireworks ReadTimeout. The gateway books ambiguous failures at the estimate ($0.013) and never retries them, and the frozen tier-1 code doesn't catch it. zs_t1 had completed: 3,241 windows, $9.81, ~5,332 s wall. Resumed with the identical command and frozen config, same run id T1. Completed calls replay from the gateway cache at $0, so this is still one run: no new tuning, no labels read. zs_t1 speed comes from the first pass, reconstructed from the ledger (`data/runs/T1/zs_t1_first_pass_timing.json`), not from the replay. The crash log is kept at `data/runs/T1/logs/t1_test_attempt1_crash.log`.
- 2026-10-28 17:05 UTC: Live-app timing test (no change, measurement). A fresh 4-min clip, mouse021 dev 60-300 s re-encoded so nothing is cached, with the CalMS21 definitions (so 3 seeded detectors and no synthesis call). Bubblewrap sandbox active. 191.2 s for 240 s of video = **0.80x real time**, $0.7456 ($11.2 per hour of video).
  - Stages: validate 15.6 s, plan 27.2 s, track 41.4 s (0.17x, both animals in 97% of samples), tier-1 88.2 s (67 windows, Fireworks median latency 7.5 s, max 36.6 s), report and overlay ~18 s in parallel.
  - Tier-1 latency is the bottleneck, so a 60 s clip will stay above 1x.
- 2026-10-28: Fly behavior definitions are quoted verbatim from Chen et al. 2002 PNAS, Table 1 (`configs/flies_definitions.json`). wing_threat: "One fly quickly raises both wings to a 45° angle towards opponent". lunge: "One fly rears up on hind legs and snaps down on the other". The dataset paper (Eyjolfsdottir 2014) is closed access and no open copy exists; Nilsen 2004 and the CADABRA definitions could not be read. Chosen before any fly prediction exists, never edited after seeing labels.
- 2026-10-28 17:43 UTC: Aniket reset the Codex usage limits. Cleared the gpt-6.1-sol entry in ops/codex/limit.json (old entry kept in limit.cleared-20261028T1743Z.json) so the queue drains now.
- 2026-10-28: (Aniket) Fly budget raised to $19.5 total from `ablation`, and Etho Local is dropped. Headline wording approved in two versions, plus fixed accuracy and speed lines (ESCALATIONS, ~13:50 ET answers). ADR-003 approved with changes: read-only public showcase; live uploads behind an access code; $3/day and $20 total from `demo-reserve`; auto-off Nov 10 ET; Tailscale Funnel; 24 h deletion; separate Unix user.
- 2026-10-28: ADR-003 implemented (branch `worktree-agent-ab0d7665ca99e742a`, runbook `docs/HOSTING.md`). Engineering choices: (1) the daily cap is a rolling 24 h window, not a UTC day, so spend cannot double around midnight (tests changed to match); (2) caps are summed over ledger rows whose caller is `job:*` on the app's tag, plus gateway-booked abandoned reservations on that tag (counted because the gateway cannot say whose they were); (3) admitting a job reserves its ceiling and each call reserves its worst case, so the ceiling is a bound, not a check; (4) retention counts from the upload, 24 h, by a timer, with a private gateway cache so entries can be deleted (the shared repo cache is never touched); (5) public mode requires a >= 12-char access code file AND bubblewrap, else read-only; (6) the service sees only bind-mounted code, a read-write `ops/`, and its own state dir (`ProtectHome=tmpfs`), so no ACLs on `/home/ubuntu` are needed; (7) Meta Files API copies cannot be deleted (no delete in the gateway surface; ids are recorded). Not run: any sudo command, any provider call.
- 2026-10-28: ADR-002 fly path, tracking adapter (branch worktree-agent-ac6e626dd37570d7d; frozen CalMS21 modules untouched, new `etho/tracking/fly_*.py`). SAM probe on train movie 1 (2 windows x 50 consecutive samples, 6 requests, $0.06): phrase "fly" returned 0 objects at native and 4x; "fruit fly" works. Native 144 px beat 4x upscale: exactly-2-masks 90%/98% (native) vs 70%/80% (4x), and the 4x run truncated a fly's mask once (area 146 vs ~200 px). Chosen: native, phrase "fruit fly", 30 fps, 60 s chunks with 1 s overlap. Dev tracking (train movies 1-2, frames 22500-31499): $1.824 each, 73-75 s wall (0.25x real time), both flies in 99.9% / 99.5% of samples. Thresholds: identity unknown when centroids < 1 body length (27.2 px); a stitch is ambiguous (new identity epoch) only when the best pairing beats the alternative by < 0.5 body lengths per fly.
- 2026-10-28: ADR-002 fly path, review fixes ($0, re-derived from raw RLE). Gateway always built from ETHO_ROOT (shared ledger); `run_fly.py` prints a job estimate and refuses > $5 without `--i-have-approval`; `public_data` and `gw` are required args. Velocity features now use a body-only centroid (3x3 opening) and are NaN (`vel_ok`/`vel_ok_s` = 0) within 0.1 s / 0.5 s of an identity-epoch change or an id_unknown run edge, and within 0.1 s of an abrupt (> 25%) mask-area change (a wing appearing in the mask). Contact runs are tested for a silent SAM id swap and relabeled (new epoch). Stitch with an empty slot is ambiguous (neutral cost 3 BL). Overlap default stays 1 s (movies 1-2 stay cache-identical; a changed default would re-bill $3.65): use `--overlap-s 2` for new runs.
- 2026-10-28: (Aniket) Demo plan approved with changes (ESCALATIONS, ~14:15 ET). Up to $5 of `demo-reserve` is unlocked on Nov 2 for the rough cut, which is before the gateway's Nov 3 unlock; this needs a gateway config change on Nov 2, limited to $5.
- 2026-10-28: App tier-1 concurrency goes from 8 to 16 (`ETHO_APP_TIER1_CONCURRENCY`).
  - Measured with `scripts/speed_sweep.py` on fresh unused-train clips (mouse024, mouse010, mouse009, about 5 min each, uncached, one clip per setting):

    | Concurrency | Tier-1 | End to end |
    |---|---|---|
    | 6 | 0.88x | 1.12x |
    | 12 | 0.50x | 0.72x |
    | 16 | 0.39x | 0.60x |

  - No HTTP 429s; 40 gave 429s on Oct 28. Fireworks median latency was ~12 s at every setting.
  - Cost is unchanged; the sweep spent $3.15 (`dev`).
  - The test-run claim stays 0.88x, since it is what was measured on test; the sweep is a fresh-clip measurement, not a re-score.
  - Why test (0.88x) looked ~4x slower than dev (0.22x): the dev figure excluded tier-1. Without tier-1, test is ~0.27x.
  - Reverse: set the env var back to 8.
