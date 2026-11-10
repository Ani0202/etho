# Phase 1 feasibility spike (2026-10-28)

All numbers below come from calls logged in `ops/spend.jsonl` and artifacts under `data/spike/`
(gitignored: raw data stays local). Clips come from the CalMS21 **train** split only.

## Setup

| Clip | Source | Frames used | Ground truth (CalMS21 labels) |
|---|---|---|---|
| A "contact" | train/mouse016, src frames 2400-2999 | 200 (10 fps, every 3rd frame) | 0-2.7 s mount, 2.7-14.5 s investigation, then other/investigation; mice in close contact most of the clip |
| B "attack" | train/mouse050, src frames 0-599 | 200 (10 fps) | 100% attack (599/600 frames) |

Video: 1024x570, grayscale, 30 fps; the black resident has a head-mounted implant with a cable.
Clips were re-encoded with H.264 (crf 18, faststart). Identity is scored against the CalMS21 pose
keypoints (MARS pose estimator, not human labels): for each SAM object and frame, which mouse's
keypoint centroid is nearer.

## What worked (with evidence)

1. **Gateway and all three providers.** Smoke calls succeeded on Meta (`muse-spark-1.3`), Baseten
   (`meta-models/muse-spark-open-1026`), and Fireworks (`muse-spark-open`, text and image). Repeat
   calls hit the disk cache at $0. Fireworks Glimmer returned 404 `NOT_FOUND`, as expected after the
   serverless retirement; it was called once and not retried.
2. **SAM 3.1 with the single phrase `"mouse"` tracks both animals with stable IDs.**

   | Clip | SAM object | Matches CalMS21 mouse | Frames present / 200 | Identity agrees with keypoints | Mean mask intensity |
   |---|---|---|---|---|---|
   | A | 0 | resident (black) | 200 | 200/200 | 51 |
   | A | 1 | intruder (white) | 160 | 160/160 | 143 |
   | B | 0 | resident (black) | 196 | 196/196 | 52 |
   | B | 1 | intruder (white) | 190 | 186/190 (2 disagreements while the mice were >60 px apart) | 143 |

   I also inspected the 8 closest-contact frames of each clip
   (`data/spike/out/*__mouse__closest.jpg`). The IDs stay on the right animals during contact,
   including the attack's tumbling. Mask intensity separates black from white by a wide margin
   (about 51 vs 143 on a 0-255 scale), so "which ID is the resident" needs no second SAM call.

   **How strong this is:** the metric gives each track its majority nearest-reference mouse and
   counts disagreeing frames. It is not a temporal swap-event count, and it doesn't enforce
   one-to-one matching. "0 disagreements in A, 4 in B" is therefore evidence of stable identity,
   not proof of zero swaps.

   **Coverage, the bigger problem:** both mice are detected in only 160/200 (A) and 190/200 (B)
   frames, 87.5% combined. In clip A, the intruder is missing in clip frames 0-39, which include
   **all 27 mount frames**: it is hidden under the resident. The mount is seen only as a single
   resident blob.

   **But every gap is an initialization gap.** No track has an internal gap: the clip A intruder
   is present from frame 40 to 199, the clip B resident from 4 onward, and the intruder from 10
   onward. All missing frames come *before* each track's first detection (pointed out by Codex,
   verified from the cached streams). The mount sat at the very start of clip A. So we have not
   tested whether an **established** track survives a mount; the spike shows only that SAM can't
   *start* a track on a hidden animal. Next test: the same mount with a few seconds of preceding
   footage, plus a box-prompt re-seed with backward propagation (about $0.10 total).
3. **Baseten Muse Spark Open accepts video** as a base64 `video_url` data URL (8.6 MB mp4) on Chat
   Completions.
   - Clip B (attack): it called every bout "attack, initiated by the black resident". That is
     correct, but with roughly ±1 s timing.
   - Clip A (contact): it called the whole clip "investigation/other, close proximity". It
     **missed the 2.7 s mount** at the start.
   - Each 20 s clip cost only 3,290 input tokens, and the model itself described its input as
     "low-resolution thumbnails". It's a reasonable tier-2 judge of short, cropped clips, but **not
     a frame-level scorer**, which matches the planned architecture.
4. **SAM image calls** work: one 1024x570 frame costs $0.0025, takes about 1 s, and returns
   `usage.images_processed`. On the same frame, `"mouse"` returns both mice.

## Failure modes found

1. **Color-qualified phrases fail on this grayscale video.**
   - `"black mouse"` / `"white mouse"` returned **nothing** for all 200 frames of clip B.
   - On clip A, they returned output only for frames 154-199, although frames 153 and 154 look
     almost identical.
   - This is not a client bug. The stream diagnostics show 46 deltas, and the streamed text equals
     the `response.completed` text (171,020 chars).
   - The image endpoint did find "black mouse" on frame 0, so video concept detection with
     attribute phrases is much stricter than image detection.
   - Implication: the perception planner must verify phrases on sampled frames and fall back to
     generic nouns (this is a good demo moment for the planner).
2. **Spurious objects.** In clip B, `"mouse"` produced two extra IDs, in 33 and 16 frames, about
   160 px from either mouse: a reflection in the cage wall and an object at the frame edge. Fix:
   keep the two most persistent tracks inside the arena-floor ROI.
3. **Detection gaps, worst during mount** (all are at track start; see the coverage note in §2).
   - Clip B's resident is missing in frames 0-3 (fast tumbling, motion blur).
   - Clip A's intruder is missing for 4 s (frames 0-39), which covers the entire mount.
   - A gap at the start of a clip has nothing to interpolate from, and interpolation can't recover
     the hidden animal's posture anyway.
   - Candidate fixes (all **untested**):
     - "partner disappeared while in contact" as a mount/attack cue, together with the resident's
       blob area and shape;
     - visual adjudication on frame strips, where white fur under the black mouse is visible;
     - a SAM box/point object prompt that re-seeds the intruder at its last-seen position.
   - This is the main accuracy risk for mount, which is 12% of test frames.
4. **The implant and cable are part of the resident's mask.** That inflates its area (about 18k px
   vs 5-15k px for the intruder) and pulls its centroid toward the head. Use orientation and
   elongation with care; the head end can come from the mask's narrow end or from the cable.
5. **Mask decoding is CPU-bound.**
   - Measured cost: about 0.35 s per mask with `lossless` masks, and about 0.40 s per mask with
     `one_bit`. `one_bit` output is about 28x smaller on the wire (529 vs 15,015 chars per frame
     line).
   - Profile: the time is pure-Python range decoding, done twice per mask (`meta-sam-parser`
     validates on parse, and then `decode_mask_to_raster` decodes and re-encodes to check
     canonical form).
   - Decoding once with the inner decoder should give about 0.14 s per mask (estimate, not yet
     measured). That's about 6.8 CPU-hours for the full test split, or about 1.7 h on this VM's 4
     cores.
   - A compiled port of the codec (allowed: SAM License, derivative works permitted) should remove
     the problem. Until it's measured, it is a schedule risk for the speed claim (see wall-clock
     below).
6. **Frame alignment** between video frame 0 and label frame 0 is *approximately* verified, not
   exactly. Box centers best match the keypoints at a shift of -1 to -2 source frames (under one
   10 fps step), but box centers are biased by tails. The evaluator should check this with mask
   centroids over more frames.

## Measured costs (all spike work, from `bin/spend`)

| Item | Cost | Latency |
|---|---|---|
| SAM video, 200 frames, one phrase (x8 calls) | $0.04 each (`video_frames_processed` = 200) | 7-9 s (1 object), about 21 s (`"mouse"`, 2-4 objects, lossless) |
| SAM image (x2) | $0.0025 each | 1-3.6 s |
| Baseten video description, 20 s clip, effort high | $0.0092 (B), $0.0124 (A) | 17 s, 25 s |
| Meta, Baseten, and Fireworks text smoke calls | $0.0006-$0.0010 each | 1.6-4 s |
| Fireworks image smoke call | $0.0018 | 9.8 s |
| **Total spike + smoke** | **$0.35** (Meta $0.326, Baseten $0.022, Fireworks $0.003) | |

Reasoning tokens are counted inside `completion_tokens` on all three providers. For example, Meta
reported 228 output tokens, 215 of them reasoning, for a 15-character answer. So effort is the main
cost lever for LLM calls.

## Projected cost for the full CalMS21 eval

SAM at 10 fps, one `"mouse"` call per video (split from `eval/splits/calms21_v1.json`, by the
evaluator):

| Set | Frames billed | SAM cost |
|---|---|---|
| dev (8 seqs, 20.5 min) | 12,329 | $2.47 |
| dev-holdout (5 seqs) | 9,639 | $1.93 |
| test, all 19 seqs (145.6 min) | 87,374 | $17.47 |
| **dev x2 + holdout + test x1** | | **$24.34** of the $40 `eval-sam` tag |

Two phrases per video would cost $48.67, which exceeds the tag. The spike shows two phrases are
both unnecessary and worse.

LLM stages (rough; the volume of borderline bouts is unknown until the rules exist):

| Stage | Assumption | Estimate |
|---|---|---|
| Rule synthesis/revision | about 10 calls at effort high, about 5k in / 8k out | under $0.50 (Meta) |
| Tier-1 adjudication | about 3,000 strips x (about 3k in + 0.5k out) on Fireworks | about $18 (Fireworks cap: $90) |
| Tier-2 escalations | about 500 clips x about $0.011 on Baseten | about $5.5 (Baseten cap: $90) |
| Planner + report | | under $1 |

Every test-split SAM pass is over $5, so it needs `ops/spend-plan.md` and an escalation before it
runs (per CLAUDE.md).

**Wall-clock per video**, for the "accelerate a scientific experiment" claim:
- The SAM *request* ran at about 1x real time on 20 s clips (21 s for 20 s of video).
- Decoding the masks afterward took far longer: about 400 masks x 0.35 s, roughly 140 s serially.
  That makes the end-to-end tracking step about 8x real time today, before any LLM stage.
- So the speed claim depends on the decoder fix. Until it is measured, it is a schedule risk, not
  a settled point.
- Latency on long videos is not measured yet; the API takes up to 15,000 frames per request.
- Manual scoring baseline: *"Scoring for social behaviors often takes human annotators 3–4× the
  video's duration to annotate"*. Source: Segalin et al., MARS, eLife 2021, Introduction
  (verified verbatim). The paper gives no further citation for that figure.

## Recommendation: **conditional GO** (revised after Codex review, see below)

**What is established:**
- API access works on all providers.
- Costs are small and fit the budget with margin: $0.35 so far, $24 projected for the SAM part of
  the eval.
- SAM 3.1 gives two persistent tracks with correct identity from one generic phrase in open
  interaction and in attack.

**What is not established:**
- that tracking supports **mount** (the intruder disappears under the resident);
- end-to-end speed (mask decode dominates);
- that tier-2 adjudication works (only full-clip descriptions were tested, not cropped candidate
  bouts);
- anything about zero-shot behavior **accuracy**.

The evidence is 2 clips, 40 s of video, from 2 of 70 train sequences. The identity reference is the
MARS pose estimate, not human-labeled positions. Long videos are untested.

**Go/no-go checkpoint before any UI or distillation work:** a rules-only (plus tier-1) dev run on
complete sequences, reporting per-behavior precision, recall and F1, tracking coverage *during each
behavior*, and uncached cost and elapsed time.
- **NO-GO signals:**
  - mount recall stays near zero even with the "partner disappeared" cue and frame-strip
    adjudication;
  - attack false positives swamp ordinary interaction;
  - decode can't be made fast enough to finish the test set in time.
- **Firm GO:** all three behaviors clearly beat the random floor (.048 / .234 / .122 F1) on dev,
  with mount coverage handled.

**Fallbacks if identity breaks on long videos:**
1. *Tracker post-processing* (first choice, no new cost):
   - re-assign black and white identity from mask intensity;
   - keep the two most persistent tracks inside the arena ROI;
   - bridge short gaps.
2. *Chunked requests* (for example 60 s chunks with 1 s overlap). This adds about 2% SAM cost.
3. *Intruder re-seeding* with a SAM box/point object prompt after it disappears (untested, cheap
   to test at $0.04).
4. *Open SAM 3.1 weights on a GPU*: the last resort. It needs a dedicated GPU, which requires
   Aniket's approval. The spike gives no reason to need it yet.

## Independent review (Codex, `bin/codex-ask`, 2026-10-28)

Codex was asked: "Is the go/no-go recommendation justified by the evidence?" It answered
**"conditional GO for a bounded feasibility phase; the evidence does not yet justify saying 'the
core bet holds.'"** Its points:
1. The intruder is missing for all 27 mount frames, and the inspected contact sheet only covered
   frames after the mount. **I verified this and was wrong in the first draft; corrected above.**
2. The identity metric isn't a swap count. Agreed; the wording is qualified above.
3. Speed excludes decoding. Agreed; corrected above.
4. Tier-2 adjudication is untested, and the model's "thumbnails" remark isn't evidence about
   provider-side sampling. Agreed.
5. The proposed fixes (brightness identity during occlusion, image-only phrase checks) need
   validation. Agreed; the planner design requires a video probe.
6. EVAL_PLAN wording: the claim should be "no labeled training frames", with dev tuning disclosed.
   Also, test-label aggregates informed dev selection, which must be disclosed. Agreed; passed to
   the evaluator.

The original first-draft recommendation was "GO". The revision above adopts Codex's framing.
Full text: `ops/codex/ask-20261028-041949.md`.
