"""Spend summaries for bin/spend."""

from __future__ import annotations

from collections import defaultdict

from .ledger import Ledger
from .prices import HARD_CAPS, META_TAG_BUDGETS


def summary(ledger: Ledger) -> str:
    rows = ledger.rows()
    by_p: dict[str, float] = defaultdict(float)
    by_t: dict[tuple[str, str], float] = defaultdict(float)
    by_m: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        c = float(r.get("cost_usd", 0.0))
        by_p[r["provider"]] += c
        by_t[(r["provider"], r.get("tag", "?"))] += c
        m = by_m[(r["provider"], r.get("model", "?"))]
        m[0] += 1
        m[1] += c
        m[2] += int(bool(r.get("cache_hit")))
    lines = ["Provider     spent       cap     used"]
    for p, cap in HARD_CAPS.items():
        s = by_p.get(p, 0.0)
        lines.append(f"{p:<10} ${s:>8.4f} ${cap:>7.2f}  {100 * s / cap:5.1f}%")
    lines.append("")
    lines.append("Meta tag     spent    budget    used")
    for t, b in META_TAG_BUDGETS.items():
        s = by_t.get(("meta", t), 0.0)
        lines.append(f"{t:<12} ${s:>7.4f} ${b:>7.2f}  {100 * s / b:5.1f}%")
    other = {k: v for k, v in by_t.items() if k[0] != "meta"}
    if other:
        lines.append("")
        lines.append("Other providers by tag")
        for (p, t), s in sorted(other.items()):
            lines.append(f"{p}/{t:<12} ${s:.4f}")
    lines.append("")
    lines.append("By model                                              calls  cache   cost")
    for (p, m), (n, c, hits) in sorted(by_m.items()):
        lines.append(f"{(p + '/' + m)[:52]:<52} {n:>6} {hits:>6}  ${c:.4f}")
    inflight = ledger._inflight()
    if inflight:
        lines.append(f"\n{len(inflight)} call(s) in flight, reserved ${sum(d['est'] for d in inflight):.4f}")
    lines.append(f"\nTotal: ${sum(by_p.values()):.4f} over {len(rows)} ledger rows")
    return "\n".join(lines)
