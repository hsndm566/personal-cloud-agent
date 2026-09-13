from uuid import uuid4

import pytest

from control_plane import ControlPlane


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((query, params))


@pytest.mark.asyncio
async def test_create_event_and_enqueue_use_private_schema_and_queue() -> None:
    connection = FakeConnection()
    control_plane = ControlPlane(connection)
    run = await control_plane.create_run(
        owner_id="user_123",
        goal="Inspect the repository",
        thread_id="thread_123",
        run_id=uuid4(),
    )
    await control_plane.append_event(
        run_id=run.id,
        owner_id=run.owner_id,
        event_type="run.created",
        payload={"goal": run.goal},
    )
    await control_plane.enqueue_run(run)

    assert len(connection.calls) == 3
    assert "agent_control.runs" in connection.calls[0][0]
    assert "agent_control.run_events" in connection.calls[1][0]
    assert "pgmq.send" in connection.calls[2][0]
    assert connection.calls[2][1][0] == "agent_runs"


@pytest.mark.asyncio
async def test_create_run_rejects_empty_identity_fields() -> None:
    control_plane = ControlPlane(FakeConnection())

    with pytest.raises(ValueError, match="owner_id"):
        await control_plane.create_run(owner_id=" ", goal="goal", thread_id="thread")
