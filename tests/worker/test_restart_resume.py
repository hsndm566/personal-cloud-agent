import asyncio
import time
from typing import Any
from uuid import uuid4

import pytest

from worker.dispatch import DurableRunWorker, RunMessage


class FakeVisibilityQueue:
    def __init__(self):
        self._next_id = 1
        self._messages: dict[int, dict[str, Any]] = {}
        self._visible_at: dict[int, float] = {}
        self._archived: set[int] = set()

    def send(self, message: dict[str, Any]) -> int:
        msg_id = self._next_id
        self._next_id += 1
        self._messages[msg_id] = message
        self._visible_at[msg_id] = 0.0
        return msg_id

    async def read(self, queue: str, visibility_timeout: int, quantity: int) -> list[dict[str, Any]]:
        now = time.monotonic()
        rows = []
        for msg_id, message in self._messages.items():
            if msg_id in self._archived or self._visible_at[msg_id] > now:
                continue
            self._visible_at[msg_id] = now + visibility_timeout
            rows.append({"msg_id": msg_id, "message": message})
            if len(rows) >= quantity:
                break
        return rows

    async def archive(self, queue: str, msg_id: int) -> None:
        self._archived.add(msg_id)

    def expire_all_leases(self) -> None:
        for msg_id in self._visible_at:
            self._visible_at[msg_id] = 0.0


@pytest.mark.asyncio
async def test_crashed_worker_message_is_retried_by_second_worker():
    queue = FakeVisibilityQueue()
    run_id = uuid4()
    queue.send({"run_id": str(run_id), "owner_id": "owner-1"})
    attempts: list[int] = []

    async def crashing_handler(message: RunMessage) -> None:
        attempts.append(1)
        raise RuntimeError("simulated worker crash mid-handler")

    worker_a = DurableRunWorker(queue=queue, handler=crashing_handler, visibility_timeout=30)
    with pytest.raises(RuntimeError):
        await worker_a.run_once()
    assert len(attempts) == 1
    assert queue._archived == set()
    assert await worker_a.run_once() is False

    queue.expire_all_leases()
    completed: list[RunMessage] = []

    async def succeeding_handler(message: RunMessage) -> None:
        completed.append(message)

    worker_b = DurableRunWorker(queue=queue, handler=succeeding_handler, visibility_timeout=30)
    assert await worker_b.run_once() is True
    assert len(completed) == 1
    assert completed[0].run_id == run_id
    assert completed[0].owner_id == "owner-1"
    assert queue._archived


@pytest.mark.asyncio
async def test_run_forever_stops_cleanly_on_stop_event():
    queue = FakeVisibilityQueue()
    stop_event = asyncio.Event()
    calls = 0

    async def noop_handler(message: RunMessage) -> None:
        nonlocal calls
        calls += 1

    worker = DurableRunWorker(queue=queue, handler=noop_handler, poll_interval=0.01)

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        stop_event.set()

    await asyncio.gather(worker.run_forever(stop_event), stop_soon())
    assert calls == 0
