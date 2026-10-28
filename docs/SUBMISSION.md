# Submission checklist

These are Aniket's steps, from the Official Rules excerpt he sent on 2026-10-28. Claude never submits,
publishes, or changes repo visibility.

**Official deadline: Nov 4, 9:00 PM ET. Late submissions are not considered.**
**Internal target: Nov 4, 12:00 PM ET** (our own margin, not the hard deadline; Aniket's correction,
2026-10-28 ~18:05 ET). The pre-submission audit runs the evening of Nov 3 and the morning of Nov 4.
Aniket is confirming the time zone on the participants page.

## Artifacts (all three are judged)
- [ ] **Code:** the repo is made public by Aniket. Before the flip, check:
  - [ ] no secrets in git history (pre-submission audit: `git log -p | grep` for key patterns, plus the reviewer
    and the Codex `pre-submission` gate);
  - [ ] no raw data: CalMS21 and Fly vs. Fly videos, .mat files and annotation arrays stay in gitignored `data/`;
  - [ ] no `pub:job:` rows (visitor job ids) in the committed `ops/spend.jsonl` (DECISIONS 2026-10-28);
  - [ ] `LICENSE` (Apache-2.0) and `NOTICE` (SAM-License files, dependencies, dataset credits) are present;
  - [ ] the README runs from a fresh clone and includes "How this was built" (Claude Code, Codex).
- [ ] **Video:** 3:00 or shorter (hard limit), on YouTube as **Unlisted** (not Private).
  - [ ] Open the link in a logged-out or incognito browser and confirm it plays.
  - [ ] Shows features, code and functionality built during the event, with the code beat (architecture plus
    our gateway and cascade code).
  - [ ] Captions burned in, large fonts, even audio levels (finals are shown live on a shared screen).
  - [ ] Honesty checks in `docs/DEMO_PLAN.md` pass.
- [ ] **Live product:** the public read-only showcase plus live uploads behind the access code
  (`docs/HOSTING.md`).
  - [ ] Up from submission through Nov 9 (finals); live mode turns itself off on Nov 10 ET.
  - [ ] The watchdog and the 15-min external uptime check run, and `ops/uptime.jsonl` shows no gaps.
  - [ ] Caps of $3 a day and $20 in total from `demo-reserve`; when a cap is hit, the app falls back to the
    showcase with a message.
  - [ ] The access code goes in the submission notes only, never on the public page.

## Form
Fields from the participants page (Aniket, 2026-10-28). The answers below are **drafts for Aniket to edit and
own**; Claude never submits. Every number is from a committed file and uses the approved wording.

- [ ] Only Aniket listed as a team member.
- [ ] Links: public repo, unlisted YouTube video, live app URL (Tailscale Funnel); access code in the notes.

**Which track are you submitting to?** (required) Indie.

**Which problem statement did you build towards?** (required) Push the Frontier.

**Most Viral Track X post link** (optional) Leave blank unless Aniket posts one.

**Project Description** (required)
> Labs that study animal behavior still score it largely by hand: someone watches every second of video and
> logs what the animals do, which for social behavior takes three to four times the video's length (Segalin et
> al. 2021). Etho is training-free behavior scoring: a researcher uploads lab video, writes behavior definitions
> in plain English, and gets a scored ethogram, metrics and an auditable methods report, with no labeled
> examples given to the models and no model training. SAM 3.1 tracks each animal, Muse Spark turns each
> definition into a short Python rule that runs in a sandbox, and Muse Spark Open checks borderline moments from
> strips of frames. Every label traces back to the rule and model call that produced it, and the researcher can
> correct a few bouts and have the rule rewritten. On the 19 official CalMS21 test videos, scored once, Etho
> reaches 0.58 F1 with no training; supervised methods trained on labeled data and given pose keypoints reach
> 0.79-0.86. It scores a video unattended in about its own length (0.88x real time on test; worst 1.54x), at
> about $11 per hour of video in model API cost. Settings were tuned on 13 labeled development videos; full
> disclosure in the README.

**Public GitHub Repository** (required) ⟨https://github.com/…/etho, after Aniket makes it public⟩

**3-Minute Demo Video** (required) ⟨unlisted YouTube link, checked logged out⟩

**Describe your use of Meta products to build the submitted project** (required)
> Etho's runtime uses only Meta models, all through one gateway that caps spend and caches every call.
> - SAM 3.1 (Meta Model API, video): tracks both animals with persistent IDs from one noun phrase; we compute
>   position, heading, contact and speed from its masks.
> - Muse Spark 1.3 (Meta Model API): picks what SAM should track, writes each behavior's Python scoring rule from
>   the plain-English definition (high effort), rewrites rules from user corrections, and drafts the methods
>   paragraph. Contributor mode on public benchmark data; Standard mode for anything a judge uploads.
> - Muse Spark Open (Fireworks, serverless): the high-volume checker, judging strips of six overlay frames at
>   low effort. We also tested it on Baseten with video clips.
> - Meta Files API: uploads for SAM, deleted right after tracking.

**Why are Meta's models important to your solution?** (required)
> They make the zero-training design possible. SAM 3.1 gives tracked masks from a single word, so there is no
> pose model to train per lab. Muse Spark turns a published definition into readable, executable code, so the
> behavior logic is inspectable and editable rather than a black box. Muse Spark Open, shown six frames at its
> lowest effort, raised test F1 from 0.375 to 0.580; that check is what makes the rules usable. Open weights
> (Muse Spark Open) also matter for labs with unpublished data.

> ⟨Aniket: the last sentence is a direction claim (open weights for private data); keep or cut.⟩

**Provide feedback from your experience using Meta products** (optional; Aniket's experience, drafted from our logs)
> - SAM 3.1 video concept detection is much stricter than image detection: "black mouse" and "white mouse"
>   returned no objects on grayscale video while "mouse" worked, and "fly" returned nothing while "fruit fly"
>   worked. A note in the docs that video prompts should be generic nouns would have saved us time.
> - Once SAM has a track it holds it through occlusion (both mice labeled on 100% of mount frames), but an animal
>   hidden at the start of a chunk is never picked up; a re-detect option mid-video would help.
> - One phrase per request and a 50-frame billing minimum shape the design; per-request multi-phrase would halve
>   cost for two-species scenes.
> - The spend-limit notification does not stop billing, so we built our own hard cap. A real hard cap in the
>   console would help every team.
> - Explicit reasoning_effort on Muse Spark worked well for cost control.

**Provide feedback from your experience using partner technologies** (optional; Fireworks, Modal, Baseten)
> - Fireworks: serverless Muse Spark Open was our workhorse (about 3,200 frame-strip calls on the test run).
>   Throughput scaled almost linearly from 6 to 16 concurrent calls with no rate limiting; median latency was
>   8-12 s per call. One read timeout in the test run. Muse Glimmer is no longer on serverless inference, so trying
>   it would have meant a dedicated deployment; we did not use it.
> - Baseten: video input to Muse Spark Open worked. Rough edges: no JSON mode or structured outputs, n>1 returns
>   400, the hackathon key works only with one exact model ID, and reasoning effort defaults to the most
>   expensive level if omitted.
> - Modal: not used.

## Before submitting (Nov 4 morning, before the 12:00 PM ET internal target)
- [ ] Pre-submission audit: the Claude reviewer plus `bin/codex-ask --gate pre-submission`.
- [ ] Every number in the README, video and app matches a committed results file.
- [ ] `bin/spend` and `bin/codex-usage` summaries are in the final STATE.md.
