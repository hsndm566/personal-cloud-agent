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

    async def get_run(self, run_id: UUID) -> RunRecord:
        result = await self._connection.execute(
            """
            select id, owner_id, goal, thread_id, project_id, status, created_at
            from agent_control.runs
            where id = %s
            """,
            (run_id,),
        )
        record = await result.fetchone() if hasattr(result, "fetchone") else result
        if record is None:
            raise KeyError(f"run {run_id} not found")
        if not isinstance(record, dict):
            record = dict(record)
        return RunRecord(
            id=record["id"],
            owner_id=record["owner_id"],
            goal=record["goal"],
            thread_id=record["thread_id"],
            project_id=record["project_id"],
            status=record["status"],
            created_at=record["created_at"],
        )

    async def get_run_for_owner(self, run_id: UUID, owner_id: str) -> RunRecord:
        """Return a run only when it belongs to the requested owner."""
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        result = await self._connection.execute(
            """
            select id, owner_id, goal, thread_id, project_id, status, created_at
            from agent_control.runs
            where id = %s and owner_id = %s
            """,
            (run_id, owner_id),
        )
        record = await result.fetchone() if hasattr(result, "fetchone") else result
        if record is None:
            raise KeyError(f"run {run_id} not found")
        if not isinstance(record, dict):
            record = dict(record)
        return RunRecord(
            id=record["id"],
            owner_id=record["owner_id"],
            goal=record["goal"],
            thread_id=record["thread_id"],
            project_id=record["project_id"],
            status=record["status"],
            created_at=record["created_at"],
        )

    async def list_events(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return an owner's ordered execution timeline for one run."""
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        result = await self._connection.execute(
            """
            select id, event_type, state, payload, created_at
            from agent_control.run_events
            where run_id = %s and owner_id = %s
            order by id asc
            limit %s
            """,
            (run_id, owner_id, limit),
        )
        rows = await result.fetchall() if hasattr(result, "fetchall") else result
        if rows is None:
            return []
        return [row if isinstance(row, dict) else dict(row) for row in rows]

    async def set_run_status(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._connection.execute(
            "update agent_control.runs set status = %s where id = %s and owner_id = %s",
            (status, run_id, owner_id),
        )
        await self.append_event(
            run_id=run_id,
            owner_id=owner_id,
            event_type="status_change",
            state=status,
            payload=payload,
        )

    async def record_artifact(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        kind: str,
        content: dict[str, Any],
    ) -> None:
        await self._connection.execute(
            """
            insert into agent_control.artifacts (run_id, owner_id, kind, content)
            values (%s, %s, %s, %s)
            """,
            (run_id, owner_id, kind, Jsonb(content)),
        )
