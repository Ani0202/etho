from __future__ import annotations


class GatewayError(RuntimeError):
    """Base class for gateway refusals and provider errors."""


class CapExceeded(GatewayError):
    """The call would push spend past a provider hard cap or a Meta tag budget."""


class PolicyError(GatewayError):
    """The call breaks the model/effort/data policy (missing effort, bad tag, contributor misuse)."""


class ProviderError(GatewayError):
    def __init__(self, status: int, body: str, provider: str, model: str):
        super().__init__(f"{provider}/{model} HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body
        self.provider = provider
        self.model = model
