"""Restart-safe pgmq consumer boundary for agent runs.

Messages are leased with pgmq's visibility timeout and archived only after the
handler succeeds. A worker crash or handler exception therefore leaves the run
available for a later worker to retry.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RunMessage:
    msg_id: int
    run_id: UUID
    owner_id: str

    @classmethod
    def from_payload(cls, msg_id: int, payload: dict[str, Any]) -> "RunMessage":
        try:
            return cls(msg_id, UUID(str(payload["run_id"])), str(payload["owner_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("queue message must contain a valid run_id and owner_id") from exc


class QueueClient(Protocol):
    async def read(self, queue: str, visibility_timeout: int, quantity: int) -> list[dict[str, Any]]: ...

    async def archive(self, queue: str, msg_id: int) -> None: ...


RunHandler = Callable[[RunMessage], Awaitable[None]]


class DurableRunWorker:
    """Consume at most one run per lease and preserve failed messages for retry."""

    def __init__(
        self,
        queue: QueueClient,
        handler: RunHandler,
        *,
        queue_name: str = "agent_runs",
        visibility_timeout: int = 60,
        poll_interval: float = 1.0,
    ):
        if visibility_timeout < 1:
            raise ValueError("visibility_timeout must be positive")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        self._queue = queue
        self._handler = handler
        self._queue_name = queue_name
        self._visibility_timeout = visibility_timeout
        self._poll_interval = poll_interval

    async def run_once(self) -> bool:
        rows = await self._queue.read(self._queue_name, self._visibility_timeout, 1)
        if not rows:
            return False
        row = rows[0]
        message = RunMessage.from_payload(int(row["msg_id"]), dict(row["message"]))
        await self._handler(message)
        await self._queue.archive(self._queue_name, message.msg_id)
        return True

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            handled = await self.run_once()
            if not handled:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass
