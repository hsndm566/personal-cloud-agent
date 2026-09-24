from datetime import UTC, datetime
from uuid import uuid4

import pytest

from control_plane import ControlPlane


class FakeCursor:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._many


class ReadConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_get_run_for_owner_filters_in_sql():
    run_id = uuid4()
    owner_id = "user_123"
    row = {
        "id": run_id,
        "owner_id": owner_id,
        "goal": "Inspect repo",
        "thread_id": "thread_123",
        "project_id": None,
        "status": "queued",
        "created_at": datetime.now(UTC),
    }
    connection = ReadConnection([FakeCursor(one=row)])
    plane = ControlPlane(connection)

    record = await plane.get_run_for_owner(run_id, owner_id)

    assert record.id == run_id
    assert record.owner_id == owner_id
    query, params = connection.calls[0]
    assert "where id = %s and owner_id = %s" in query
    assert params == (run_id, owner_id)


@pytest.mark.asyncio
async def test_get_run_for_owner_hides_missing_or_foreign_run():
    connection = ReadConnection([FakeCursor(one=None)])
    plane = ControlPlane(connection)

    with pytest.raises(KeyError):
        await plane.get_run_for_owner(uuid4(), "user_123")


@pytest.mark.asyncio
async def test_list_events_is_owner_scoped_and_ordered():
    run_id = uuid4()
    owner_id = "user_123"
    rows = [
        {
            "id": 1,
            "event_type": "run.created",
            "state": "queued",
            "payload": {},
            "created_at": datetime.now(UTC),
        }
    ]
    connection = ReadConnection([FakeCursor(many=rows)])
    plane = ControlPlane(connection)

    events = await plane.list_events(run_id=run_id, owner_id=owner_id, limit=25)

    assert events == rows
    query, params = connection.calls[0]
    assert "where run_id = %s and owner_id = %s" in query
    assert "order by id asc" in query
    assert params == (run_id, owner_id, 25)


@pytest.mark.asyncio
async def test_list_events_rejects_unbounded_limit():
    plane = ControlPlane(ReadConnection([]))

    with pytest.raises(ValueError, match="limit"):
        await plane.list_events(run_id=uuid4(), owner_id="user_123", limit=501)
