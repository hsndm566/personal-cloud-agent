"""Logical model aliases with an injectable LiteLLM-backed completion client.

The application chooses a stable alias (for example ``agent-default``) while
deployment configuration supplies the provider-specific model identifier. This
keeps provider credentials and failover policy outside agent business logic.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ModelAlias(StrEnum):
    """Stable names used by agent code instead of provider model IDs."""

    FAST_CHEAP = "fast-cheap"
    AGENT_DEFAULT = "agent-default"
    REASONING_PREMIUM = "reasoning-premium"
    CODING_SPECIALIST = "coding-specialist"


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """A configured provider model and optional LiteLLM fallback models."""

    model: str
    fallbacks: tuple[str, ...] = ()


class CompletionClient(Protocol):
    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        fallbacks: Sequence[str] = (),
        **kwargs: Any,
    ) -> Any: ...


class AliasGateway:
    """Resolve aliases and delegate completions to an injected client."""

    def __init__(self, routes: Mapping[ModelAlias | str, ModelRoute], client: CompletionClient):
        self._routes = {ModelAlias(alias): route for alias, route in routes.items()}
        self._client = client

    def resolve(self, alias: ModelAlias | str) -> ModelRoute:
        try:
            return self._routes[ModelAlias(alias)]
        except (KeyError, ValueError) as exc:
            raise ValueError(f"No model route configured for alias: {alias}") from exc

    async def complete(
        self,
        alias: ModelAlias | str,
        messages: Sequence[Mapping[str, str]],
        **kwargs: Any,
    ) -> Any:
        route = self.resolve(alias)
        return await self._client.complete(
            model=route.model,
            messages=messages,
            fallbacks=route.fallbacks,
            **kwargs,
        )


class LiteLLMClient:
    """Thin lazy adapter around LiteLLM's async completion API."""

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        fallbacks: Sequence[str] = (),
        **kwargs: Any,
    ) -> Any:
        from litellm import acompletion

        request = dict(kwargs)
        if fallbacks:
            request["fallbacks"] = list(fallbacks)
        return await acompletion(model=model, messages=list(messages), **request)
