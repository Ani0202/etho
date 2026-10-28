# Decisions

One line per reversible decision; ADRs (context, options, choice, evidence, reversal) for
architectural or methodological ones. Newest at the bottom.

- 2026-10-28: Gateway uses httpx directly (no provider SDKs): one retry/cost/cache path for all four OpenAI-compatible providers.
- 2026-10-28: Cap enforcement reserves each call's worst-case cost (in-flight files under `ops/inflight/`, flock-protected) and settles with actual usage, so parallel workers can't jointly overshoot a cap. Stale reservations expire after 2h.
- 2026-10-28: Meta tag budgets are enforced as refusals at 100% (moving money between tags needs an ADR anyway); 80% crossings and 50%/80% provider-cap crossings are written to `ops/alerts.jsonl` for escalation.
- 2026-10-28: `demo-reserve` tag is refused by the gateway before 2026-11-03 05:00 UTC (Nov 3 00:00 ET).
- 2026-10-28: Together AI hard cap set to $5 (it's Aniket's own money; CLAUDE.md gives no cap). Disabled anyway unless TOGETHER_API_KEY is set.
- 2026-10-28: `-contributor` models require `public_data=True` at the call site; the gateway refuses otherwise.
- 2026-10-28: Cost for calls that return no usage is booked at the worst-case estimate, not $0. A SAM stream that fails mid-way is also booked at its estimate.
- 2026-10-28: Use Meta's `meta-sam-parser` (PyPI, SAM License: use/modify/redistribute allowed, must acknowledge SAM in publications) to decode SAM 3.1 masks instead of reimplementing the codec.
- 2026-10-28: Local videos sent to SAM are uploaded via the Files API (cached by sha256) rather than inlined as base64.
