# Etho state

_Updated 2026-10-29 07:00 UTC (03:00 ET)._

## Latest (Oct 30)
- **Notify check:** an in-session cron (every 20 min, :07/:27/:47) pushes unsent rows of ops/notify.jsonl to Aniket.
  It is session-only and expires Nov 6. **Re-create it after any session restart, and before Nov 6** (CronCreate).
  At session start, also read ops/notify.jsonl.
- **Funnel outages (Oct 28 22:41, Oct 29 05:35 UTC):** both were `sudo tailscale funnel reset` from SSH sessions on
  pts/1 (128.135.204.122; 73.9.100.228), not tailscaled or VM events. Funnel was back Oct 30 04:03 UTC.
- In progress: uptime cron + down alerts + funnel keepalive (implementer); HEAD, privacy notice, read-only UI,
  retention sweep P2 (implementer); showcase representativeness numbers (evaluator; rule pre-stated in DECISIONS).

- **Oct 29 18:58 UTC: restarted by Claude** (restart-only sudo rule installed; blanket NOPASSWD removed). Showcase,
  sample clip (CC BY 4.0 credit) and read-only 403 verified on 127.0.0.1:8000; the two Codex fixes are now running.
  **Both go-live blockers below FIXED 19:06 UTC** (Aniket: pre-flight OK, drop-in ProtectKernelTunables=no, app.env
  caps; Claude restarted): log shows "rule sandbox: bwrap isolation active" and "caps $3.00 per 24 h ($8.00 in the
  judging window) and $30.00 in total". Still read-only on purpose (READ_ONLY set, no access code file) until Nov 3.
  Were:
  1. bwrap unusable under the unit: "Can't mount proc on /newroot/proc: Operation not permitted" (most likely
     `ProtectKernelTunables=yes`; HOSTING.md section 5 pre-flight). Public live mode stays read-only until fixed.
  2. app.env still has the old caps: "$3.00 per 24 h (unchanged in the judging window) and $20.00 in total", so the
     approved $8/day Nov 4-9 and $30 total are not in effect.
- **Overnight Codex review fixes merged** (d0a9e83): fly slow-velocity margin, retention keeps shared cache keys and
  Meta files (per-key lock + recheck; lock files left to the idle sweep), revision reports/views no longer claim "no
  labeled examples". Two Claude reviewer rounds; 700 passed, lint clean. Codex second opinion queued
  (20261029T050548-review-fd07b4). **Not live yet: Aniket restarts in the morning** (then phone check on cellular +
  section 7). Must be live and verified on the hosted app before the Nov 3 go-live (report in the Oct 31 status).
- **Sudoers:** `ubuntu` already has `NOPASSWD: ALL` (cloud-init default); Aniket has the commands for a
  restart-only rule and for removing the blanket rule. Claude restarts only after he confirms it is installed.
- **Reliability pass, done so far (all no spend):**
  - Fresh clone from GitHub (e8d831b): `uv sync` + `make check` 683 passed, 17 skipped. 13 skips were bwrap,
    because the clone was under /tmp (the sandbox's private /tmp hides the .venv); a clone in ~ passes the sandbox
    tests (25 passed). README note added (781a73b). The other 4 skips need gitignored data.
  - Cap-hit drill (separate public instance on :8772, total/daily cap $0.05): upload refused with 429 `daily_cap`
    and the friendly fallback message; showcase still listed; 0 ledger rows.
  - Kill-switch drill (same instance): health `mode: killed`, upload refused 503 `killed`, showcase page and sample
    clip 200; removing the file returns to `live`. No errors in the log; the access code never appears in it.
  - Still to do: the same drills on the hosted unit after the restart, and the judge path from a phone on cellular.
- **Aniket decisions (Oct 29):** "No label goes unexplained."; 0.7 s seeded rules step shown; X tag official or
  `@AIatMeta`; push before any restart Nov 4-9.

## Earlier (evening Oct 28)
- **Hosting:** Aniket ran the sudo steps; `etho-app` is active in read-only mode at the Funnel URL (ops/public-url,
  untracked). It still runs pre-merge code: **needs `sudo systemctl restart etho-app`** to load the cap schedule,
  the ADR-005 bundle and the new showcase. The VM cannot reach its own Funnel URL (known loopback limit), so external
  checks run from Aniket's Mac / a phone.
- **ADR-005 bundle merged** (3b93891 + 89762a2): planner card, phased progress with real clock, bout trace, ethogram
  polish + expert row, sample clip, job-showcase packaging, presentation mode. Claude reviewer: merge after fixes, all
  9 fixed. Codex refused by policy (app-only). `make check`: 688 passed, lint clean.
- **Public caps:** $3/day, $8/day Nov 4-9 ET, $30 total (5dba069).
- **Showcase:** `sc-calms21-mouse075-live`, a real app run ($0.89 dev, 0.43x real time); scripted error recurs.
- **Form:** docs/FORM_FINAL.md final (Aniket's review applied); open: X handle/hashtag.
- **Script v4** adopted; captions draft docs/demo_captions_v4.srt; architecture diagram docs/architecture.svg.
- **Next:** reliability pass (fresh-clone README test; judge path from a phone on cellular; kill-switch and cap-hit
  drills), Oct 30 checkpoint, README polish, results in the Oct 31 status.

## Status (updated 2026-10-28 17:35 UTC / 13:35 ET)

### Test run 1: DONE, scored once (`eval/results/test1_report.md`, sections 1-10 plus corrections in section 11)
| Arm | Macro F1 [95% CI] | Attack | Investigation | Mount |
|---|---|---|---|---|
| **zs_t1** (primary, training-free + tier-1) | **0.580** [0.493, 0.621] | 0.449 | 0.635 | 0.656 |
| zs_rules | 0.375 | 0.324 | 0.240 | 0.561 |
| fs_t1 (18 corrections simulated from dev labels) | 0.572 | 0.461 | 0.682 | 0.574 |
| fs_rules | 0.424 | 0.338 | 0.426 | 0.508 |

- **Baselines:**
  - 73.1% of the raw-pose 1D ConvNet baseline (.793; per class .664/.814/.900). Better published methods exist (.829 and .864).
  - Frequency-matched random reference: 0.1345.
- **Paired differences:**
  - Tier-1 lift: +0.205, robust.
  - fs_t1 vs zs_t1: inconclusive, so no accuracy claim for the correction loop.
- **Identity error:** 0.29%. Coverage is 0.973, so the "good" level is not met.
- **Speed including preprocessing:** 0.88x real time pooled, median 0.86x, max 1.54x. Preprocessing was measured
  after the run; the "good" speed target is not met. Tier-1 is the bottleneck.
- **Cost:** $11.25 per hour of video (model API at logged rates, excluding the planner).
- **Spend:** Fireworks $19.80 for tier-1 (approved up to $25). The run crashed once on a timeout and was resumed
  from cache; the decision is logged.
- **Codex test-results gate (Astra):** done, `ops/codex/ask-20261028-165452-293961.md`. The accuracy result stands.
  The corrections are applied in report section 11. The headline wording is escalated.

### Also done today (to 19:00 UTC)
- **Sandbox:** the rule sandbox is isolated with bwrap and merged.
- **Live app:** a fresh 4-min clip ran at 0.80x. With tier-1 concurrency 16 (now the app default) the fresh-clip sweep gave 0.60x end to end.
- **Headline wording:** approved and applied in the README and the report header. The evidence card and accuracy/speed lines are approved (ESCALATIONS ~13:50 and ~14:35 ET).
- **License:** Apache-2.0 LICENSE and NOTICE. The SAM-derived decoder files stay under the SAM License.
- **Demo plan:** v2 approved, script ~431 words with sources, clip mouse075 0-300 s. CalMS21: no test-2 (A3); per-video results unsealed.
- **Fly vs. Fly:**
  - tracking merged (dev movies 1-2, $3.71);
  - definitions from Chen 2002;
  - eval plan frozen (F1), and Aniket's claim bar added (ADR-004 / F2: CI lower > r, F1 >= 0.40, shown next to the simple baseline).
- **Hosting (ADR-003, approved):** guards built on a branch, and Meta file delete is live-verified. The Claude review says merge after fixes and NO-GO for go-live as written; the fixes are in progress.

### Fly vs. Fly: finished, qualitative (2026-10-28 20:50 UTC)
- **Lunge gate (dev 1-2):** GO, branch mid5 at 30 fps.
- **Wing gate (train 1-5):** NO-GO. F1 0.584, but the CI lower bound 0.009 is below r = 0.043.
- **Outcome:** no fly claim and no fly test; the demo uses the NO-GO line. The README reports train numbers and baselines.
- **Spend:** $9.33 of the $19.5 approved.

### Hosting
- Merged (6fcdf2d). Go-live needs Aniket's sudo steps (docs/HOSTING.md sections 3-6), the section 7 checks, and Nov 3. Codex review queued.

### Next
1. App: an optional fix for discarded tier-1 verdicts, dev-tested and never claimed in the test numbers.
2. `docs/architecture.svg` for the demo's code beat.
3. Oct 30 ET checkpoint report.
4. README polish, and the pre-submission audit (internal target Nov 4 12:00 PM ET; official deadline 9:00 PM ET).

### Waiting on Aniket
- The remaining submission-form fields.
- The sudo steps for hosting.

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
