# Kickoff prompt for the lead session

You don't paste this anywhere. `bin/lead-start` launches the lead session in a detached tmux
session with Remote Control on, and tells it to read this file and follow everything after the line
below. `bin/lead-watchdog` (cron, every 5 minutes and at boot) resumes it if the process ever exits.

---

Read CLAUDE.md and the agents in .claude/agents/. You're the lead on Etho from now until submission.
Run `date` first; the VM clock is UTC and deadlines are in ET. Three keys are in the secrets file:
Baseten ($100 credit), Meta Model API ($150 hackathon credit), and Fireworks ($100 credit). Budget caps and Meta's per-tag allocation are in CLAUDE.md; build tag support
into the gateway from the start. Work through Phase 0 and Phase 1, then stop and report.

## Phase 0: infrastructure (target: under 2 hours)

1. **Repo skeleton:** `etho/` (package), `etho/gateway/`, `eval/`, `docs/`, `ops/`, `bin/`, `tests/`.
   uv project, pytest, a `Makefile` with `check` (lint + tests). Create `docs/STATE.md` and
   `docs/DECISIONS.md`.
2. **Gateway** (`etho/gateway/`), the only path to any model:
   - Clients for Meta Model API (Responses + Chat Completions, Files upload, SAM 3.1 video) and
     Baseten and Fireworks (both OpenAI-compatible; optional Together AI client, disabled unless
     `TOGETHER_API_KEY` is set). Load keys from `~/.config/etho/secrets.env` at runtime.
   - Cost accounting from returned usage (tokens, frames) with the prices in CLAUDE.md; append every
     call to `ops/spend.jsonl` (time, provider, model, units, est. cost, caller, cache hit).
   - Per-provider hard caps from CLAUDE.md; refuse calls that would exceed them.
   - Require an explicit reasoning effort on every LLM call (mapped per provider as in CLAUDE.md's
     model policy), and log `reasoning_tokens`.
   - Pre-call cost estimate for SAM from frame count.
   - Disk cache keyed by request hash.
   - Retries with backoff on 429/5xx; respect Retry-After.
   - Tests with mocked HTTP, including: cap refusal, cache hit costs $0, cost math.
3. **CLIs** in `bin/`: `spend` (totals per provider and per tag vs cap), `muse`, `open` (Baseten Muse
   Spark Open), `fw` (Fireworks), `sam`, all thin wrappers over the gateway.
4. **Claude Code config** in `.claude/settings.json`. Check the current Claude Code docs for the exact
   hooks and permissions schema first; don't guess.
   - Allow: uv, pytest, make, git (not force push), ffmpeg, ffprobe, jq, python, bin/*.
   - Deny: reading `~/.config/etho/**`, `git push --force`, `gh repo edit`, `gh repo create`.
   - A PreToolUse hook that blocks Bash commands sending requests straight to `api.meta.ai`,
     `inference.baseten.co`, `api.fireworks.ai` or `api.together.xyz` (everything goes through the gateway).
   - Commit `.claude/settings.json` before spawning any session: worktrees only see committed files.
5. **Smoke tests:**
   - One short text call on Baseten with model `meta-models/muse-spark-open-1026` (the only model
     the key allows; don't call `/v1/models` or try other IDs).
   - One text call to `muse-spark-1.3` and one SAM 3.1 image call.
   - Fireworks: one text call and one image call to `accounts/fireworks/models/muse-spark-open` with
     `reasoning_effort: low`. Confirm the hackathon key is accepted (credits may be model-restricted).
   - Fireworks Glimmer: exactly one tiny call to `accounts/fireworks/models/muse-glimmer-30b`. A 404 is
     expected (serverless retired Sep 25); record the result and don't retry.
   - Report the cost of each from `bin/spend`.
   - Codex: run `codex exec --sandbox read-only "Reply with: ready" </dev/null` from the repo and
     confirm it works. (Without `</dev/null`, Codex can hang waiting on stdin.)
     Add `bin/codex-*` and `codex exec` to the allowlist, and deny reading `~/.codex/**`.

## Phase 1: feasibility spike (go/no-go)

0. Check `docs/PROBLEM_STATEMENTS.md`. If it exists, write a short note on which statement Etho fits
   and how. If it's missing, escalate: this is a pass/fail gate.
1. Check free disk first. Download CalMS21: annotations and the released videos (only what the
   spike and the proposed split need), and record the dataset license. Figure out exactly which annotated
   sequences have video, and propose a dev/test split. The evaluator writes this into a draft
   `docs/EVAL_PLAN.md`; don't commit targets yet.
   Ask the scout to confirm which MABe22 datasets include raw video (for the second-species demo).
2. Cut two 20-second clips at 10 fps: one with close contact, one with an annotated attack or mount.
3. **Baseten video test:** send a clip to Muse Spark Open and ask for a timestamped description of
   the two mice's interactions. Record whether it accepts the video, latency, and cost.
4. **SAM test:** track "black mouse" and "white mouse" on both clips.
   Render overlay videos. Measure identity swaps during close contact by inspecting sampled frames,
   and record cost.
5. Write `docs/SPIKE.md` covering:
   - what worked, with evidence;
   - failure modes;
   - measured costs and a projected cost for the full eval;
   - a go/no-go recommendation, plus the fallback if SAM identity breaks in close contact (open SAM
     3.1 weights on a GPU, or tracker post-processing).

## Then stop

Update `docs/STATE.md` with a day-by-day plan through Nov 4. Commit and push. Then post the summary
in this session (what's verified, what's not, total spend, and the 2-3 decisions you want me to
confirm) and send me a PushNotification saying the checkpoint is ready. Wait for my reply in this
session; I'll answer from my phone.
Before stopping, run `bin/codex-ask docs/SPIKE.md "Is the go/no-go recommendation justified by the
evidence?"` and include its answer. Use subagents where they genuinely help (scout for docs and dataset digging, implementer for the
gateway, evaluator for the split). Do Phase 0 steps 2-4 yourself or with one implementer, not a swarm.
