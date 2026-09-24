import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from worker.main import _heartbeat_loop


@pytest.mark.asyncio
async def test_heartbeat_loop_publishes_immediately_and_stops():
    plane = AsyncMock()
    stop_event = asyncio.Event()

    async def stop_after_first_heartbeat(**kwargs):
        stop_event.set()

    plane.upsert_worker_heartbeat.side_effect = stop_after_first_heartbeat

    with patch("worker.main.settings") as mock_settings:
        mock_settings.CONTROL_PLANE_HEARTBEAT_INTERVAL = 15.0
        await _heartbeat_loop(
            plane,
            worker_id="worker-1",
            agent_id="deep-agent",
            stop_event=stop_event,
        )

    plane.upsert_worker_heartbeat.assert_awaited_once()
    kwargs = plane.upsert_worker_heartbeat.call_args.kwargs
    assert kwargs["worker_id"] == "worker-1"
    assert kwargs["agent_id"] == "deep-agent"
    assert "pid" in kwargs["metadata"]


@pytest.mark.asyncio
async def test_heartbeat_loop_rejects_non_positive_interval():
    plane = AsyncMock()
    stop_event = asyncio.Event()

    with patch("worker.main.settings") as mock_settings:
        mock_settings.CONTROL_PLANE_HEARTBEAT_INTERVAL = 0
        with pytest.raises(ValueError, match="must be positive"):
            await _heartbeat_loop(
                plane,
                worker_id="worker-1",
                agent_id="deep-agent",
                stop_event=stop_event,
            )

    plane.upsert_worker_heartbeat.assert_not_awaited()
