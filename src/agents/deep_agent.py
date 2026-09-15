"""Deep Agents graph with fail-closed, read-only GitHub inspection tools."""

import logging
from datetime import datetime

from deepagents import create_deep_agent
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StreamableHttpConnection
from langgraph.graph.state import CompiledStateGraph

from agent_foundation.skills import MarkdownSkillRouter
from agents.github_mcp_agent.github_mcp_agent import REPOSITORY_INSPECTION_TOOLS
from agents.lazy_agent import LazyLoadingAgent
from core import get_model, settings
from core.settings import SKILLS_DIR

logger = logging.getLogger(__name__)
_skill_router = MarkdownSkillRouter(SKILLS_DIR)
SKILL_TOOL_ALIASES = {
    "github.get_repo": "get_repository_tree",
    "github.fetch_file": "get_file_contents",
    "github.search_code": "search_code",
    "github.list_commits": "list_commits",
}
current_date = datetime.now().strftime("%B %d, %Y")


class DeepAgent(LazyLoadingAgent):
    def __init__(self) -> None:
        super().__init__()
        self._mcp_tools: list[BaseTool] = []
        self._mcp_client: MultiServerMCPClient | None = None

    def _instructions(self, goal: str = "") -> str:
        skills = _skill_router.select(goal)
        skill_lines = "\n".join(f"- {s.name}: {s.description}" for s in skills) or "- (none matched)"
        return f"""You are the personal cloud agent's Deep Agents execution graph.
Today's date is {current_date}.
Plan with the structured ExecutionPlan/SuccessCriterion contracts in agent_foundation.plan
before acting, and verify every criterion via agent_foundation.plan.plan_is_complete.
You have read-only repository inspection tools only. Base conclusions on repository evidence.
Available skills for this goal:
{skill_lines}
"""

    async def load(self) -> None:
        if not settings.GITHUB_PAT:
            self._mcp_tools = []
            self._graph = self._create_graph()
            self._loaded = True
            return
        try:
            connections: dict[str, Connection] = {
                "github": StreamableHttpConnection(
                    transport="streamable_http",
                    url=settings.MCP_GITHUB_SERVER_URL,
                    headers={
                        "Authorization": f"Bearer {settings.GITHUB_PAT.get_secret_value()}",
                        "X-MCP-Readonly": "true",
                        "X-MCP-Tools": ",".join(sorted(REPOSITORY_INSPECTION_TOOLS)),
                    },
                )
            }
            self._mcp_client = MultiServerMCPClient(connections)
            server_tools = await self._mcp_client.get_tools()
            declared = {
                SKILL_TOOL_ALIASES[raw]
                for skill in _skill_router.skills
                for raw in skill.allowed_tools
                if raw in SKILL_TOOL_ALIASES
            }
            allowed = REPOSITORY_INSPECTION_TOOLS & declared
            self._mcp_tools = [tool for tool in server_tools if tool.name in allowed]
        except Exception as exc:
            logger.error("Failed to initialize deep-agent MCP tools: %s", exc)
            self._mcp_tools = []
            self._mcp_client = None
        self._graph = self._create_graph()
        self._loaded = True

    def _create_graph(self) -> CompiledStateGraph:
        return create_deep_agent(
            tools=self._mcp_tools,
            system_prompt=self._instructions(),
            model=get_model(settings.DEFAULT_MODEL),
        )


deep_agent = DeepAgent()
