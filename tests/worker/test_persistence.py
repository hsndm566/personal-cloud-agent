from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from worker.persistence import initialized_worker_agent


@pytest.mark.asyncio
async def test_persistence_stays_attached_until_worker_exits(monkeypatch):
    saver, store = SimpleNamespace(setup=AsyncMock()), SimpleNamespace(setup=AsyncMock())
    closed = []

    @asynccontextmanager
    async def database():
        try:
            yield saver
        finally:
            closed.append("database")

    @asynccontextmanager
    async def memory():
        try:
            yield store
        finally:
            closed.append("store")

    graph = SimpleNamespace(checkpointer=None, store=None)
    monkeypatch.setattr("worker.persistence.initialize_database", database)
    monkeypatch.setattr("worker.persistence.initialize_store", memory)
    monkeypatch.setattr("worker.persistence.load_agent", AsyncMock())
    monkeypatch.setattr("worker.persistence.get_agent", lambda _: graph)
    with pytest.raises(RuntimeError, match="worker crash"):
        async with initialized_worker_agent("deep-agent") as agent:
            assert agent.checkpointer is saver
            assert agent.store is store
            assert closed == []
            saver.setup.assert_awaited_once()
            store.setup.assert_awaited_once()
            raise RuntimeError("worker crash")
    assert graph.checkpointer is None
    assert graph.store is None
    assert set(closed) == {"database", "store"}
