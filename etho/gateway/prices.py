"""Prices, caps, and budgets from the project brief (CLAUDE.md, in tag hackathon-submission). Never raise caps here
without Aniket's approval."""

from __future__ import annotations

from dataclasses import dataclass

PER_M = 1_000_000


@dataclass(frozen=True)
class TokenPrice:
    input: float  # USD per million input tokens
    output: float  # USD per million output tokens (reasoning tokens bill as output)
    cached_input: float | None = None  # USD per million cached input tokens, if discounted


# provider -> model -> price
TOKEN_PRICES: dict[str, dict[str, TokenPrice]] = {
    "meta": {
        "muse-spark-1.3": TokenPrice(1.25, 4.25),
        "muse-spark-1.3-contributor": TokenPrice(0.10, 0.20),
        "muse-spark-1.2": TokenPrice(1.25, 4.25),
    },
    "baseten": {
        "meta-models/muse-spark-open-1026": TokenPrice(1.25, 4.25, 0.15),
    },
    "fireworks": {
        "accounts/fireworks/models/muse-spark-open": TokenPrice(1.25, 4.25, 0.15),
        # Serverless inference retired Sep 25 2026 (expect 404). Priced like Together as a ceiling.
        "accounts/fireworks/models/muse-glimmer-30b": TokenPrice(0.35, 1.50),
    },
    "together": {
        "meta-models/Muse-Glimmer-30B": TokenPrice(0.35, 1.50),
    },
}

# SAM 3.1 (Meta)
SAM_MODEL = "sam-3.1"
SAM_VIDEO_PER_FRAME = 0.20 / 1000
SAM_IMAGE_PER_IMAGE = 2.50 / 1000
SAM_MIN_VIDEO_FRAMES = 50

# Hard caps per provider, USD (CLAUDE.md "Budget caps").
HARD_CAPS: dict[str, float] = {
    "meta": 140.0,
    "baseten": 90.0,
    "fireworks": 90.0,
    "together": 5.0,  # Aniket's own money; small until he says otherwise (docs/history/DECISIONS.md)
}

# Soft sub-budgets for Meta spend, by tag. Calls are refused once a tag is exhausted. The sum is still 140, the Meta
# hard cap. Moved on Nov 3 (Aniket; DECISIONS 2026-11-04): evaluation is finished, so eval-llm ($0.07 spent of 30) gives
# $29 and eval-sam ($24.58 spent of 40) gives $15 to demo-reserve, for open live uploads. No provider cap changed.
META_TAG_BUDGETS: dict[str, float] = {
    "dev": 15.0,
    "eval-sam": 25.0,
    "eval-llm": 1.0,
    "ablation": 20.0,
    "demo-reserve": 79.0,
}
TAGS = tuple(META_TAG_BUDGETS)

# demo-reserve is untouchable until Nov 3 00:00 ET (= 05:00 UTC; EST is UTC-5 after Nov 1).
DEMO_RESERVE_OPENS_UTC = "2026-11-03T05:00:00+00:00"

ALERT_FRACTIONS = (0.5, 0.8)


def token_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int,
               cached_tokens: int = 0) -> float:
    """USD cost from usage. completion_tokens already includes reasoning tokens (OpenAI convention)."""
    p = price_for(provider, model)
    cached = min(cached_tokens, prompt_tokens) if p.cached_input is not None else 0
    uncached = prompt_tokens - cached
    cost = uncached * p.input + completion_tokens * p.output
    if cached:
        cost += cached * p.cached_input  # type: ignore[operator]
    return cost / PER_M


def price_for(provider: str, model: str) -> TokenPrice:
    try:
        return TOKEN_PRICES[provider][model]
    except KeyError:
        raise ValueError(f"no price for {provider}/{model}; add it to etho/gateway/prices.py") from None


def sam_video_cost(frames: int) -> float:
    return max(frames, SAM_MIN_VIDEO_FRAMES) * SAM_VIDEO_PER_FRAME


def sam_image_cost(images: int = 1) -> float:
    return images * SAM_IMAGE_PER_IMAGE
