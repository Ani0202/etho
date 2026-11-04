# Submission form: final answers (paste-ready)

For Aniket to paste into the CV platform form. Claude never submits. Each field has a **short** version (under
500 characters) and a **full** version, because the form's limits are unknown. Each answer stands on its own. The
numbers' sources are in the table at the end. Aniket's review of 2026-10-28 is applied (flagged lines kept and reworded).

Placeholders look like `<...>`. Replace them before pasting.

---

## 1. Which track are you submitting to?

```
Indie
```

## 2. Which problem statement did you build towards?

```
Push the Frontier
```

## 3. Most Viral Track: X post link

```
<X post URL, only if you post it>
```

Draft X post: 261 characters without the handle/hashtag, counting the video link at 23 characters as X does, so a handle or hashtag of up to 18 characters fits. Replace `<handle/hashtag>` with the official hackathon tag from the participants page, or `@AIatMeta` (9 characters) if there is none (Aniket, 2026-10-29; Aniket verifies the handle); if it doesn't fit, drop the video link and attach the video to the post instead.

```
Many labs still hand-score animal behavior, at 3-4x the video's length. I built Etho: plain-English definitions in, scored behavior out, no training. 0.58 F1 on CalMS21's test set (supervised: 0.79-0.86). On Meta's SAM 3.1 + Muse Spark. <handle/hashtag> <video URL>
```

## 4. Project Description

Short:

```
Behavior labs often still score video by hand, at 3-4 times the video's length for social behavior. Behavior scoring underpins neuroscience research and drug-safety studies (ICH S7A; OECD TG 424). I built Etho: lab video and plain-English definitions in, a scored ethogram and auditable report out, with no model training. On the 19 CalMS21 test videos, scored once: 0.58 macro F1 (averaged over attack, investigation and mount); supervised methods reach 0.79-0.86. Tuned on 13 labeled dev videos.
```

Full:

```
Labs that study animal behavior often still score it by hand: someone watches every second of video and logs what the animals do. For social behavior that takes human annotators three to four times the video's length (Segalin et al. 2021, eLife). Behavior scoring underpins neuroscience research and drug-safety studies (ICH S7A; OECD TG 424).

I built Etho, training-free behavior scoring. A researcher uploads lab video, writes behavior definitions in plain English, and gets a scored ethogram, metrics and an auditable methods report. No labeled examples are given to the models and no model is trained.

How it works: Muse Spark picks a phrase for SAM 3.1 to track, and SAM 3.1 follows each animal with a persistent identity. Muse Spark turns each definition into a short Python rule that runs in an isolated sandbox. Muse Spark Open then checks borderline moments from strips of six frames. Every label traces back to the rule and model call that produced it. The researcher can correct a few bouts and have the rule rewritten, so they stay in control.

Results on the public CalMS21 mouse benchmark, all 19 official test videos, scored once: 0.58 macro F1 (averaged over attack, investigation and mount: 0.449, 0.635 and 0.656). Supervised methods trained on labeled data and given pose keypoints reach 0.79-0.86; Etho works from raw video. It scores a video unattended in about its own length (0.88 times real time on test; worst video 1.54 times), at $11.25 per hour of video in model API cost for the fixed benchmark pipeline (excluding the planner and one-time rule writing).

Disclosure: pipeline settings were tuned on 13 labeled development videos. Test-label summaries (behavior and bout counts) informed dev-set selection, the 10 fps sampling rate and the targets; no test predictions were used to choose settings. A second species (fighting fruit flies) runs on the same pipeline but is shown only as a qualitative preview, because it did not clear the confidence bar I set in advance.

Try it without any keys: `make demo` in the repo runs the full pipeline on a 60-second CalMS21 sample clip in about 20 seconds, replaying recorded Meta model responses (clearly labelled as a replay; it never calls a model or the network, and it stops with an error on anything it has no recording for). With your own Meta Model API key, the same pipeline runs live on your video.

Code, results and every disclosure are in the public repo.

Live app (no login; precomputed runs, read-only): <URL>.
```

## 5. Public GitHub Repository

```
https://github.com/<owner>/etho
```

Replace with the final URL after you make the repo public. Check it in a logged-out browser.

## 6. 3-Minute Demo Video

```
https://youtube.com/watch?v=<id>
```

Use the Unlisted video (not Private), checked in a logged-out browser.

## 7. Describe your use of Meta products to build the submitted project

Short:

```
Etho's runtime uses only Meta models. SAM 3.1 (Meta Model API) tracks each animal from one noun phrase. Muse Spark 1.3 picks that phrase, writes each behavior's Python rule from its English definition, rewrites rules from user corrections, and drafts the methods paragraph. Muse Spark Open on Fireworks checks borderline moments from six-frame strips. Every call goes through a gateway I built that caps spend and caches results.
```

Full:

```
Etho's runtime uses only Meta models, and every call goes through a gateway I built that caps spend, caches every result and logs each call.

- SAM 3.1 (Meta Model API, video): tracks both animals with persistent identities from a single noun phrase. I compute position, heading, contact and speed from its masks, frame by frame. On the CalMS21 test videos the identity error is 0.29%.
- Muse Spark 1.3 (Meta Model API): proposes tracking phrases from sample frames (SAM then checks each one), writes each behavior's Python scoring rule from its plain-English definition at high reasoning effort, rewrites a rule when the researcher corrects bouts, and drafts the report's methods paragraph. It runs in contributor mode on public benchmark data and in Standard mode for anything a judge uploads.
- Muse Spark Open (Fireworks, serverless): the high-volume checker. It judges strips of six overlay frames at low reasoning effort and can change a borderline window to investigation or none; its attack and mount verdicts cannot overwrite the rule.
- Muse Spark Open (Baseten, video): I tested sending disputed clips as video. It added +0.005 F1 on dev for $2.30 (on the dev disagreement set), so it is not in the final pipeline.
- Meta Files API: uploaded videos go to SAM and are deleted from Meta right after tracking.
- Offline replay for judges: `make demo` replays recorded Meta model responses (planner, SAM 3.1, Muse Spark Open, report) matched by meaning rather than bytes, refuses anything it has no recording for, and never touches the network, so the pipeline runs with no keys.

I used Claude Code and Codex as development tools, credited in the README's "How this was built" section. The product itself runs only on Meta models.
```

## 8. Why are Meta's models important to your solution?

Short:

```
They make the no-training design possible. SAM 3.1 tracks animals from one word, so no pose model has to be trained per lab. Muse Spark turns a published definition into readable code, so the behavior logic can be inspected and edited. Muse Spark Open, shown six frames at low effort, raised test macro F1 from 0.375 to 0.580, the biggest gain of any step on top of the rules.
```

Full:

```
Meta's models are what make scoring without training possible.

- SAM 3.1 gives tracked masks from a single word ("mouse"), so there is no pose model to train for each lab, camera or species. It held both mice through mounts: with 60 s chunks, both were labeled on 100% of mount frames (development video mouse016).
- Muse Spark turns a published English definition into a short, readable Python rule. The behavior logic is code a researcher can read, edit and audit, not a black-box classifier. Those rules alone reach 0.375 macro F1 on the CalMS21 test set, against a frequency-matched random reference of 0.134.
- Muse Spark Open checks the borderline moments. Shown six frames at its lowest reasoning effort, it raised test macro F1 from 0.375 to 0.580 (averaged over attack, investigation and mount). That is the biggest gain of any step on top of the rules, and the confidence interval excludes zero.

Together they take a researcher from raw video and a sentence per behavior to a scored, traceable ethogram, with no labeled examples given to the models.
```

## 9. Feedback from your experience using Meta products

Short:

```
SAM 3.1's video concept detection is much stricter than image detection: "black mouse" found nothing on grayscale video while "mouse" worked, and "fly" found nothing while "fruit fly" worked. A docs note to use generic nouns would save time. SAM held tracks through occlusion, but an animal hidden when a chunk starts is never picked up. Request: a smaller open-weight, video-capable model that a lab could self-host, so tools like Etho could process unpublished data on-premises.
```

Full:

```
What worked well:
- SAM 3.1 keeps a track through heavy occlusion: with 60 s chunks, both mice stayed labeled on 100% of mount frames (development video mouse016), and identity error was 0.29% on the test videos.
- Muse Spark writes readable, executable scoring rules from a one-paragraph definition, and setting reasoning effort explicitly on every call made cost predictable.
- Muse Spark Open at low effort was a strong checker: six frames per call lifted test macro F1 by 0.205.

What I'd change:
- SAM 3.1 video concept detection is much stricter than image detection. On grayscale video, "black mouse" and "white mouse" returned no objects in all 200 frames of a test clip, while "mouse" tracked both. The image endpoint did find "black mouse". For flies, "fly" returned nothing and "fruit fly" worked. A docs note that video prompts should be generic nouns would have saved me time.
- An animal hidden under another when a video chunk starts is never picked up later in that chunk (with 10 s chunks, two-animal coverage during mounts dropped to 15.6%). An option to re-detect mid-video would help.
- One phrase per SAM request and the documented 50-frame billing minimum shaped the design. Multiple phrases per request would cut cost for multi-species scenes.
- Per Meta's guidance, the spend-limit notification doesn't stop billing, so I built a hard cap in my gateway. A real hard cap in the console would protect every team.
- Muse Spark Open's attack and mount verdicts at low effort were only 21-26% right on dev, so it may change a window only to investigation or none; its attack and mount verdicts never overwrite the rule. Better fine-grained action recognition from frame strips would let it do more.

Request: a smaller open-weight, video-capable model that a lab could self-host would let tools like Etho process unpublished data on-premises.
```

## 10. Feedback from your experience using partner technologies (Fireworks, Modal, Baseten)

Short:

```
Fireworks: serverless Muse Spark Open handled nearly 10,000 frame-strip calls (median latency about 8 s). Throughput scaled from 6 to 16 concurrent calls, taking a 5-minute clip from 1.12 to 0.60 times real time. I hit one short burst of 11 rate-limit errors and one transport timeout. Baseten: 274 Muse Spark Open video calls for $2.32 worked, but added little over frame strips for my task. Modal: not used.
```

Full:

```
Fireworks: serverless Muse Spark Open was my workhorse. It handled nearly 10,000 uncached frame-strip calls over the event, with a median latency of about 8 s. Throughput scaled as I raised concurrency: at 6, 12 and 16 concurrent calls, a fresh 5-minute clip went from 1.12 to 0.72 to 0.60 times real time end to end. Problems I logged: one burst of 11 rate-limit (HTTP 429) errors within 7 seconds early in development, returned with the type "invalid_request_error", which is easy to mistake for a request bug; and one ambiguous transport timeout during the test run, which my pipeline does not retry by design. Muse Glimmer returned 404 on serverless inference, so I did not use it.

Baseten: I sent 274 uncached video-clip calls to Muse Spark Open for $2.32 in total (median latency about 9 s). Video input worked. For my task it added only +0.005 F1 on dev over the frame-strip checker, so it is not in the final pipeline. The API has no JSON mode or structured outputs, so I parse responses defensively.

Modal: not used.
```

---

## Sources for every number

| Number or claim | Field(s) | Source |
|---|---|---|
| 3-4 times the video's length (manual scoring) | 3, 4 | Segalin et al. 2021, eLife, doi:10.7554/eLife.63720, Introduction (cited in README and docs/DEMO_PLAN.md) |
| 0.58 (0.580) macro F1; attack 0.449, investigation 0.635, mount 0.656 | 3, 4, 8 | eval/results/test1_report.md (zs_t1, test run 1) |
| 19 test videos, scored once; 13 dev videos | 4 | eval/results/test1_report.md; eval/splits/calms21_v1.json; README footnote |
| Supervised 0.79-0.86 (.793, .829, .864) | 3, 4 | eval/results/test1_report.md §11.9 (Sun et al. 2021, arXiv:2104.02710, Tables 1, 2, 8) |
| 0.88 times real time on test, worst 1.54 times | 4 | eval/results/test1_report.md §11.3 |
| $11.25 per hour of video | 4 | eval/results/test1_report.md (model API cost at logged rates) |
| Six frames per strip, low effort | 4, 7, 8, 9 | etho/adjudication/tier1.py (FRAMES_PER_STRIP = 6); configs/frozen_test_v1.json |
| 0.375 to 0.580, +0.205, CI excludes zero | 8, 9 | eval/results/test1_report.md (tier-1 lift, zs_t1 - zs_rules) |
| Frequency-matched random reference 0.134 | 8 | eval/results/test1_report.md |
| "Biggest gain of any step on top of the rules" | 8 | test1_report (tier-1 +0.205; corrections inconclusive); docs/DECISIONS.md line ~133 (video tier +0.005 dev); README "What we learned" (medium effort -0.011 dev) |
| Identity error 0.29% | 7, 9 | eval/results/test1_report.md |
| 100% of mount frames (60 s chunks; development video mouse016); 15.6% (10 s chunks) | 8, 9 | docs/DECISIONS.md, chunk-length table |
| "black mouse" / "white mouse" 0 objects in 200 frames; image endpoint found "black mouse" | 9 | docs/SPIKE.md |
| "fly" 0 objects, "fruit fly" works | 9 | docs/DECISIONS.md, ADR-002 tracking line |
| Attack and mount verdicts 21-26% right at low effort (dev) | 9 | README "What we learned"; docs/DECISIONS.md |
| +0.005 dev F1 for $2.30 (video tier) | 7, 10 | docs/DECISIONS.md line ~133 |
| Nearly 10,000 uncached Fireworks calls; median latency about 8 s (7.9 s) | 10 | ops/spend.jsonl (provider fireworks, cache_hit false, no error: 9,864 rows on Nov 4) |
| 11 HTTP 429 errors within 7 s; 1 transport timeout; Glimmer 404 | 10 | ops/spend.jsonl (error rows, 2026-10-28 06:35 UTC; test-run timeout logged in docs/DECISIONS.md 15:05 UTC line) |
| Concurrency 6 / 12 / 16: 1.12 / 0.72 / 0.60 times real time | 10 | docs/DECISIONS.md, concurrency line; scripts/speed_sweep.py |
| 274 Baseten calls, $2.32, median about 9 s | 10 | ops/spend.jsonl (provider baseten, cache_hit false) |
| Neuroscience research and drug-safety studies | 4 | ICH S7A (2000) §2.7.1; OECD TG 424 (1997) (cited in docs/DEMO_PLAN.md sources table) |
| Files deleted from Meta after tracking | 7 | docs/DECISIONS.md (Meta file delete live-verified); etho/app/metafiles.py |
