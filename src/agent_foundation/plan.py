"""Typed planning and verification contracts for agent runs."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    FAILED = "failed"
    BLOCKED = "blocked"


class SuccessCriterion(BaseModel):
    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    verification_method: str = Field(min_length=1)


class PlannedTask(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    risk_level: str = "read_only"
    verification_method: str = Field(min_length=1)


class ExecutionPlan(BaseModel):
    summary: str = Field(min_length=1)
    success_criteria: list[SuccessCriterion] = Field(min_length=1)
    tasks: list[PlannedTask] = Field(min_length=1)


class VerificationResult(BaseModel):
    status: VerificationStatus
    criterion_id: str = Field(min_length=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


def plan_is_complete(plan: ExecutionPlan, results: list[VerificationResult]) -> bool:
    """Require every criterion to have a verified result before completion."""
    by_id = {result.criterion_id: result for result in results}
    return all(by_id.get(item.id, None) and by_id[item.id].status == VerificationStatus.VERIFIED for item in plan.success_criteria)
