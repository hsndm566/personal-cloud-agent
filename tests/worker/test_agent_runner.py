from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from control_plane.supabase import RunRecord
from worker.agent_runner import build_agent_run_handler
from worker.dispatch import RunMessage


@pytest.mark.asyncio
async def test_queue_owner_must_match_persisted_owner():
    run = RunRecord(uuid4(), "owner", "goal", "thread")
    plane = AsyncMock()
    plane.get_run.return_value = run
    handler = build_agent_run_handler(plane, "deep-agent")
    with pytest.raises(PermissionError):
        await handler(RunMessage(1, run.id, "other-owner"))
    plane.set_run_status.assert_not_awaited()
    plane.record_artifact.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "interrupted", "cancelled"])
async def test_finalized_run_is_not_executed_again(status, monkeypatch):
    run = RunRecord(uuid4(), "owner", "goal", "thread", status=status)
    plane = AsyncMock()
    plane.get_run.return_value = run
    loader = AsyncMock()
    monkeypatch.setattr("worker.agent_runner.load_agent", loader)
    await build_agent_run_handler(plane, "deep-agent")(RunMessage(1, run.id, run.owner_id))
    loader.assert_not_awaited()
    plane.set_run_status.assert_not_awaited()
    plane.record_artifact.assert_not_awaited()
