import asyncio
from uuid import uuid4

import pytest

from worker import DurableRunWorker


class FakeQueue:
    def __init__(self, rows):
        self.rows = list(rows)
        self.archived: list[int] = []
        self.reads: list[tuple[str, int, int]] = []
        self.extended: list[tuple[str, int, int]] = []
        self.renewed = asyncio.Event()

    async def read(self, queue, visibility_timeout, quantity):
        self.reads.append((queue, visibility_timeout, quantity))
        return self.rows[:quantity]

    async def archive(self, queue, msg_id):
        self.archived.append(msg_id)

    async def extend_visibility(self, queue, msg_id, visibility_timeout):
        self.extended.append((queue, msg_id, visibility_timeout))
        self.renewed.set()


@pytest.mark.asyncio
async def test_worker_archives_only_after_successful_handler():
    run_id = uuid4()
    queue = FakeQueue([{"msg_id": 7, "message": {"run_id": str(run_id), "owner_id": "user-1"}}])
    received = []

    async def record(message):
        received.append(message)

    worker = DurableRunWorker(queue, record)
    assert await worker.run_once() is True
    assert received[0].run_id == run_id
    assert received[0].owner_id == "user-1"
    assert queue.archived == [7]
    assert queue.reads == [("agent_runs", 60, 1)]


@pytest.mark.asyncio
async def test_worker_leaves_message_for_retry_when_handler_fails():
    queue = FakeQueue([{"msg_id": 9, "message": {"run_id": str(uuid4()), "owner_id": "user-2"}}])

    async def fail(_message):
        raise RuntimeError("transient")

    worker = DurableRunWorker(queue, fail, visibility_timeout=30)
    with pytest.raises(RuntimeError, match="transient"):
        await worker.run_once()
    assert queue.archived == []


@pytest.mark.asyncio
async def test_worker_renews_visibility_during_long_handler():
    run_id = uuid4()
    queue = FakeQueue([{"msg_id": 11, "message": {"run_id": str(run_id), "owner_id": "user-3"}}])

    async def wait_for_renewal(_message):
        await asyncio.wait_for(queue.renewed.wait(), timeout=1.5)

    worker = DurableRunWorker(queue, wait_for_renewal, visibility_timeout=1)
    assert await worker.run_once() is True
    assert queue.extended == [("agent_runs", 11, 1)]
    assert queue.archived == [11]


@pytest.mark.asyncio
async def test_worker_archives_malformed_message_without_calling_handler():
    queue = FakeQueue([{"msg_id": 13, "message": {"owner_id": "user-4"}}])
    received = []

    async def handler(message):
        received.append(message)

    worker = DurableRunWorker(queue, handler)
    assert await worker.run_once() is True
    assert received == []
    assert queue.archived == [13]
    assert queue.extended == []
