from pathlib import Path

import pytest

from agent_foundation import (
    ExecutionPlan,
    MarkdownSkillRouter,
    PlannedTask,
    SuccessCriterion,
    VerificationResult,
    VerificationStatus,
    plan_is_complete,
)


def test_completion_requires_every_criterion_to_be_verified() -> None:
    plan = ExecutionPlan(
        summary="inspect",
        success_criteria=[
            SuccessCriterion(id="framework", description="framework", verification_method="manifest"),
            SuccessCriterion(id="tests", description="tests", verification_method="ci"),
        ],
        tasks=[
            PlannedTask(
                id="inspect",
                title="Inspect files",
                objective="read repository evidence",
                verification_method="file evidence",
            )
        ],
    )
    assert not plan_is_complete(plan, [VerificationResult(
        criterion_id="framework", status=VerificationStatus.VERIFIED, explanation="found"
    )])
    assert plan_is_complete(plan, [
        VerificationResult(criterion_id="framework", status=VerificationStatus.VERIFIED, explanation="found"),
        VerificationResult(criterion_id="tests", status=VerificationStatus.VERIFIED, explanation="found"),
    ])


def test_skill_router_selects_relevant_cards() -> None:
    router = MarkdownSkillRouter(Path(__file__).parents[2] / "skills")
    selected = router.select("Inspect the GitHub repository files and recent commits")
    assert selected
    assert selected[0].name == "github-read"
    assert "github.fetch_file" in selected[0].allowed_tools


@pytest.mark.parametrize("goal", ["", "  "])
def test_plan_rejects_empty_summary(goal: str) -> None:
    with pytest.raises(ValueError):
        ExecutionPlan(
            summary=goal,
            success_criteria=[SuccessCriterion(id="x", description="x", verification_method="x")],
            tasks=[],
        )
