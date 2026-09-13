"""Minimal adapter for the private Supabase agent control plane."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb


class AsyncConnection(Protocol):
    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...


@dataclass(frozen=True)
class RunRecord:
    id: UUID
    owner_id: str
    goal: str
    thread_id: str
    project_id: UUID | None = None
    status: str = "queued"
    created_at: datetime | None = None


class ControlPlane:
    """Persist run ownership and dispatch through one database connection."""

    def __init__(self, connection: AsyncConnection):
        self._connection = connection

    async def create_run(
        self,
        *,
        owner_id: str,
        goal: str,
        thread_id: str,
        project_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> RunRecord:
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not goal.strip():
            raise ValueError("goal must not be empty")
        if not thread_id.strip():
            raise ValueError("thread_id must not be empty")

        record = RunRecord(
            id=run_id or uuid4(),
            owner_id=owner_id,
            goal=goal,
            thread_id=thread_id,
            project_id=project_id,
        )
        await self._connection.execute(
            """
            insert into agent_control.runs
                (id, owner_id, goal, thread_id, project_id)
            values (%s, %s, %s, %s, %s)
            """,
            (record.id, record.owner_id, record.goal, record.thread_id, record.project_id),
        )
        return record

    async def append_event(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        event_type: str,
        state: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._connection.execute(
            """
            insert into agent_control.run_events
                (run_id, owner_id, event_type, state, payload)
            values (%s, %s, %s, %s, %s)
            """,
            (run_id, owner_id, event_type, state, Jsonb(payload or {})),
        )

    async def enqueue_run(self, record: RunRecord) -> None:
        await self._connection.execute(
            "select pgmq.send(%s, %s)",
            ("agent_runs", Jsonb({"run_id": str(record.id), "owner_id": record.owner_id})),
        )
