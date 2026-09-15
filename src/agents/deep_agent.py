"""Deep Agents execution graph registered through the normal agent boundary."""

from deepagents import create_deep_agent

from agent_foundation.skills import MarkdownSkillRouter
from core import get_model, settings
from core.settings import SKILLS_DIR

_skill_router = MarkdownSkillRouter(SKILLS_DIR)


def _instructions(goal: str = "") -> str:
    skills = _skill_router.select(goal)
    skill_lines = "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)
    return (
        "You are the personal cloud agent's Deep Agents execution graph. "
        "Plan with the structured ExecutionPlan/SuccessCriterion contracts in "
        "agent_foundation.plan before acting, and verify every criterion before "
        "reporting completion.\n\nAvailable skills:\n" + (skill_lines or "- (none matched)")
    )


deep_agent = create_deep_agent(
    tools=[],
    system_prompt=_instructions(),
    model=get_model(settings.DEFAULT_MODEL),
)
