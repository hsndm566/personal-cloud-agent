"""Optional Mem0 OSS adapter for durable semantic user and project memory."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any, Protocol, cast


class MemoryClient(Protocol):
    def add(self, messages: Any, **kwargs: Any) -> Any: ...

    def search(self, query: str, **kwargs: Any) -> Any: ...


class Mem0Memory:
    """Keep Mem0 behind an async, scoped interface while Supabase remains authoritative."""

    def __init__(self, client: MemoryClient):
        self._client = client

    async def add(
        self,
        messages: Sequence[dict[str, str]],
        *,
        user_id: str,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        return await asyncio.to_thread(
            self._client.add,
            list(messages),
            user_id=_required_scope(user_id, "user_id"),
            agent_id=_required_scope(project_id, "project_id") if project_id else None,
            metadata=metadata or {},
        )

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        project_id: str | None = None,
        limit: int = 10,
    ) -> Any:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        return await asyncio.to_thread(
            self._client.search,
            query,
            user_id=_required_scope(user_id, "user_id"),
            agent_id=_required_scope(project_id, "project_id") if project_id else None,
            limit=limit,
        )


def client_from_config(config_json: str) -> Mem0Memory:
    """Build a Mem0 client from explicit JSON configuration without importing it at module load."""
    from mem0 import Memory

    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as exc:
        raise ValueError("MEM0_CONFIG_JSON must be valid JSON") from exc
    return Mem0Memory(cast(Any, Memory.from_config(config)))


def _required_scope(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized
