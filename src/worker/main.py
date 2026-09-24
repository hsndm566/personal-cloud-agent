"""Standalone pgmq worker entrypoint."""

import asyncio
import logging
import os
import signal
from typing import Any, cast
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from control_plane.supabase import ControlPlane
from core import settings
from worker.agent_runner import build_agent_run_handler
from worker.dispatch import DurableRunWorker
from worker.persistence import initialized_worker_agent
from worker.pgmq_client import PgmqQueueClient

logger = logging.getLogger(__name__)


async def _heartbeat_loop(
    control_plane: ControlPlane,
    *,
    worker_id: str,
    agent_id: str,
    stop_event: asyncio.Event,
) -> None:
    interval = settings.CONTROL_PLANE_HEARTBEAT_INTERVAL
    if interval <= 0:
        raise ValueError("CONTROL_PLANE_HEARTBEAT_INTERVAL must be positive")
    while not stop_event.is_set():
        try:
            await control_plane.upsert_worker_heartbeat(
                worker_id=worker_id,
                agent_id=agent_id,
                metadata={"pid": os.getpid()},
            )
        except Exception:
            logger.exception("worker heartbeat update failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if settings.CONTROL_PLANE_DATABASE_URL is None:
        raise RuntimeError("CONTROL_PLANE_DATABASE_URL must be set for the worker process")
    dsn = settings.CONTROL_PLANE_DATABASE_URL.get_secret_value()
    # Commit each queue lease before executing the agent. Holding a transaction
    # open while polling hides leases and run updates from other processes.
    async with (
        await psycopg.AsyncConnection.connect(
            dsn, row_factory=dict_row, autocommit=True
        ) as conn,
        initialized_worker_agent("deep-agent"),
    ):
        control_plane = ControlPlane(
            cast(Any, conn),
            queue_name=settings.CONTROL_PLANE_QUEUE_NAME,
        )
        queue_client = PgmqQueueClient(cast(Any, conn))
        worker = DurableRunWorker(
            queue=queue_client,
            handler=build_agent_run_handler(control_plane, agent_id="deep-agent"),
            queue_name=settings.CONTROL_PLANE_QUEUE_NAME,
            visibility_timeout=settings.CONTROL_PLANE_VISIBILITY_TIMEOUT,
            poll_interval=settings.CONTROL_PLANE_POLL_INTERVAL,
        )
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        worker_id = (
            settings.CONTROL_PLANE_WORKER_ID
            or os.getenv("HOSTNAME")
            or f"worker-{uuid4()}"
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(
                control_plane,
                worker_id=worker_id,
                agent_id="deep-agent",
                stop_event=stop_event,
            )
        )
        logger.info(
            "worker started, id=%s polling queue=%s",
            worker_id,
            settings.CONTROL_PLANE_QUEUE_NAME,
        )
        try:
            await worker.run_forever(stop_event)
        finally:
            stop_event.set()
            await heartbeat_task
        logger.info("worker stopped, id=%s", worker_id)


if __name__ == "__main__":
    asyncio.run(main())
