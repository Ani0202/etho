# Submission checklist

These are Aniket's steps, from the Official Rules excerpt he sent on 2026-10-28. Claude never submits,
publishes, or changes repo visibility.

**Official deadline: Wed Nov 4, 9:00 PM, via the CV platform. Late submissions are not considered.** The
participants page shows no time zone, so treat it as 9:00 PM ET (Aniket, 2026-10-28).
**Internal target: Nov 4, 12:00 PM ET** (our own margin; it stays there).
- [x] **Nov 3:** Discord (organizers): no private field or channel for judge access and the README is public, so no
  access codes anywhere; judges work from the repo and the video, a hosted demo is optional and must be
  credential-free. Submissions can be edited until Nov 4, 9:00 PM ET.
- [ ] **Nov 3:** the form is fully filled and ready to submit, so any platform issue can be raised in Discord that
  day (Nov 3 is the last full day for submission questions and troubleshooting). Find out whether submissions
  can be edited after sending.
- [ ] **Nov 3, after live mode is on:** test event streaming end to end. Submit a short job from the public URL
  (phone or Mac, not the VM) and confirm the progress events arrive live, not only at the end (Aniket, 2026-10-30).
- The pre-submission audit runs the evening of Nov 3 and the morning of Nov 4.

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
- [ ] **Live product:** the public read-only showcase, no login; uploads of visitors' own videos stay off
  (`docs/HOSTING.md`).
  - [ ] Up from submission through Nov 9 (finals); live mode turns itself off on Nov 10 ET.
  - [ ] The watchdog and the 15-min external uptime check run, and `ops/uptime.jsonl` shows no gaps.
  - [ ] Caps from `demo-reserve`: $3 a day before Nov 4, **$8 a day Nov 4-9 ET** (judging window), **$30 in total**; when a cap is hit, the app falls back to the
    showcase with a message.

## Form
- [ ] Only Aniket listed as a team member.
- [ ] Paste the answers from `docs/FORM_FINAL.md` (one code block per field, short and full versions, sources
  table at the end). Fill the placeholders: repo URL, unlisted video URL, live app URL, X post link if any.
- [ ] The access code written on Nov 3 was never shared; it can be deleted (HOSTING.md section 15) or left unused.

## Before submitting (Nov 4 morning, before the 12:00 PM ET internal target)
- [ ] Pre-submission audit: the Claude reviewer plus `bin/codex-ask --gate pre-submission`.
- [ ] Every number in the README, video and app matches a committed results file.
- [ ] `bin/spend` and `bin/codex-usage` summaries are in the final STATE.md.
- [ ] `ops/funnel-off` does not exist and `crontab -l` shows funnel-keepalive and uptime-check.
