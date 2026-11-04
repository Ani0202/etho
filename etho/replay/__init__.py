"""Offline replay of recorded Meta model responses (`make demo`).

A separate component from `etho/gateway/`: it never builds a provider client, never touches the live gateway's
code path, and plugs into the web app the way the tests' fake gateway does (`create_app(gateway_factory=...)`).
See README "Try it in 1 minute (no keys)" and docs/DECISIONS.md.
"""

LABEL = "Replay of recorded Meta model responses; for live results, add your own Meta Model API key."

__all__ = ["LABEL"]
