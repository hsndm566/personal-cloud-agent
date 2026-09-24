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


@pytest.mark.asyncio
async def test_create_run_rejects_project_owned_by_another_user():
    project_id = uuid4()
    connection = ReadConnection([FakeCursor(one=None)])
    plane = ControlPlane(connection)

    with pytest.raises(KeyError, match="project"):
        await plane.create_run(
            owner_id="user_123",
            goal="Inspect repo",
            thread_id="thread_123",
            project_id=project_id,
        )

    assert len(connection.calls) == 1
    query, params = connection.calls[0]
    assert "agent_control.projects" in query
    assert params == (project_id, "user_123")


@pytest.mark.asyncio
async def test_enqueue_uses_configured_queue_name():
    connection = ReadConnection([None])
    plane = ControlPlane(connection, queue_name="personal_agent_runs")
    run = await plane.create_run(
        owner_id="user_123",
        goal="Inspect repo",
        thread_id="thread_123",
    )
    await plane.enqueue_run(run)

    _, params = connection.calls[-1]
    assert params[0] == "personal_agent_runs"


@pytest.mark.asyncio
async def test_status_update_does_not_append_event_for_foreign_owner():
    run_id = uuid4()
    connection = ReadConnection([FakeCursor(one=None)])
    plane = ControlPlane(connection)

    with pytest.raises(KeyError):
        await plane.set_run_status(
            run_id=run_id,
            owner_id="wrong-owner",
            status="completed",
        )

    assert len(connection.calls) == 1


@pytest.mark.asyncio
async def test_status_update_records_timestamped_transition():
    run_id = uuid4()
    connection = ReadConnection([FakeCursor(one={"id": run_id}), None])
    plane = ControlPlane(connection)

    await plane.set_run_status(
        run_id=run_id,
        owner_id="user_123",
        status="completed",
    )

    update_query, update_params = connection.calls[0]
    assert "started_at" in update_query
    assert "completed_at" in update_query
    assert "returning id" in update_query
    assert update_params[-2:] == (run_id, "user_123")
    assert "agent_control.run_events" in connection.calls[1][0]
