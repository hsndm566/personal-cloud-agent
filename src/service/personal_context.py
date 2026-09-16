"""Authenticated ingestion and retrieval of owner-scoped personal reference records."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from memory.user_memory import UserMemory

router = APIRouter(prefix="/personal-context", tags=["personal-context"])
Scope = Literal["profile", "knowledge", "activity"]


class ContextRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=8000)
    source: str = Field(min_length=1, max_length=2000)


def owner_memory(request: Request) -> tuple[str, UserMemory]:
    user_id = getattr(request.state, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(401, "An authenticated user identity is required")
    store = getattr(request.app.state, "personal_context_store", None)
    if store is None:
        raise HTTPException(503, "Personal context storage is unavailable")
    return user_id, UserMemory(store)


@router.put("/{scope}/{key}")
async def save_context(
    scope: Scope,
    key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")],
    body: ContextRecordInput,
    request: Request,
) -> dict[str, str]:
    user_id, memory = owner_memory(request)
    await memory.put(
        user_id, key,
        {"content": body.content, "source": body.source,
         "updated_at": datetime.now(UTC).isoformat()},
        scope=scope,
    )
    return {"key": key, "scope": scope, "status": "saved"}


@router.get("/{scope}/{key}")
async def get_context(scope: Scope, key: str, request: Request) -> dict:
    user_id, memory = owner_memory(request)
    record = await memory.get(user_id, key, scope=scope)
    if record is None:
        raise HTTPException(404, "Context record not found")
    return {"key": record.key, "scope": scope, "value": record.value}
