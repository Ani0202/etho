# Spend plans (jobs over $5)

## 2026-10-28: CalMS21 test run 1 (pre-approved by Aniket, up to $20 SAM)

**Conditions (Aniket, 2026-10-28):**
- EVAL_PLAN targets committed;
- Codex eval-plan gate done;
- prompts and thresholds frozen (`configs/frozen_test_v1.json`);
- nothing tuned on test.

| Item | Provider / tag | Basis | Estimate |
|---|---|---|---|
| SAM 3.1 tracking, 19 test videos, 10 fps, 60 s chunks | Meta / `eval-sam` | 87,374 samples; plus a few per-chunk 50-frame minimums | **$17.5-18.0** |
| Tier-1 strips, zero-shot arm | Fireworks | about 21 windows/video-min on holdout x 145.6 min, about 3,050 windows x $0.0030 | about $9 |
| Tier-1 strips, few-shot arm (new windows only; shared ones hit the cache) | Fireworks | about 40-60% new windows | about $4-6 |
| Rules | none | sandboxed local code | $0 |
| **Total** | | | **Meta about $18 (cap $20 approved), Fireworks about $15** |

**Budget headroom:**
- `eval-sam` after this run: about $6.9 + $18 = $25 of $40.
- Fireworks: about $9 spent so far + $15, of a $90 cap.

**Guards:**
- The gateway refuses anything past the caps.
- The SAM pass is run once and cached, so test run 2 (if any) reuses these tracks for $0 SAM.
