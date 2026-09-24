"""Authenticated durable run control-plane API."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from control_plane import ControlPlane, RunRecord

router = APIRouter(prefix="/runs", tags=["runs"])


class RunCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=12000)
    project_id: UUID | None = None
    thread_id: str | None = Field(default=None, min_length=1, max_length=255)


class RunResponse(BaseModel):
    id: UUID
    owner_id: str
    goal: str
    thread_id: str
    project_id: UUID | None = None
    status: str


class RunEventResponse(BaseModel):
    id: int
    event_type: str
    state: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


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
    )


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_run(body: RunCreateInput, request: Request) -> RunResponse:
    """Persist and enqueue one durable agent run for the authenticated owner."""
    owner_id = _owner_id(request)
    control_plane = _control_plane(request)
    record = await control_plane.create_run(
        owner_id=owner_id,
        goal=body.goal,
        thread_id=body.thread_id or str(uuid4()),
        project_id=body.project_id,
    )
    await control_plane.append_event(
        run_id=record.id,
        owner_id=owner_id,
        event_type="run.created",
        state=record.status,
        payload={"thread_id": record.thread_id, "project_id": str(record.project_id) if record.project_id else None},
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
