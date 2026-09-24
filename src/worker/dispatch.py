"""Restart-safe pgmq consumer boundary for agent runs.

Messages are leased with pgmq's visibility timeout and archived only after the
handler succeeds. A worker crash or handler exception therefore leaves the run
available for a later worker to retry.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


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

    async def extend_visibility(
        self, queue: str, msg_id: int, visibility_timeout: int
    ) -> None: ...


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

    async def _renew_visibility(self, msg_id: int, stop_event: asyncio.Event) -> None:
        renew_after = max(0.25, self._visibility_timeout / 2)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=renew_after)
                return
            except TimeoutError:
                try:
                    await self._queue.extend_visibility(
                        self._queue_name,
                        msg_id,
                        self._visibility_timeout,
                    )
                except Exception:
                    logger.exception(
                        "failed to renew queue lease for msg_id=%s queue=%s",
                        msg_id,
                        self._queue_name,
                    )
                    return

    async def run_once(self) -> bool:
        rows = await self._queue.read(self._queue_name, self._visibility_timeout, 1)
        if not rows:
            return False
        row = rows[0]
        msg_id = int(row["msg_id"])
        try:
            message = RunMessage.from_payload(msg_id, dict(row["message"]))
        except ValueError:
            logger.exception(
                "archiving malformed queue message msg_id=%s queue=%s",
                msg_id,
                self._queue_name,
            )
            await self._queue.archive(self._queue_name, msg_id)
            return True
        lease_stop = asyncio.Event()
        lease_task = asyncio.create_task(
            self._renew_visibility(message.msg_id, lease_stop)
        )
        try:
            await self._handler(message)
        finally:
            lease_stop.set()
            await lease_task
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
