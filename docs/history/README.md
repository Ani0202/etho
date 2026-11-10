# docs/history/: how Etho was built (records from the hackathon, Oct 26 to Nov 9, 2026)

These files are records, not instructions; they are kept as they were when moved. The whole repo as it was at the
submission deadline, including the agent setup, is the tag
[`hackathon-submission`](https://github.com/Ani0202/etho/tree/hackathon-submission) (commit `44bdc15`).

| File | What it is |
|---|---|
| `DECISIONS.md` | Decision log and ADRs: every engineering and methodology choice, with evidence |
| `SPIKE.md` | Phase-1 feasibility spike (Oct 28): what SAM 3.1 tracking got right and wrong, measured and projected costs, the go decision (`scripts/spike_sam.py`) |
| `STATE.md` | Running progress log (done / in progress / next / blocked), Oct 26 to Nov 9 |
| `PROBLEM_STATEMENTS.md` | The official problem statements and how Etho maps to "Push the Frontier" |
| `DEMO_PLAN.md` | Plan and beat timeline of the 3-minute demo video |
| `spend-plan.md` | Written estimates for jobs over $5, approved before they ran |
| `spend.jsonl` | The spend ledger at the deadline (every model call, cost, tag); identical to the tag's `ops/spend.jsonl` |
| `setup_vm.sh` | One-time setup of the build VM |
| `codex/` | Codex (GPT) blind critiques, gate reviews and code reviews, cited by the test report and EVAL_PLAN |

## Old paths

`eval/` and `docs/EVAL_PLAN.md` are frozen (pre-registered plan and results), so their citations keep the old paths.
Those paths resolve in the tag; in this branch they map to:

| Old path | Now |
|---|---|
| `docs/DECISIONS.md`, `docs/SPIKE.md`, `docs/STATE.md`, `docs/PROBLEM_STATEMENTS.md`, `docs/DEMO_PLAN.md` | `docs/history/<same name>` |
| `ops/spend.jsonl` (as cited for results) | `docs/history/spend.jsonl` (the live ledger is still written to `ops/spend.jsonl`, untracked) |
| `ops/spend-plan.md`, `ops/setup_vm.sh` | `docs/history/<same name>` |
| `ops/codex/ask-*.md`, `ops/codex/review-*.md`, `ops/codex/adr001-blind-context.md` | `docs/history/codex/<same name>` |
| `docs/ESCALATIONS.md`, `docs/KICKOFF.md`, `docs/SUBMISSION.md`, `docs/FORM_FINAL*.md`, `docs/VIDEO_APP_SCRIPT.md`, older caption drafts, `docs/screenshots/`, `docs/video_assets/`, `video/`, `bin/codex*`, `ops/codex/usage.jsonl` | removed; in the tag only |
| `CLAUDE.md`, `AGENTS.md`, `.claude/`, `bin/lead-*`, `bin/spawn` | agent setup, not in the repo; in the tag |
