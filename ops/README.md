# ops/: runtime state (not in the repo)

The gateway and the hosted app write their state here; everything in this folder except this file is git-ignored.

- `spend.jsonl`: the spend ledger. Every model call is logged here, and the spend caps are computed from it.
- `inflight/`, `.spend.lock`: cap reservations for calls in flight.
- `uptime.jsonl`, `uptime.cron.log`, `funnel*.json*`, `funnel-keepalive.log`, `public-url`, `notify.jsonl`: hosting
  checks and notifications (docs/HOSTING.md).
- `app-kill`, `app-readonly`, `funnel-off`: operator markers (docs/HOSTING.md).

The ledger as it stood at the hackathon deadline is in `docs/history/spend.jsonl`.
