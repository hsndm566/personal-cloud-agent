"""Standalone pgmq worker entrypoint."""

import asyncio
import logging
import signal
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row

from control_plane.supabase import ControlPlane
from core import settings
from worker.agent_runner import build_agent_run_handler
from worker.dispatch import DurableRunWorker
from worker.pgmq_client import PgmqQueueClient

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if settings.CONTROL_PLANE_DATABASE_URL is None:
        raise RuntimeError("CONTROL_PLANE_DATABASE_URL must be set for the worker process")
    dsn = settings.CONTROL_PLANE_DATABASE_URL.get_secret_value()
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        control_plane = ControlPlane(cast(Any, conn))
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
        logger.info("worker started, polling queue=%s", settings.CONTROL_PLANE_QUEUE_NAME)
        await worker.run_forever(stop_event)
        logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
