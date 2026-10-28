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

## Data and licenses

- **CalMS21** (Caltech Mouse Social Interactions), CC BY 4.0. Sun et al., "The Multi-Agent Behavior
  Dataset: Mouse Dyadic Social Interactions", arXiv:2104.02710 (2021).
  https://data.caltech.edu/records/s0vdx-0k302. Raw data is never committed; fetch it with the
  scripts in `etho/datasets/` (individual videos are range-read from the 28 GB archive).
- **SAM 3.1** output is decoded with a numba port of Meta's `meta-sam-parser` codec
  (`etho/tracking/mask_codec.py`, `sam_lines.py`). It is derived from SAM Materials and redistributed
  under the SAM License; a copy is in `third_party/meta-sam-parser/LICENSE`. This project uses SAM
  Materials (Segment Anything Model 3.1, Meta).

## How this was built

Developed with AI coding tools: Claude Code (lead engineer and subagents) and OpenAI Codex CLI
(independent code review). Etho's runtime pipeline uses only Meta models. The fast SAM mask decoder
is an AI-assisted port (Claude Code) of Meta's open reference decoder, verified bit-exact against it.
