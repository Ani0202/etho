# Submission checklist

These are Aniket's steps, from the Official Rules excerpt he sent on 2026-10-28. Claude never submits,
publishes, or changes repo visibility.

**Official deadline: Wed Nov 4, 9:00 PM, via the CV platform. Late submissions are not considered.** The
participants page shows no time zone, so treat it as 9:00 PM ET (Aniket, 2026-10-28).
**Internal target: Nov 4, 12:00 PM ET** (our own margin; it stays there).
- [ ] **Nov 3:** the form is fully filled and ready to submit, so any platform issue can be raised in Discord that
  day (Nov 3 is the last full day for submission questions and troubleshooting). Find out whether submissions
  can be edited after sending.
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
- [ ] **Live product:** the public read-only showcase plus live uploads behind the access code
  (`docs/HOSTING.md`).
  - [ ] Up from submission through Nov 9 (finals); live mode turns itself off on Nov 10 ET.
  - [ ] The watchdog and the 15-min external uptime check run, and `ops/uptime.jsonl` shows no gaps.
  - [ ] Caps of $3 a day and $20 in total from `demo-reserve`; when a cap is hit, the app falls back to the
    showcase with a message.
  - [ ] The access code goes in the submission notes only, never on the public page.

## Form
- [ ] Only Aniket listed as a team member.
- [ ] Paste the answers from `docs/FORM_FINAL.md` (one code block per field, short and full versions, sources
  table at the end). Fill the placeholders: repo URL, unlisted video URL, live app URL, X post link if any.
- [ ] Access code in the notes field only, never on the public page.

## Before submitting (Nov 4 morning, before the 12:00 PM ET internal target)
- [ ] Pre-submission audit: the Claude reviewer plus `bin/codex-ask --gate pre-submission`.
- [ ] Every number in the README, video and app matches a committed results file.
- [ ] `bin/spend` and `bin/codex-usage` summaries are in the final STATE.md.
