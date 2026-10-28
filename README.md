# Etho

Zero-shot animal behavior scoring. Upload lab video, write behavior definitions in plain English,
and get a scored ethogram, metrics, and an auditable methods report, with no labeled training frames.
Built for the Meta Global AI Developer Hackathon (Push the Frontier).

**Status:** early development (Phase 0/1: infrastructure and feasibility spike). See `docs/STATE.md`.

## Setup

```bash
uv sync                      # Python 3.11+, installs deps into .venv
make check                   # lint + tests (no network needed)
```

API keys go in `~/.config/etho/secrets.env` (outside the repo):

```
META_MODEL_API_KEY=...
BASETEN_API_KEY=...
FIREWORKS_API_KEY=...
# TOGETHER_API_KEY=...   (optional)
```

All model calls go through `etho/gateway/`, which enforces per-provider spending caps, caches every
response on disk, and logs each call to `ops/spend.jsonl`. `bin/spend` prints totals.

## How this was built

Developed with AI coding tools: Claude Code (lead engineer and subagents) and OpenAI Codex CLI
(independent code review). Etho's runtime pipeline uses only Meta models.
