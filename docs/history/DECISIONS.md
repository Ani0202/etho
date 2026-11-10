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
- 2026-10-28: ADR-002 fly path, rule synthesis and frozen config (branch worktree-agent-a79b3f2f1eda1fc2f; new `etho/flies/`, `scripts/run_fly_etho.py`, `scripts/freeze_flies.py`; CalMS21 modules and tracking code untouched). Rules-only; no tier-1. Meta spend $0.14 of the $1.50 limit (`ablation`, `muse-spark-1.3-contributor`, effort high, public data).
  - **Dev finding that drove the design.** The tracker's `vel_ok` guard hides the velocity columns near identity-unknown runs and abrupt mask-area changes, which is exactly where lunges happen: only 9.6% of annotated dev lunge frames had a valid `toward_speed`. `etho/flies/extras.py` adds unmasked, swap-robust `*_raw` motion columns (flies re-matched between samples) computed from the existing table, so no re-tracking. With them, the plain-prompt lunge candidates went from frame F1 0.03-0.07 to 0.10-0.32.
  - **Prompt variants** (generic hints written from the definition text; the dev movies chose between them): `plain`, `physics` (silhouette and timescale cues), `simple` (physics plus a parsimony instruction that also said "a distance gate"), `simple2` (the same without the distance gate). The dev diagnosis of `simple` showed the clearest long wing bout was missed because every candidate gated on proximity, which our own hint had suggested. `simple2` wing candidates: 0.32-0.75 raw frame F1 (vs 0.26-0.47 for `simple`, 0.02-0.42 for `plain`+extras).
  - **Final.** wing_threat: `simple2`, 6 candidates, vote >= 3 of 6, drop runs under 2 s. lunge: `physics`, 5 candidates, vote >= 2 of 5, bridge gaps and drop runs under 0.1 s. Each behavior's prompt variant, vote threshold and post-processing were chosen on dev movies 1-2 (6 wing bouts in one movie, 77 lunge bouts), so the dev numbers are optimistic. Medoid selection (label-free) is implemented and was logged, but votes did better on dev for both behaviors.
  - **Tracking overlap_s = 1.0**, not the 2.0 the tracking implementer suggested: the dev tracks Etho was tuned and gated on were made at 1.0, re-tracking dev at 2.0 would cost about $3.65 of the fly budget left for the gates and test, and 1.0 gave localized coverage 0.999 / 0.994 on dev. The weak spot is the first second of a chunk (movie 2 chunk 4: both flies in 87% of it). Reverse: set `overlap_s` in `configs/flies_tracking.json` before freezing.
  - `configs/frozen_flies_v1.json` is written by `scripts/freeze_flies.py freeze` at the commit that holds every asset; nothing under `etho/` or `configs/` may change after it.

## ADR-004 (2026-10-28): Fly vs. Fly claim bar (Aniket's decision; supersedes the evaluator's "clearly above" bar). Recorded before any fly gate or test labels are opened.
**Context.** EVAL_PLAN §14 (amendment F1, frozen) defined "clearly above the random floor" as F1 ≥ max(2r, r+0.15)
with the CI lower bound > r. That is about F1 ≥ 0.19 for wing threat. Aniket: "not what I meant."

**Decision (Aniket, 2026-10-28 ~14:35 ET).** An on-screen F1 claim for any fly behavior requires all three:
- (a) the movie-bootstrap CI lower bound is strictly above the random reference r;
- (b) point F1 ≥ **0.40**;
- (c) the number is shown next to the frozen simple baseline's score (`eval/flies_baselines_v1.json`) on the
  same frames.

Otherwise the fly segment is labeled **"qualitative"**, with no F1 on screen.

**How it applies to the frozen tree.**
- **Wing-threat train gate (by Oct 31 ET):** "clearly above" now means (a) and (b) on the train gate run.
  The relative bar is replaced by this stricter absolute one.
- **Test:** runs only if the train gate passes. The on-screen claim then needs (a), (b) and (c) on test.
- **Lunge:** the lunge gate still only selects the branch (window and fps). Any lunge F1 claim must meet the
  same three conditions, on test.
- **Not changed:** evidence guards, the CI-informative rule, the deadline and the one-execution limits.

**README regardless of outcome:**
- report the simple baselines, stating that the 0.989 mask-width F1 is in-sample on dev movie 2 (6 bouts,
  picked from 540 grid points);
- if Etho's synthesized rule resembles the width threshold, say it was written by Muse Spark from the
  plain-English definition, with no hand-written threshold.

**Next.** The evaluator writes this as amendment F2 to EVAL_PLAN §14 and updates `eval/flies_criteria_v1.json`
before any gate run.

**Reverse:** only by Aniket.

## ADR-005 (2026-10-28): What to build between now and the Nov 2 freeze. APPROVED 2026-10-28: bundle (b) + planner card + sample clip, up to $1.50 from `dev`; (a) rejected (see the decision log line).
**Context.** Accuracy is final: test-1 zs_t1 macro F1 0.580, no test-2, no more CalMS21 tuning, flies qualitative. Nothing here changes or re-claims a test number. It is Oct 28 ~18:10 ET. Freeze is Nov 2 EOD, rough cut Nov 2, recording Nov 3, internal submission target Nov 4 12:00 PM ET, official deadline 9:00 PM ET (SUBMISSION.md). Routine Codex reviews stop Nov 1, so anything touching the sandbox, scoring, spend or eval must merge by Oct 31. The 3:00 video is full: DEMO_PLAN v3 has ~6 s of slack. The hosted app is a primary judging artifact (ADR-003).
**Found while reading (all verified this session).**
- **No showcase is packaged.** `data/showcase/` does not exist, so the public read-only site would list nothing today. `make_showcase.py:86` asserts the CalMS21 trio, so a fly showcase can't be packaged as written.
- **Precomputed showcases look thin.** The plan step is "skipped", speed is "n/a", and the audit says "prompts and responses were not captured" (`make_showcase.py:137,159-167`; `project.js:62-65`).
- **The planner is buried.** Its steps (propose, per-phrase SAM image counts, 50-frame video probe) are stored (`data/app/jobs/je496f25acb6f/result.json`), but the UI shows them only as raw JSON inside the collapsed audit (`project.js:347`).
- **The planner doesn't do what the architecture says.** It checks detection *counts*. Muse Spark never looks at an overlay, and there is no re-prompt: it tries its 3 proposals in order, then falls back (`planner.py:91-115`). The prompt itself suggests "mouse"/"fly" (`planner.py:35`).
- **The video's error trace may not reproduce.** The traced error (mouse075, 37.5-43.5 s) comes from the T1 predictions. The live app re-encodes the clip and cuts its own chunks, so tier-1 strips are not cache hits. Whether the error shows up again in a live run is **unverified**.
**Options.**
- **(a) "Ask your experiment."** A plain-English question over a collection of scored videos. Muse Spark writes analysis code, the sandbox runs it, and the app shows a plot plus a test, with the code.
  - *Sandbox (read, not executed):*
    - The AST check bans all imports (`sandbox.py:257-258`). The namespace is np, math and the helpers, and the runner requires `detect`/`classify` returning one value per sample (`sandbox.py:103-110,334-336`).
    - scipy is in `.venv` (pyproject dependency) and the venv is bind-mounted into bwrap (`sandbox.py:140-141,178-179`), so its files are present, but model code can't reach it. matplotlib is not installed.
    - So: a separate `etho/analysis/` runner that reuses `_bwrap_argv`, `_read_output` and the AST rules with its own entry point `per_video(v) -> dict of named numeric values`. Leave `sandbox.py` HELPERS/RUNNER untouched, because HELPERS is prepended to every detector.
    - The model writes only the per-video metric. The app runs a fixed, seeded numpy test (paired sign-flip or two-sample permutation, bootstrap CI) and renders the plot in JS from the returned arrays. The sandbox returns no images.
  - *Data:* the 19 per-video zs_t1 ethograms (`data/runs/T1/zs_t1/*.npz`, present), Etho predictions only, no labels. CalMS21 has no experimental conditions, so honest questions are within-session only: first vs second half (habituation of investigation), latency to first attack, bout durations, investigation→attack transitions. Attack occurs in only 5 videos (mouse075-079), which may share animals (test1_report:78), so attack questions have n ≤ 5. Questions that need conditions must come back as "this collection has no condition labels".
  - *p-hacking optics:*
    - the unit of analysis is the video;
    - effect size and CI are shown before p;
    - every question asked in a collection is listed, with Holm-adjusted p across them;
    - the banner reads "demonstration on benchmark data, Etho labels (0.58 macro F1), not a scientific finding".
  - *Cost (estimate, unmeasured):* 4-6k tokens in, 4-10k out at effort high.
    - Standard ~$0.03-0.06 per question, ~$0.10 with one repair. Live judge questions are Standard (user text; `store.py:130-132` precedent) and count against the $3/day.
    - Precomputed showcase questions use `-contributor` (public data), < $0.01 each.
    - Dev prompt testing ≤ $2 from `dev` ($11.99 left).
  - *Time:* ~12-14 h (runner, tests, collection builder, route plus estimate plus job ceiling, JS plot, prompt, review fixes). It touches the sandbox and a new spend route, so Codex review is needed by Oct 31.
  - *Risk:* moderate. The new POST is closed by default (`server.py:44-47,188-191`), but it needs its own estimate and ceiling, and it queues behind a 5-min upload on the single worker. Frozen detectors are unaffected if `sandbox.py` is left alone.
  - *Video:* needs ~15 s. Either replace the correction beat (1:45-2:00), which drops a headline feature from the video and is Aniket's call, or cut the fly beat to ~5 s (2:30-2:35) and use the 6 s of slack.
- **(b) Live-product polish and a rehearsed judge path.**
  - *Package showcases ($0, from disk):* mouse075 from T1 (tracking, zs_t1 and zs_rules preds present) and a dev video. Generalize `make_showcase.py` for the fly clip (~1 h).
  - *Rehearsal run:* one real app run of mouse075 0-300 s on `dev`, ~$0.93 (scaled from the measured $0.7456 per 240 s). It doubles as the dress rehearsal for the 0:35-1:05 beat and shows whether the 1:20-1:45 error reproduces. Package it as the main showcase, so judges see real stage timings, plan steps, prompts and per-call costs instead of "n/a".
  - *Judge path:* a "start here" card on the home page, plan and audit sections easier to find, and empty and loading states.
  - *Time:* ~5-6 h. Frontend, `scripts/` and `data/showcase` only, so the reviewer subagent is enough and no Codex review is needed.
  - *Risk:* low. `data/showcase` must exist before the service starts (HOSTING.md:266).
  - *Video:* no cut. The same footage gets better in 0:35-1:05 and 2:45-3:00 ("live at the link").
- **(c1) Show the perception planner.** A card showing the 4 sampled frames, the proposed phrases, each phrase's SAM image result with mask outlines, and the video-probe pass/fail.
  - *Change:* `planner.py` gets an additive `save_dir` argument (planner.py isn't run on test, and the fly runs are finished). ~3 h with tests. Send it for routine review by Oct 31 as perception-adjacent.
  - *Cost:* $0 on mouse075, since it rides on the (b) run. One fly planner run is ~$0.03 (`-contributor`, CC0).
  - *Payoff:* if its alternates include "fruit fly", the card shows "fly" failing SAM's video probe and "fruit fly" passing. That is the README's "generic noun" lesson, live, and the clearest "creative use of Meta tools" moment we have. Whether the alternates include "fruit fly" is **unverified**.
  - *Honesty:* say "SAM checks each phrase", not "Muse Spark inspects the overlay", and disclose that the prompt hints at generic nouns.
  - *Video:* ~3 s under the existing line "Muse Spark picks what to track" (0:35-1:05), or behind the fly beat. No words added, nothing cut.
- **(c2) Sample clip for judges.** A download link to a 60 s public CalMS21 *dev* clip that a judge uploads with the access code.
  - *Run cost:* each run is $0.23-0.36 (measured, ADR-003) and takes ~90 s, since a 60 s clip runs at ~1.5x real time (chunk-length line). That is ~8 runs per day under $3.
  - *Build:* ~0.5 h, with no new spend route.
  - *Video:* none.
- **(c3) Rejected: app-only fix for discarded tier-1 verdicts.** At low effort, tier-1 attack and mount verdicts were 21-26% right on dev (log line ~126), so the fix would mostly add errors. The app would also diverge from the frozen config behind the test numbers. It needs dev spend plus Codex review, and it would erase the video's most honest beat (1:20-1:45).
- **(c4) Rejected: tier-2 video in the app.** It gave +0.005 dev F1 for $2.30, adds Baseten latency against the ~1x speed line, and the README already says it isn't used.
**Choice (recommended): (b) + (c1) + (c2) as one bundle.**
- *Cost:* ~$1.5 from `dev` (rehearsal ~$0.93, fly planner ~$0.03, two sample-clip checks).
- *Time:* ~9-10 h implementer time, merged by Oct 31 so the Nov 2 rough cut uses it.
- *Why:*
  - It fixes a real gap in a judged artifact: an empty public showcase.
  - It de-risks two video beats (the live run and the traced error) before the rough cut.
  - It makes Meta's models visibly do agentic work without cutting a second of the video.
  - It changes no scoring code, spend route or eval path.
- *Not (a):* it is a new headline feature (Aniket's call). It changes the sandbox and adds a spend route under an Oct 31 review deadline, costs a 15 s beat, and on CalMS21 it can only ask within-session questions of 0.58-F1 labels.
- *If Aniket wants (a) anyway:* build it precomputed-only, with 3 fixed questions on the 19-video collection using `-contributor` (< $0.05), the app-side test, and no live question box. If it isn't merged by Oct 31, it is dropped.
**Evidence.** File:line references above were read this session. Showcase absence and on-disk T1 files were checked by glob. The rehearsal, (a) per-question and fly-planner costs are estimates. Whether the traced error reproduces and what the fly planner proposes are unverified.
**Risks.**
- *Rules:* none. Showcases use public data (CalMS21 CC BY, flies CC0) and stay gitignored.
- *Budget:* ≤ $1.6 from `dev`, with no tag moves. (a) live adds Standard calls inside the existing $3/day.
- *Integrity:* none, because the app reads no labels. If (a) is ever validated against human labels, that is evaluator-owned, eval/-only, and needs its own ADR.
**Reverse.**
- Delete `data/showcase/<name>` (or restore the prior dir).
- Revert the static/ commits.
- Remove the planner's `save_dir` argument. It's additive, and old plans still render.
- Remove the sample link.
- No migration and no frozen file touched.
**Codex view:** not requested (routine budget). Optional `bin/codex-ask` before Oct 31.
**Approval needed from Aniket:**
1. The (b)+(c1)+(c2) bundle and ~$1.5 from `dev`?
2. Do you want "Ask your experiment" at all? If yes: precomputed-only, and which beat it replaces (the correction loop, or most of the fly beat)?
- 2026-10-28: (Aniket, Official Rules excerpt) The submission deadline is **Nov 4 12:00 PM ET**, with no late submissions. This replaces the 9:00 PM ET in CLAUDE.md. Hosting is required and must stay up through Nov 9. The video is hard-limited to 3:00 and must show code built during the event. The checklist is docs/SUBMISSION.md and the demo plan is v3.
- 2026-10-28: (Aniket, ~18:05 ET correction to the line above) The official deadline is **Nov 4, 9:00 PM ET**; 12:00 PM ET is our internal target and stays there. CLAUDE.md already said this; SUBMISSION.md, DEMO_PLAN.md and STATE.md are fixed. Accuracy is final (no test-2, no more CalMS21 tuning). Aniket is confirming the time zone.
- 2026-10-28: (Aniket, ADR-005) APPROVED the bundle: live-product polish + planner card + sample clip, up to $1.50 from `dev`, merged by Oct 31 ET; a routine Codex review before Oct 31 if allowance permits, otherwise the Claude reviewer. (a) "Ask your experiment": NO, listed in the README "What's next" with no claims; the correction-loop and fly beats stay. Traced-error contingency: use a different real error if the live run shows one (rewrite the 1:20-1:45 line); if none, show the committed test-report trace labeled "from the test run"; never re-roll runs. The sample-clip page carries CalMS21 attribution and "CC BY 4.0". After the bundle, before the freeze: fresh-clone README test, judge path on the hosted URL from a phone on cellular, kill-switch and cap-hit drills; results go in the Oct 31 status.
- 2026-10-28: (Aniket) Script v4 adopted (391 words, timeline 0:00-2:57). "Largest single gain in the pipeline" changed to "the biggest gain of any step on top of the rules" (rules alone are +0.240 over the random reference); "more than six hundred tests" kept (662 passed). Deadline confirmed: Wed Nov 4, 9:00 PM (treated as ET), via the CV platform; Nov 3 is the last full day for platform questions.
- 2026-10-28: (Aniket, form review) Public-app caps raised for the judging window: $8 per rolling 24 h from Nov 4 00:00 ET through Nov 9 ET ($3 before and on Nov 10), $30 total, all from `demo-reserve`; live mode still ends Nov 10 ET. Implemented as `AppConfig.judging_daily_cap_usd` + `daily_cap_at(now)` (etho/app/config.py, guards.py; test `test_daily_cap_is_raised_only_in_the_judging_window`). The form has no notes field, so the access code goes at the end of the #4 full description; on Nov 3 ask in Discord whether submission pages are public / have a private notes field; rotate the code after Nov 9. Gateway tag budget `demo-reserve` ($35, Meta spend only) is unchanged; $30 of app spend is about $20 of it.
- 2026-10-28: (lead) ADR-005 bundle review (Claude reviewer; Codex refused by policy: app-only diff): merge after fixes 1-4 (trace "rules alone" wording, contact-promotion "decided by", raw error text reaching visitors + operator logging, cost-label overstatement); 5-9 also being fixed. Expert row on a showcase: dev-split sequences by default; a test sequence only with `--allow-test-split`, used solely for the demo clip mouse075 (Aniket approved showing the real CalMS21 annotation timeline; CalMS21 test is final per A3, no test-2, so displaying its labels cannot influence any reported number). The label export itself is done by the evaluator.
- 2026-10-28 23:09-23:12 UTC: (lead, ADR-005 approved spend) Showcase run of mouse075 0-300 s (H.264 re-encode, crf 23, 71 MB) on a local non-public instance, tag `dev`, Standard mode (upload path), job ceiling pinned to $1.50: 129.5 s wall (0.43x real time), $0.89 (planner $0.03, SAM $0.60, tier-1 $0.25, report $0.01). Rules came from the benchmark seed detectors (definitions match the dataset authors' wording), so the rules stage took 0.7 s. One run, not re-rolled: the scripted traced error recurs (final mount 37.5-44.0 s; tier 1 said attack at 37.5-38.0 s and 41.4-44.0 s, set aside by the restriction; expert attack 34.3-42.1 s). Packaged as `sc-calms21-mouse075-live` with the evaluator's display-only expert row (`--allow-test-split`). Sample clip: dev mouse008 270-330 s (attack + investigation), in data/showcase/_sample because the public unit only binds data/showcase. The 300 s demo clip exceeds the public limits (120 s, 50 MB), so the 0:35 live-run beat is recorded on a local instance of the same code.
- 2026-10-28: ADR-003 review fixes and hosting additions (same branch). (1) A wrong access code is throttled and delayed, the right one is never refused (a 24-char code has ~140 bits; a lockout only let a guesser deny real visitors); (2) an unreadable ledger row closes the caps (counted as $1e6) instead of being skipped, and the offset moves only after a chunk is processed; (3) the public instance counts only rows with caller prefix `pub:job:`, so the lead's localhost recording on `demo-reserve` does not use the public $3/$20; (4) public per-job ceiling is max(1.5x estimate, estimate + $0.25) so two 120 s jobs fit under $3/day (measured cost is 0.4-0.6 of the estimate); (5) the app has its own chunk dir in both modes and retention deletes only chunk dirs cut from a project's own video; (6) showcase revisions on a public deployment use Standard models; (7) ffprobe/ffmpeg on uploads run under bwrap; (8) /api/health shows visitors only the mode and the budget; (9) a systemd health watchdog and an external uptime check (public DNS, so the check does not stop at the tailnet) were added; the optional stop timer is Nov 11 00:05 ET.
- 2026-10-28: The ledger and public visitors. Once the public app is live (Nov 3+), its rows carry `pub:job:<id>`, and the job id is a capability link to that visitor's project for 24 h. So `ops/spend.jsonl` is not committed with any `pub:job:` row in it. From go-live, ledger commits use a redacted copy (pub rows replaced by daily totals), or the ledger is untracked before the repo goes public. The pre-submission audit checks this.
- 2026-10-28: ADR-002 fly path, pre-gate review fixes (same branch; no gate run, no tracking, no model call).
  - **Frozen-run guards** (`etho/flies/frozen.py`, `run_fly_etho.py predict --frozen-config`): the detectors file must hash to the frozen `assets.detectors` entry, `--override` is refused, every frozen asset is re-hashed, any dirty path in etho/ (outside `etho/app` and `etho/gateway`) or configs/ is refused, and window, fps and behaviors come from the branch in `eval/flies_decision.json` (the GO branch, mid5 at 30 fps, while no record exists, which is the dev lunge gate), never from the CLI. The manifest records per-movie `features_sha256`, `tracking_sha256` and their paths (`config.inputs`, the shape amendment F3 wants), `window`, `detectors_sha256`, the decision-record hash and the frozen-asset check. `run_dirty_paths` now lists only dirty paths inside the frozen trees, before any truncation; other dirty paths are counted.
  - **Dev features rederived (M1).** `run_fly.py rederive` from the cached raw RLE with the frozen tracking code gave arrays identical to `data/runs/F1/movie{1,2}/features.npz` except `gap`, which differs by at most 7.6e-6 px (float32 round trip). I replaced the two dev tables with the rederived ones (old ones kept as `features.pre-freeze.npz`; `tracking.json` untouched). The final detectors on them give byte-identical prediction masks and identical dev metrics (run R44), so nothing was retuned. The baselines in `eval/flies_baselines_v1.json` record the old features hashes; the scorer checks only the baseline parameter file's hash, and I did not touch `eval/`.
  - **NO-GO branch (10 fps) is less validated than GO (M2).** A synthetic stride-3 table through `finalize_fly` at 10 fps and `predict_movie` (mid10) is accepted by the scorer's loader (test), but no real 10 fps tracking exists, and some model-written wing detectors use sample counts that do not scale with fps (they floor at 3 samples, which is 0.3 s at 10 fps). Detector code is frozen and was not edited. Windows in seconds at 30 fps -> 10 fps: cand 0 smoothing `max(3, round(0.15 fps))` 0.17 -> 0.30 s; cand 1 `k_shape` `max(3, round(0.1 fps))` 0.10 -> 0.30 s and `k_spd` `max(3, round(0.15 fps))` 0.17 -> 0.30 s; cand 2 `k` (min 3) 0.10 -> 0.30 s; cand 3 `max(3, round(0.15 fps))` 0.17 -> 0.30 s; cand 4 `k_fast` `max(3, round(0.1 fps))` 0.10 -> 0.30 s (its 0.3 s look-back scales correctly); cand 5 `max(1, round(0.1 fps))` stays 0.10 s. All `bouts_clean` limits, the 2 s minimum run and the vote are in seconds. Smoothing that is 1.8-3x longer in seconds can blur short wing onsets; with a 2 s minimum run the effect on long bouts should be small, but it is untested on real 10 fps data (a subsample of the 30 fps dev features gave wing frame F1 0.885 vs 0.911).
  - **EXTRA_DOC NaN text corrected (M3), computation unchanged.** The old text said `*_raw` was NaN only where a fly is missing; actually one missing fly leaves the present fly's speed (so `min_speed_raw` equals `max_speed_raw` then), 1-2 sample gaps are bridged, and toward/closing speeds are NaN while either fly is missing. The detectors were synthesized with the old wording; their recorded `prompt_hash` values refer to it, so re-running `synth` with the current code would not reproduce those hashes.
- 2026-10-28 20:50 UTC: Fly wing-threat train gate is NO-GO (pooled F1 0.584, CI lower bound 0.009 < r 0.043; run flies-train-20261028T205010Z-fdba57). Under the frozen protocol the flies are a qualitative demo clip: no fly F1 claim, no fly test. Movies 6-10 stay untracked and their labels unopened. Fly spend stops at $9.33 of the approved $19.5. The demo uses the NO-GO line; the README reports train-movie numbers and the baselines, labelled "not a test result".
- 2026-10-29 05:10 UTC: Overnight Codex routine review (ops/codex/review-20261029-040001-958114.md) found three P2 issues, all confirmed still present on main: fly slow-velocity exclusion margin too short, retention purge deleting cache entries shared with live jobs, revision reports claiming "no labeled examples". Fixes in progress on a branch (implementer, tests per fix); retention and report fixes must be live on the hosted app before the Nov 3 go-live (Aniket). Script line "Nothing is a black box" -> "No label goes unexplained."; 0.7 s seeded rules step shown on camera; X tag fallback `@AIatMeta` (all Aniket, 2026-10-29).
- 2026-10-29 06:20 UTC: Merged the overnight Codex review fixes (d0a9e83) after two Claude reviewer rounds; 700 tests pass. Retention now handles each cache key under its gateway lock and rechecks liveness inside it, keeps keys other projects or running jobs use, and no longer unlinks lock files (the idle sweep does). Fly slow-velocity features are NaN over the slow smoother's full support near shape jumps; eval/flies_decision.json predates this and is not re-run (frozen NO-GO; the fly clip is qualitative). Codex second opinion queued (20261029T050548-review-fd07b4). Needs `systemctl restart etho-app` to go live.
- 2026-10-30 04:40 UTC: **Showcase representativeness rule, stated before any numbers are computed** (Aniket asked for numbers, no decision yet). Metric: frame-level F1 per behavior and macro F1 over attack / investigation / mount, computed exactly as in eval/results/test1_report.md. (1) The mouse075 showcase is "far below average" if its macro F1 over 0-300 s is below the 25th percentile of the 19 per-video test-1 macro F1s (test1_report §12). (2) If so, the proposed replacement is the test video with at least one expert attack bout whose test-1 macro F1 is nearest the median of the 19 (ties: more expert attack frames). Its window is the 300 s window starting on a whole minute with the most expert attack frames (ties: earliest). Aniket decides; nothing in the pipeline or post-processing changes either way.
- 2026-10-30 04:55 UTC: Correction: the representativeness-rule entry above says "04:40 UTC" but was committed at 04:18:00 UTC (845e6d9), before the evaluator's first label read (04:22:10 UTC). Rule text unchanged. Result (eval/results/showcase_mouse075_check.md): showcase macro 0.4193 < 25th percentile 0.4650, so "far below average"; the rule's replacement is mouse077 0-300 s (macro 0.5061 = the median), which has no expert mount. Escalated; no change made.
- 2026-10-30 18:45 UTC: Showcase option A (Aniket): keep mouse075. Script v5 adds "This is one of the harder clips: 0.42 here, against 0.58 across all 19 test videos. Mount is where it struggles most." with trims elsewhere (394 words, ends 2:54; docs/DEMO_PLAN.md, docs/demo_captions_v5.srt). The showcase page description (data/showcase/calms21-mouse075-live/result.json, gitignored) now ends: "This is one of the harder clips: macro F1 0.42 on this run (attack 0.58, investigation 0.68, mount 0.00), against 0.58 across all 19 test videos in the separate scored test run. Mount is where it struggles most on this clip." Numbers from eval/results/showcase_mouse075_check.md, rounded to 2 decimals. No pipeline change. App restarted 18:44 UTC.

## ADR-006 (2026-10-30): "Ask Etho why" (Aniket's spec). C (skip): lead decision, APPROVED by Aniket 2026-10-30.
**Context.** Aniket's spec: on a bout, the researcher asks "why?". Muse Spark (`muse-spark-1.3`, Standard mode) answers from the run's audit trail (rule text, frame-check verdicts, timestamps). It must cite trace entries and refuse anything the trace doesn't support. In the video it replaces narration inside the results beat (1:21-1:50, v5) at no extra time. **Aniket's gate:** build only if the honest estimate is ≤ 6 h including tests, merged by Nov 1 ET. Freeze is Nov 2, rough cut Nov 2, recording Nov 3. Video and README come first.
**Found in the code (read this session; nothing executed).**
- *Trace data.* `GET /api/projects/{pid}/trace?v&behavior&s0&s1` (`server.py:559-579`) returns `PG.bout_trace` (`progress.py:187-220`):
  - `behavior`, `s0`/`s1` (samples) and `start_s`/`end_s`;
  - `rule_result` {flagged, rule bouts overlapping, exact};
  - `tier1`: every verdict whose window touches the bout, as built by `read_verdicts` (`progress.py:157-175`): `s0, s1, rule_label, source, label, confidence, reason` (tier-1's own free text), `decision` (set_aside / agreed / restriction / applied), `decision_text` (a fixed sentence from `decide()`, `progress.py:138-154`) and `strip`;
  - `decided_by` (rules / tier1) and `summary`.
  
  **No entry has an id.** A stable key can be derived: (version, `rule`/`t1`, s0, s1). The verdicts file is per version and is not rewritten, and the window is unique per verdict there (assumed, not checked). Rule text is not in the trace. It is in `res["behaviors"]["definitions"]` and `codes` (`backend.py:465`), or in the revision record for v > 0.
- *The trace already answers "why" deterministically.* `decision_text` and `summary` are plain sentences written by code. For the scripted mount bout, the panel already says the tier-1 "attack" was set aside by the restriction. The model would rephrase and connect these sentences. It would not add evidence.
- *Gateway tool calling.* `Gateway.chat` (`gateway.py:245-264`) passes `tools`/`tool_choice` through `**extra` and refuses only `tool_choice` without `tools`. It has no first-class support:
  - `_extract_text` (`gateway.py:564-573`) returns only `message.content`, and `tool_calls` survive only in `LLMResult.raw`;
  - there is no loop helper;
  - nothing in the repo has ever sent `tools` to Meta (grep: only a cost-estimate test, `test_gateway_review_fixes.py:21`);
  - `FakeGateway.chat` (`tests/app_helpers.py:105-111`) returns no `raw`.
  
  Whether `muse-spark-1.3` returns OpenAI-style `tool_calls` with `reasoning_effort` set is **unverified**.
- *Spend gates.* The middleware refuses every non-GET, non-`SAFE_POST` request before reading the body, through `spend_refusal`: read-only, kill switch, live window, then the access cookie (`server.py:173-181,196-202`). So a new `POST .../ask` is closed by default in read-only/showcase browsing and without the code.
  - Revise adds `limiter.check`, `guard.check(est, outstanding)` and a Job whose ceiling `JobManager.submit` reserves (`server.py:607-664`).
  - The caps count only ledger rows whose caller starts with `cfg.caller_prefix` (`pub:job:` / `job:`; `config.py:68-71,145-146`; `jobgw.py:43-50`). **So an Ask call must go through `JobGateway` with a synthetic job id** (e.g. `{pid}-ask{n}`). A bare `Gateway` call would not count toward the $3 / $8 / $30 caps.
  - `SpendGuard.reserve` sizes the ceiling as `max(mult × est, est + extra)` (`guards.py:115-126`). Per ADR-003's log line that is about $0.25 over a 1-cent call, so Ask needs its own small ceiling.
- *Retention and privacy.* The gateway caches the request body, which includes the visitor's question. Whether retention (keyed by job ids) purges the cache keys of a synthetic ask id is **unverified**, so it must be checked in review. Otherwise ask uses `use_cache=False` on the public instance.
- *Tests.* `FakeGateway` plus `patch_pipeline` (`tests/app_helpers.py`) mock every model call. An Ask test monkeypatches the chat text and asserts the ledger row prefix, the tag, the effort and the gate refusals.

**How "refuses unsupported claims" can be enforced in code (needed by A and B; the prompt alone is not enough).**
1. The server builds numbered evidence entries:
   - E1, the bout and `summary`;
   - E2, the rule result in seconds;
   - E3, the definition;
   - E4, the rule code;
   - T1..Tn, one per touching verdict with all its fields, in seconds and samples.
   
   It maps the short ids back to the stable keys in the response, so the UI highlights the cited trace rows.
2. The answer is plain text, at most 5 sentences and about 600 characters. There is no JSON mode, so it is parsed defensively. Every sentence must end with one or more `[E#]`/`[T#]` ids that exist in this bout's entries.
3. Every number in a sentence must appear, after normalization, in the rendered text of an entry that sentence cites. Spelled-out numbers are mapped to digits first. The same check applies to every quoted label, which must be one of the behavior names, "none" or "unsure".
4. If any check fails, or the model outputs the agreed `UNSUPPORTED` token, the server returns a fixed refusal: "The trace for this bout doesn't support an answer to that question." The model's text is not shown.
5. **Limit (must be stated honestly):** this checks the provenance of citations, numbers and labels, not semantic entailment. "Set aside because the mice were far apart [T2]" passes if T2 exists and has no numbers. The demo and README may say "every sentence cites the trace, and numbers must match it". They may not say "it can't make things up".

**Prompt injection.** The question, the definitions (written by the user on uploads), the tier-1 `reason` text and the model-written rule code are all untrusted.
- They go in delimited data blocks, marked as data.
- There are no tools with side effects, no secrets in context and no persistence into the shared showcase. The answer goes only to the asker.
- The question is capped at 300 characters. Ask has its own rate limit.
- The output is rendered with `textContent` under the existing CSP.
- The validator bounds the length, the citations and the numbers.

Residual risk: off-topic text that still carries valid citations, shown only to the person who asked. Low, given the access code.
**Cost per question (estimate, unmeasured), Standard `muse-spark-1.3` at $1.25 in / $4.25 out.**
- Input is about 3-6k tokens: system prompt about 0.8k, definition plus rule code 1-2.5k, 2-8 verdicts at about 0.15k each, question ≤ 0.1k. That is $0.004-0.0075.
- Output at effort `low` is a 150-300 token answer plus about 300-1,500 reasoning tokens (unmeasured), $0.002-0.008. Total **about $0.006-0.016 per question**; `medium` is perhaps $0.01-0.025.
- **Effort: `low`** (same policy as the report stage, "writing from computed numbers"). Retry at `medium` only if smoke answers fail the validator for reasoning reasons.
- Ask gets its own ceiling (estimate + $0.02), so it never pins $0.25 of headroom.
- Smoke from `dev`: ≤ 3 calls, ≤ $0.05. The current `dev` balance was not checked this session (no shell), so the lead checks `bin/spend` first.

**Options.**
- **(A) Full spec with a tool loop.** Tools `get_bout_trace`, `get_rule(behavior)` and `get_window(id)`, at 2-3 model calls per question.
  - *Time:* about **9-11 h**. That is B (below) plus a live probe of Meta's tool-call format (unverified; 2-4 extra dev calls, about $0.05), the loop with turn and ceiling limits, `tool_calls` extraction (from `raw` in the app, or a gateway change), a richer FakeGateway, and loop tests. A gateway change makes it a gateway merge, which needs a routine Codex review: at most 1 per day, none from Nov 1.
  - *Cost:* 2-3x B per question.
  - *Latency:* 2-3 sequential calls, unmeasured. The latency has to be cut out of the video.
  - *Capability:* none gained. A bout's whole trace fits in one prompt, so the tools fetch what B already supplies, and the validator is still required.
  - *Risk:* moderate. Unknown tool-call behavior on Meta, a new spend route, and the trace panel is in the video.
- **(B) Reduced: the bout's evidence in context, one call, server-side validator, fixed refusal.**
  - *Time:* about **6-6.5 h central, range 5-8 h**:
    - evidence builder, prompt, parser and validator: 1.5 h;
    - route with the existing gates, its own limiter, `JobGateway` with a synthetic id and a small ceiling, and the cache/retention decision: 1.25 h;
    - UI (ask box in the trace panel, citation chips that highlight rows, disabled states with the same reasons as Revise): 1.0 h;
    - tests (about 12 validator cases including injection-shaped outputs; about 8 route cases for read-only 403, no-code 401, killed, cap 429, the ledger prefix counted by `AppLedger`, the ceiling, a bad question and the refusal path): 1.25 h;
    - Claude reviewer and fixes: 0.75-1 h (app-only, so no Codex review, as in ADR-005);
    - real smoke and prompt tweak: 0.25-0.5 h.
    
    The UI is the most likely place to overrun. The estimate is not calibrated: ADR-005's own estimate was never compared with the actual time.
  - *Video rework, on top of the agent hours:* about 0.5-1 h of lead and Aniket time (below).
  - *Risk:* low to moderate. The POST is closed by default and nothing in scoring, eval, the sandbox or the gateway changes. But it edits the trace panel, which the video and the judges use, 1-2 days before the freeze.
- **(C) Skip.** One line in the README "What's next" ("ask why about a bout, answered only from its trace, with citations"), with no claim that it exists.
  - *Time:* 0.25 h. *Cost:* $0. *Risk:* none.

**Video fit.** The 1:21-1:50 beat is already full: 71 words is 29.4 s of speech in 29 s. To show Ask, someone types the question (cut as "time skipped"), then the answer is held on screen long enough to read at ≥ 48 px, about 5-6 s for 2-3 sentences. That needs about 13-15 words cut. The natural cut is "The frame check answered 'attack', but a rule in the cascade set it aside, and the trace shows exactly which step", so the on-screen answer says it instead. A possible line: "At thirty-eight seconds, Etho says mount; the expert says attack. I ask Etho why. Every sentence cites the trace; when the trace can't support an answer, it refuses. No label goes unexplained." The wording, and the claim that goes with it, are Aniket's call. The captions (`demo_captions_v5.srt`) and the Nov 3 order of operations (DEMO_PLAN) need updating.
- *Integrity:* record the first answer that passes the validator, and never re-ask to get a nicer one, as with "never re-roll runs". The cache makes the same question reproducible.
- *If the Nov 3 live run shows a different error:* the question changes with it.
- *Recording:* the video is recorded on a local instance (ADR-005 log), so Ask spends there from `dev` or, from Nov 3, `demo-reserve`.

**Recommendation: (C) skip, and list it in "What's next".**
- *The gate is not clearly met.* The honest central estimate for B is 6-6.5 h, over Aniket's 6 h line, with the risk weighted toward overrun. A is 9-11 h.
- *It adds little evidence.* The trace panel already answers "why" with code-written sentences (`decide()` / `bout_trace`). An LLM rephrasing them adds a surface for unsupported claims, which the validator can limit but not remove.
- *It competes with higher priorities.* It needs a rewrite of the video's most honest beat and new captions in the same 48 h as the rough cut and the README.
- *If the lead judges that B fits in 6 h anyway:* build B only, nothing else from A. Merge by Nov 1 ET or drop it. Keep the README and video claim to "every sentence cites the trace, and numbers must match it".

**Evidence.** File:line references were read this session. Costs, latency, tool-call behavior on Meta, retention coverage and the `dev` balance are **unverified**. No code was run and no model was called.
**Risks.**
- *Rules:* none. The feature is built during the event and Meta's model is central. Standard mode is used for user data. No OpenAI or Claude output enters the product.
- *Budget:* B is about 1-2 cents per question, inside the existing caps, provided it goes through `JobGateway` (otherwise it bypasses the app caps: a must-fix in review).
- *Integrity:* no scoring, eval or label path is touched. The video answer is not re-rolled.
**Reverse.**
- (B): remove the route, `ask.py` and the UI block. No data migration; answers are not persisted.
- (C): build later. Nothing to undo.
**Codex view** (`ops/codex/ask-20261030-185303-1264827.md`, gpt-6.1-sol xhigh, routine; given the ADR's options and evidence and the 6 h gate, asked which option and what the ADR gets wrong): **chooses C.** B's itemized 6-6.5 h leaves no margin and is not credibly within the gate; the code-written trace already explains the scripted error. Corrections it raised: (1) citation and number checks prove provenance, not that a sentence follows from its source, so narration like "when the trace can't support an answer, it refuses" would overclaim; (2) `behaviors.codes` are label ids, not rule code; detector source is in each version's `detectors.json`; (3) `JobGateway` attribution alone does not join the app's shared reservation lifecycle, so Ask would need real integration with SpendGuard and the job manager; (4) the gateway caches responses, not request bodies, and the audit file records prompts even with `use_cache=False`. Strongest argument against C it gave: a conversational explanation could make the audit trail more accessible on screen; its value is undemonstrated.

**Lead decision (2026-10-30 19:00 UTC): C, skip.** The honest estimate (6-6.5 h central, 5-8 h range) does not clear the 6 h gate, Codex's corrections (3) and (4) add backend work the estimate did not cover, and it would change the trace panel the video records 1-2 days before freeze. "Ask Etho why" goes to the README "What's next" with no claims. Reversible: build B after the submission. **Awaiting Aniket's approval.**
**Approval needed from Aniket:** (1) skip (C), or B under the 6 h / Nov 1 ET rule? (2) If B: the replacement wording for the results beat and the exact claim ("cites the trace", not "can't make things up").
- 2026-10-30 20:29 UTC (commit 095af8d): **Fly-beat window rule (Aniket, 2026-10-30), committed before any window or label is looked at:** "the earliest 15 s window in Fly vs. Fly movies 2-5 with the most expert-labeled lunge or wing-threat bouts; ties go to the earlier movie". The rule uses expert labels only, never Etho's predictions. Terms, fixed now (lead):
  - **Search space:** the span SAM 3.1 tracked for the frozen run in each movie (source frames 22500-31500 at 30 fps), because masks exist only there. No new tracking or model calls.
  - **Windows:** 15 s windows starting on whole seconds of that span (offsets 0-285 s).
  - **Score:** the number of expert-labeled lunge or wing-threat bouts whose onset falls inside the window, both flies counted.
  - **Ties:** the earlier movie, then the earlier start.
  - **What is shown:** the clip shows that window at real speed with Etho's frozen-arm predictions for it, whatever they are, still labeled "Qualitative preview: not scored", with no accuracy number (ADR-004).
  - **Timeline:** the fly beat grows from 13 s to 15 s (2:26-2:41), and the close moves to 2:41-2:56, still under 3:00.
- 2026-10-30 20:29 UTC: **Aniket's decisions:**
  - ADR-006 skip APPROVED ("What's next", no claims).
  - Live run on Nov 2 on a local instance, up to $0.90 from `dev`, APPROVED. The same day: score it with eval/showcase_check.py and update the voice-over line and captions, so that Nov 3 is the voice-over and final assembly.
- 2026-11-02 20:35 UTC: Live run for the video (Aniket): ceiling raised to $1.20 from `dev`, run now, recorded headless. Local instance :8770 with a fresh data dir and gateway cache (data/app_nov2), tag dev, ETHO_APP_CEILING_MULT=0.5911 / EXTRA=-0.83, so the job ceiling is $1.20 on the $2.03 estimate. Exactly one job; no re-roll. Rough-cut notes: if none arrive by 6 PM ET, v1 counts as approved apart from the live-run update. Nov 3 plan approved as written.
- 2026-11-02 23:28 UTC (18:28 ET): The 6 PM ET defaults applied, with no message from Aniket: rough cut v1 counts as approved apart from the live-run update (now in, 3c9306b). The hosted showcase stays the packaged run (0.42, its own note), and the video shows the Nov 2 live run (0.44). Feature freeze holds from tonight.
- 2026-11-02 23:45 UTC: Hosted showcase switched to the Nov 2 live run (Aniket): `scripts/package_job_showcase.py --job data/app_nov2/jobs/j46d77dd8aee9 --name calms21-mouse075-live --calms21 --expert-labels ... --allow-test-split`. The URL is unchanged (/p/sc-calms21-mouse075-live). The note reads "macro F1 0.44 on this run (attack 0.57, investigation 0.74, mount 0.00) ... Here, mount is where it struggles most." Restarted, and verified through the public Funnel IP: showcase, overlay, cost $0.895 and the 37.5-38.0 s trace (tier 1 attack 0.95). The Oct 28 package is archived at data/showcase_archive/calms21-mouse075-live-oct28. `eval/results/showcase_mouse075_check.json` refers to that archived run; the Nov 2 run's score is `eval/results/live_run_nov2_check.json`. Final video cut: built by the lead from Aniket's per-beat voice-over takes (not the video app).
- 2026-11-04 03:30 UTC (Nov 3, 22:30 ET): Aniket's plan after the organizers' answer (no private field; credential-free judge path wanted; edits allowed until Nov 4 21:00 ET):
  - (1) **Offline replay demo** (`make demo`), priority 1. It is a separate component used only by the demo; the live gateway's code path is unchanged and the existing gateway tests stay unchanged. Semantic keys, a hard error on any miss, no network, clearly labelled. Merge only after the Claude reviewer passes, by about 05:00 ET; otherwise it stays unmerged.
  - (2) **Open live uploads on the hosted app**, priority 2: no code, 10 runs per visitor per day, $20/day Nov 4-9 ($8 before), $60 total, a rights/no-people checkbox enforced on the server, and a cap message pointing to the showcase and make demo. Branch only tonight. Deploy on Nov 4 afternoon after Aniket's app.env edit and the public-URL drills; if they don't pass by 17:00 ET the site stays read-only. Budget move approved: demo-reserve to about $80 (eval-llm $29.93 unused + eval-sam $15.42 left); gateway hard caps unchanged.
  - (3) **First-frames check skipped**; the drills confirm that a no-animal clip stops early.
  - (4) **Pre-submission audit** 05:00-08:00 ET on main.
  - (5) FORM_FINAL in two versions.
  - Hard stop on all building: Nov 4, 21:00 ET.
  - Recording run for the demo: job j4eded39f5aae (data/app_rec), 60 s sample clip, $0.29 dev, 109 s wall time.
  - Visitor identity through Funnel was checked in the hosted access log: real client IPs appear (X-Forwarded-For is honoured), so per-visitor limits are per visitor.
- 2026-11-04 04:00 UTC (Nov 3, 23:00 ET): **Open live uploads, built on branch `open-uploads`, not deployed** (Aniket's spec). `ETHO_APP_OPEN=1` removes the access code and login for uploads and revisions on a public app; without it nothing changes (a public app with no code file stays read-only, existing tests unchanged). Server-enforced consent field (HTTP 400 `consent_required`, only the fact is logged), per-visitor limit 10 runs per rolling 24 h per client IP (`ETHO_APP_RATE_JOBS`, new `ETHO_APP_RATE_WINDOW_S`; a revision counts as a run), at most 2 jobs queued or running (new `ETHO_APP_MAX_IN_FLIGHT`; the manager keeps ONE worker: the tier-1 semaphore, the SAM fan-out, the Files API cleanup thread and the 12 GB RAM all assume one job at a time, and the reservation logic is already safe for any number of jobs but two workers would double SAM/Fireworks concurrency and buy about 100 s of waiting for the second judge), friendly messages on every limit that point to the showcase and `make demo` (README), and say when it resets. Caps for `app.env`: $8 per rolling 24 h, $20 in the Nov 4-9 ET window, $60 total. **Budget move (Meta tag budgets, `etho/gateway/prices.py`, Meta total still 140, no provider hard cap changed):** `eval-llm` 30 to 1 (spent $0.07), `eval-sam` 40 to 25 (spent $24.58), `demo-reserve` 35 to 79; `dev` 15 and `ablation` 20 unchanged. Sum 15+25+1+20+79 = 140. Deploy steps and the drills: `docs/DEPLOY_OPEN_UPLOADS.md`. Reverse: delete `ETHO_APP_OPEN`, restart; the budgets revert by editing the five numbers back.
- 2026-11-04 05:15 UTC: Reviewer fixes on `open-uploads` (open mode only; everything off without `ETHO_APP_OPEN`): at most 1 upload in progress and 1 queued-or-running job per client key (middleware and `submit`, revisions included; new `ETHO_APP_MAX_UPLOADS_PER_CLIENT`, `ETHO_APP_MAX_JOBS_PER_CLIENT`), a 120 s deadline for the whole upload body (`ETHO_APP_UPLOAD_DEADLINE_S`, `etho/app/deadline.py`), 10 rejected uploads per client per hour (`ETHO_APP_MAX_REJECTS_PER_HOUR`), cap messages reworded so they are true before and after the replay demo lands ("an offline replay demo (make demo) is described in the project README"), README updated for open mode. Consequence: one visitor can no longer show the queue alone, so drill 2b uses a phone and drill 7 runs on a throwaway instance with `ETHO_APP_RATE_JOBS=2` and no key file (cost $0, run once against the branch). **Budget note: `eval-sam` has $24.58 spent of 25, about $0.42 of headroom, so no further SAM evaluation run is possible without a new budget move.** The no-animal planner gate is not built (Aniket has not decided).
- Offline replay demo (`make demo`, Nov 4): a separate package `etho/replay/` (the live gateway is untouched) injects a `ReplayGateway` through `create_app(gateway_factory=...)`, the seam the tests use for FakeGateway. Calls are keyed semantically (method + provider + model + caller module + the request with image/video bytes replaced by placeholders, keeping text, image count, SAM phrase and parameters; an occurrence counter for identical keys; uploads by purpose + duration + frame count), because ffmpeg/OpenCV bytes differ per machine. The one volatile prompt field, the report's stage timings, is masked in the key; costs are not. A miss raises `ReplayMiss` (key, caller, nearest recorded keys) and poisons the gateway: the app's tier-1 stage would otherwise catch it and carry on with rule labels, so the demo also fails the whole run when any miss was recorded. `lockdown()` removes keys and blocks `Gateway`, the real HTTP transport and non-loopback sockets in the demo process. The index (56 calls, 344 KB) was built from the existing run j4eded39f5aae by re-running the job through a recorder over a cache-only `Gateway` that refuses misses before the network: $0 spent, no paid ledger row. Reverse: delete `etho/replay/`, `demo/`, and the Makefile/README entries.
- Replay review fixes (Nov 4): the replay banner now has a second sentence saying which numbers are the recorded live run's (cost, methods-paragraph timings, validate estimate) and that the replay spent $0, with the figures read from the index header; the speed and cost tiles are qualified by a same-origin script served from the replay wrapper; the recording is tied to the clip by SHA-256; exit code 4 when the labels differ; lockdown accepts only literal loopback IPs or `localhost`. One edit outside `etho/replay/`: in the rule sandbox's runner (`etho/rules/sandbox.py`) only the `RLIMIT_AS` `setrlimit` is wrapped in `try/except (ValueError, OSError)` so the rlimit-only mode survives platforms that cannot set it (macOS); every other limit is still set and Linux behaviour is unchanged. Reverse: remove the try/except.
- 2026-11-04 05:50 UTC: Codex gate findings on `open-uploads` fixed, and the no-animal gate built (Aniket approved it). (1) A rejected showcase revision no longer forks the showcase: `JobManager.check_capacity` runs before the labels are loaded and the copy is made (submit still decides atomically). (2) The upload body deadline is a hard stop (no read after it, even if data is ready). (3) The local drills (cap hit, per-visitor limit) start with every provider key variable unset (`env -u META_MODEL_API_KEY -u BASETEN_API_KEY -u FIREWORKS_API_KEY -u TOGETHER_API_KEY`) and a nonexistent `ETHO_SECRETS`, and never touch the public caps or budget; run once with sentinel keys exported. (4) Drill 2b uses the 2 s clip; totals corrected. **No-animal gate (app job backend only; `etho/app/backend.py`, not the planner, eval or scoring):** after the planner, `found_no_animals(plan.steps)` declines the job when the proposed expected count is 0, or when no phrase found an object in the SAM image check on any probe frame; the job ends with the new terminal status `declined` (error code `no_animals`, not an error), before tracking, for the planner's cost only (measured $0.032 and $0.034 on the sample clip). When the planner's record cannot be read, the job goes on. `make demo` still serves 56 of 56 recorded calls and matches the recorded run (the gate adds no call).
- Nov 4 01:05 ET: open uploads went live after all drills passed (drill spend $0.51); see STATE. Drill 1 is $0.46 not $0.29 for a fresh upload because rules are synthesized for it.
