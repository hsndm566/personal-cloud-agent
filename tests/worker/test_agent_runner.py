from unittest.mock import AsyncMock, patch
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
    plane.mark_run_running.assert_not_awaited()
    plane.record_artifact.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "interrupted", "cancelled"])
async def test_finalized_run_is_not_executed_again(status, monkeypatch):
    run = RunRecord(uuid4(), "owner", "goal", "thread", status=status)
    plane = AsyncMock()
    plane.get_run.return_value = run
    plane.mark_run_running.return_value = False
    loader = AsyncMock()
    monkeypatch.setattr("worker.agent_runner.load_agent", loader)
    await build_agent_run_handler(plane, "deep-agent")(RunMessage(1, run.id, run.owner_id))
    loader.assert_not_awaited()
    plane.mark_run_running.assert_awaited_once_with(run_id=run.id, owner_id=run.owner_id)
    plane.record_artifact.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_failure_is_left_for_queue_retry(monkeypatch):
    run = RunRecord(uuid4(), "owner", "goal", "thread")
    plane = AsyncMock()
    plane.get_run.return_value = run
    plane.mark_run_running.return_value = 1
    agent = AsyncMock()
    agent.store = None
    agent.ainvoke.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr("worker.agent_runner.load_agent", AsyncMock())
    monkeypatch.setattr("worker.agent_runner.get_agent", lambda _agent_id: agent)

    with patch("worker.agent_runner.settings") as mock_settings:
        mock_settings.CONTROL_PLANE_MAX_ATTEMPTS = 3
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await build_agent_run_handler(plane, "deep-agent")(
                RunMessage(1, run.id, run.owner_id)
            )

    plane.set_run_status.assert_not_awaited()
    plane.append_event.assert_awaited_once()
    payload = plane.append_event.call_args.kwargs["payload"]
    assert payload["attempt"] == 1
    assert payload["max_attempts"] == 3
    assert payload["error_type"] == "RuntimeError"
    assert "provider unavailable" not in payload.values()


@pytest.mark.asyncio
async def test_final_attempt_marks_failed_and_returns_for_archive(monkeypatch):
    run = RunRecord(uuid4(), "owner", "goal", "thread")
    plane = AsyncMock()
    plane.get_run.return_value = run
    plane.mark_run_running.return_value = 3
    agent = AsyncMock()
    agent.store = None
    agent.ainvoke.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr("worker.agent_runner.load_agent", AsyncMock())
    monkeypatch.setattr("worker.agent_runner.get_agent", lambda _agent_id: agent)

    with patch("worker.agent_runner.settings") as mock_settings:
        mock_settings.CONTROL_PLANE_MAX_ATTEMPTS = 3
        await build_agent_run_handler(plane, "deep-agent")(
            RunMessage(1, run.id, run.owner_id)
        )

    plane.set_run_status.assert_awaited_once()
    kwargs = plane.set_run_status.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["payload"]["attempt"] == 3
    assert kwargs["payload"]["reason"] == "max_attempts_exhausted"


@pytest.mark.asyncio
async def test_empty_agent_output_counts_toward_retry_limit(monkeypatch):
    run = RunRecord(uuid4(), "owner", "goal", "thread")
    plane = AsyncMock()
    plane.get_run.return_value = run
    plane.mark_run_running.return_value = 3
    agent = AsyncMock()
    agent.store = None
    agent.ainvoke.return_value = []
    monkeypatch.setattr("worker.agent_runner.load_agent", AsyncMock())
    monkeypatch.setattr("worker.agent_runner.get_agent", lambda _agent_id: agent)

    with patch("worker.agent_runner.settings") as mock_settings:
        mock_settings.CONTROL_PLANE_MAX_ATTEMPTS = 3
        await build_agent_run_handler(plane, "deep-agent")(
            RunMessage(1, run.id, run.owner_id)
        )

    plane.set_run_status.assert_awaited_once()
    kwargs = plane.set_run_status.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["payload"]["error_type"] == "ValueError"
    assert kwargs["payload"]["attempt"] == 3
