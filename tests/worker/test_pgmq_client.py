import pytest

from worker.pgmq_client import PgmqQueueClient


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        return None


@pytest.mark.asyncio
async def test_extend_visibility_uses_pgmq_set_vt():
    connection = FakeConnection()
    queue = PgmqQueueClient(connection)

    await queue.extend_visibility("agent_runs", 42, 60)

    query, params = connection.calls[0]
    assert "pgmq.set_vt" in query
    assert params == ("agent_runs", 42, 60)
