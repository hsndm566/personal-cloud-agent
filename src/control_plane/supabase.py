"""Minimal adapter for the private Supabase agent control plane."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

RUN_STATUSES = frozenset(
    {"queued", "running", "blocked", "completed", "failed", "cancelled", "interrupted"}
)


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
    retry_count: int = 0
    created_at: datetime | None = None


class ControlPlane:
    """Persist run ownership and dispatch through one database connection."""

    def __init__(self, connection: AsyncConnection, *, queue_name: str = "agent_runs"):
        if not queue_name.strip():
            raise ValueError("queue_name must not be empty")
        self._connection = connection
        self._queue_name = queue_name

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
        if project_id is not None:
            result = await self._connection.execute(
                """
                select id
                from agent_control.projects
                where id = %s and owner_id = %s
                """,
                (project_id, owner_id),
            )
            owned_project = await result.fetchone() if hasattr(result, "fetchone") else result
            if owned_project is None:
                raise KeyError(f"project {project_id} not found")

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
            (self._queue_name, Jsonb({"run_id": str(record.id), "owner_id": record.owner_id})),
        )

    @staticmethod
    def _run_record(record: Any) -> RunRecord:
        if not isinstance(record, dict):
            record = dict(record)
        return RunRecord(
            id=record["id"],
            owner_id=record["owner_id"],
            goal=record["goal"],
            thread_id=record["thread_id"],
            project_id=record["project_id"],
            status=record["status"],
            retry_count=int(record.get("retry_count", 0)),
            created_at=record["created_at"],
        )

    async def get_run(self, run_id: UUID) -> RunRecord:
        result = await self._connection.execute(
            """
            select id, owner_id, goal, thread_id, project_id, status, retry_count, created_at
            from agent_control.runs
            where id = %s
            """,
            (run_id,),
        )
        record = await result.fetchone() if hasattr(result, "fetchone") else result
        if record is None:
            raise KeyError(f"run {run_id} not found")
        return self._run_record(record)

    async def get_run_for_owner(self, run_id: UUID, owner_id: str) -> RunRecord:
        """Return a run only when it belongs to the requested owner."""
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        result = await self._connection.execute(
            """
            select id, owner_id, goal, thread_id, project_id, status, retry_count, created_at
            from agent_control.runs
            where id = %s and owner_id = %s
            """,
            (run_id, owner_id),
        )
        record = await result.fetchone() if hasattr(result, "fetchone") else result
        if record is None:
            raise KeyError(f"run {run_id} not found")
        return self._run_record(record)

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

    async def list_runs(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        status: str | None = None,
    ) -> list[RunRecord]:
        """Return recent runs for one owner, newest first."""
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        if status is not None and status not in RUN_STATUSES:
            raise ValueError(f"unsupported run status: {status}")
        if status is None:
            query = """
                select id, owner_id, goal, thread_id, project_id, status, retry_count, created_at
                from agent_control.runs
                where owner_id = %s
                order by created_at desc
                limit %s
            """
            params = (owner_id, limit)
        else:
            query = """
                select id, owner_id, goal, thread_id, project_id, status, retry_count, created_at
                from agent_control.runs
                where owner_id = %s and status = %s
                order by created_at desc
                limit %s
            """
            params = (owner_id, status, limit)
        result = await self._connection.execute(query, params)
        rows = await result.fetchall() if hasattr(result, "fetchall") else result
        return [self._run_record(row) for row in (rows or [])]

    async def list_artifacts(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return artifacts for one owned run."""
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        result = await self._connection.execute(
            """
            select id, kind, uri, content, created_at
            from agent_control.artifacts
            where run_id = %s and owner_id = %s
            order by created_at asc, id asc
            limit %s
            """,
            (run_id, owner_id, limit),
        )
        rows = await result.fetchall() if hasattr(result, "fetchall") else result
        return [row if isinstance(row, dict) else dict(row) for row in (rows or [])]

    async def cancel_run(self, *, run_id: UUID, owner_id: str) -> RunRecord:
        """Cancel a non-running owned run without racing an active worker."""
        run = await self.get_run_for_owner(run_id, owner_id)
        if run.status == "cancelled":
            return run
        if run.status not in {"queued", "blocked", "interrupted"}:
            raise RuntimeError(f"run cannot be cancelled from status {run.status}")
        result = await self._connection.execute(
            """
            update agent_control.runs
            set status = 'cancelled', completed_at = now(), updated_at = now()
            where id = %s and owner_id = %s and status in ('queued','blocked','interrupted')
            returning id, owner_id, goal, thread_id, project_id, status, retry_count, created_at
            """,
            (run_id, owner_id),
        )
        row = await result.fetchone() if hasattr(result, "fetchone") else result
        if row is None:
            raise RuntimeError("run status changed before cancellation could be applied")
        cancelled = self._run_record(row)
        await self.append_event(
            run_id=run_id,
            owner_id=owner_id,
            event_type="status_change",
            state="cancelled",
            payload={"reason": "user_cancelled"},
        )
        return cancelled

    async def upsert_worker_heartbeat(
        self,
        *,
        worker_id: str,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record liveness for one deployed background worker."""
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if not agent_id.strip():
            raise ValueError("agent_id must not be empty")
        await self._connection.execute(
            """
            insert into agent_control.worker_heartbeats
                (worker_id, agent_id, metadata, last_seen_at)
            values (%s, %s, %s, now())
            on conflict (worker_id) do update
            set agent_id = excluded.agent_id,
                metadata = excluded.metadata,
                last_seen_at = now()
            """,
            (worker_id, agent_id, Jsonb(metadata or {})),
        )

    async def latest_worker_heartbeat(self) -> dict[str, Any] | None:
        """Return the freshest worker heartbeat."""
        result = await self._connection.execute(
            """
            select worker_id, agent_id, metadata, started_at, last_seen_at
            from agent_control.worker_heartbeats
            order by last_seen_at desc
            limit 1
            """
        )
        row = await result.fetchone() if hasattr(result, "fetchone") else result
        if row is None:
            return None
        return row if isinstance(row, dict) else dict(row)

    async def mark_run_running(self, *, run_id: UUID, owner_id: str) -> int:
        """Atomically claim a queued/retry run and return its persisted attempt number."""
        result = await self._connection.execute(
            """
            update agent_control.runs
            set status = 'running',
                retry_count = retry_count + 1,
                started_at = coalesce(started_at, now()),
                updated_at = now()
            where id = %s
              and owner_id = %s
              and status in ('queued','running')
            returning retry_count
            """,
            (run_id, owner_id),
        )
        claimed = await result.fetchone() if hasattr(result, "fetchone") else result
        if claimed is None:
            return 0
        attempt = int(claimed["retry_count"] if isinstance(claimed, dict) else claimed[0])
        await self.append_event(
            run_id=run_id,
            owner_id=owner_id,
            event_type="status_change",
            state="running",
            payload={"phase": "worker_claim", "attempt": attempt},
        )
        return attempt

    async def set_run_status(
        self,
        *,
        run_id: UUID,
        owner_id: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"unsupported run status: {status}")
        result = await self._connection.execute(
            """
            update agent_control.runs
            set status = %s,
                started_at = case
                    when %s = 'running' and started_at is null then now()
                    else started_at
                end,
                completed_at = case
                    when %s in ('completed','failed','cancelled','interrupted') then now()
                    else completed_at
                end,
                updated_at = now()
            where id = %s and owner_id = %s
            returning id
            """,
            (status, status, status, run_id, owner_id),
        )
        updated = await result.fetchone() if hasattr(result, "fetchone") else result
        if updated is None:
            raise KeyError(f"run {run_id} not found")
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
