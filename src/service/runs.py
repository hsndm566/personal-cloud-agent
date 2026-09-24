"""Authenticated durable run control-plane API."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from control_plane import ControlPlane, RunRecord
from core import settings

router = APIRouter(prefix="/runs", tags=["runs"])


class RunCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=12000)
    project_id: UUID | None = None
    thread_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("goal must not be blank")
        return normalized

    @field_validator("thread_id")
    @classmethod
    def normalize_thread_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("thread_id must not be blank")
        return normalized


class RunResponse(BaseModel):
    id: UUID
    owner_id: str
    goal: str
    thread_id: str
    project_id: UUID | None = None
    status: str
    retry_count: int = 0


class RunEventResponse(BaseModel):
    id: int
    event_type: str
    state: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class RunArtifactResponse(BaseModel):
    id: UUID
    kind: str
    uri: str | None = None
    content: dict[str, Any] | None = None
    created_at: datetime | None = None


class WorkerHealthResponse(BaseModel):
    available: bool
    worker_id: str | None = None
    agent_id: str | None = None
    last_seen_at: datetime | None = None
    age_seconds: float | None = None


def _owner_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Durable runs require an authenticated user identity",
        )
    return user_id


def _control_plane(request: Request) -> ControlPlane:
    control_plane = getattr(request.app.state, "control_plane", None)
    if control_plane is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Durable run control plane is not configured",
        )
    return control_plane


def _run_response(record: RunRecord) -> RunResponse:
    return RunResponse(
        id=record.id,
        owner_id=record.owner_id,
        goal=record.goal,
        thread_id=record.thread_id,
        project_id=record.project_id,
        status=record.status,
        retry_count=record.retry_count,
    )


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(body: RunCreateInput, request: Request) -> RunResponse:
    """Persist and enqueue one durable agent run for the authenticated owner."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    try:
        record = await control_plane.create_run(
            owner_id=owner_id,
            goal=body.goal,
            thread_id=body.thread_id or str(uuid4()),
            project_id=body.project_id,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        ) from exc
    await control_plane.append_event(
        run_id=record.id,
        owner_id=owner_id,
        event_type="run.created",
        state=record.status,
        payload={
            "thread_id": record.thread_id,
            "project_id": str(record.project_id) if record.project_id else None,
        },
    )
    try:
        await control_plane.enqueue_run(record)
    except Exception as exc:
        await control_plane.set_run_status(
            run_id=record.id,
            owner_id=owner_id,
            status="failed",
            payload={"phase": "enqueue", "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Run was persisted but could not be queued",
        ) from exc
    return _run_response(record)


@router.get("", response_model=list[RunResponse])
async def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    run_status: str | None = Query(default=None, alias="status"),
) -> list[RunResponse]:
    """List recent durable runs owned by the authenticated user."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    try:
        rows = await control_plane.list_runs(
            owner_id=owner_id,
            limit=limit,
            status=run_status,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return [_run_response(row) for row in rows]


@router.get("/system/worker-health", response_model=WorkerHealthResponse)
async def worker_health(request: Request) -> WorkerHealthResponse:
    """Return the freshest worker heartbeat without exposing worker metadata."""
    _owner_id(request)
    control_plane = _control_plane(request)
    heartbeat = await control_plane.latest_worker_heartbeat()
    if heartbeat is None:
        return WorkerHealthResponse(available=False)
    last_seen_at = heartbeat["last_seen_at"]
    age_seconds = None
    if isinstance(last_seen_at, datetime):
        reference = datetime.now(UTC)
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)
        age_seconds = max(0.0, (reference - last_seen_at).total_seconds())
    available = (
        age_seconds is not None
        and age_seconds <= settings.CONTROL_PLANE_HEARTBEAT_STALE_AFTER
    )
    return WorkerHealthResponse(
        available=available,
        worker_id=str(heartbeat["worker_id"]),
        agent_id=str(heartbeat["agent_id"]),
        last_seen_at=last_seen_at,
        age_seconds=age_seconds,
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: UUID, request: Request) -> RunResponse:
    """Return one run only when it belongs to the authenticated owner."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    try:
        record = await control_plane.get_run_for_owner(run_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    return _run_response(record)


@router.get("/{run_id}/events", response_model=list[RunEventResponse])
async def get_run_events(
    run_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RunEventResponse]:
    """Return the persisted execution timeline for one owned run."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    try:
        await control_plane.get_run_for_owner(run_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    rows = await control_plane.list_events(run_id=run_id, owner_id=owner_id, limit=limit)
    return [RunEventResponse(**row) for row in rows]


@router.get("/{run_id}/artifacts", response_model=list[RunArtifactResponse])
async def get_run_artifacts(
    run_id: UUID,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RunArtifactResponse]:
    """Return persisted artifacts for one owned run."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    try:
        await control_plane.get_run_for_owner(run_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    rows = await control_plane.list_artifacts(run_id=run_id, owner_id=owner_id, limit=limit)
    return [RunArtifactResponse(**row) for row in rows]


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(run_id: UUID, request: Request) -> RunResponse:
    """Cancel a queued, blocked, or interrupted run owned by the authenticated user."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    try:
        record = await control_plane.cancel_run(run_id=run_id, owner_id=owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _run_response(record)
