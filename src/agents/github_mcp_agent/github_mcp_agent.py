"""GitHub MCP Agent - An agent that uses GitHub MCP tools for repository inspection."""

import logging
from datetime import datetime

from langchain.agents import create_agent
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StreamableHttpConnection
from langgraph.graph.state import CompiledStateGraph

from agents.lazy_agent import LazyLoadingAgent
from core import get_model, settings

logger = logging.getLogger(__name__)

# Milestone 1 intentionally exposes only the GitHub tools required to inspect a
# repository. The GitHub MCP server is also configured in read-only mode, and
# this client-side allowlist fails closed if the server returns anything else.
REPOSITORY_INSPECTION_TOOLS = frozenset(
    {
        "get_commit",
        "get_file_contents",
        "get_repository_tree",
        "list_branches",
        "list_commits",
        "search_code",
        "search_repositories",
    }
)

current_date = datetime.now().strftime("%B %d, %Y")
prompt = f"""
You are GitHubBot, a specialized assistant for read-only GitHub repository inspection.
Today's date is {current_date}.

Your available GitHub capabilities are deliberately limited to:
- discovering repositories
- reading files and directory contents
- inspecting repository trees
- searching code
- inspecting branches
- inspecting commits and commit details

You do not have tools that create, update, merge, push, delete, or otherwise mutate GitHub resources.
If a user asks for a write operation, explain that this agent is currently read-only.
Base conclusions on repository evidence and clearly distinguish observed facts from inference.
Respect repository permissions and access controls.
"""


class GitHubMCPAgent(LazyLoadingAgent):
    """GitHub MCP Agent with async initialization."""

    def __init__(self) -> None:
        super().__init__()
        self._mcp_tools: list[BaseTool] = []
        self._mcp_client: MultiServerMCPClient | None = None

    async def load(self) -> None:
        """Initialize the GitHub MCP agent by loading a minimal read-only tool set."""
        if not settings.GITHUB_PAT:
            logger.info("GITHUB_PAT is not set, GitHub MCP agent will have no tools")
            self._mcp_tools = []
            self._graph = self._create_graph()
            self._loaded = True
            return

        try:
            github_pat = settings.GITHUB_PAT.get_secret_value()
            connections: dict[str, Connection] = {
                "github": StreamableHttpConnection(
                    transport="streamable_http",
                    url=settings.MCP_GITHUB_SERVER_URL,
                    headers={
                        "Authorization": f"Bearer {github_pat}",
                        "X-MCP-Readonly": "true",
                        "X-MCP-Tools": ",".join(sorted(REPOSITORY_INSPECTION_TOOLS)),
                    },
                )
            }

            self._mcp_client = MultiServerMCPClient(connections)
            logger.info("MCP client initialized successfully")

            server_tools = await self._mcp_client.get_tools()
            self._mcp_tools = [
                tool for tool in server_tools if tool.name in REPOSITORY_INSPECTION_TOOLS
            ]

            unexpected_tools = sorted(
                {tool.name for tool in server_tools} - REPOSITORY_INSPECTION_TOOLS
            )
            if unexpected_tools:
                logger.warning(
                    "Dropped unexpected GitHub MCP tools in read-only mode: %s",
                    ", ".join(unexpected_tools),
                )

            logger.info(
                "GitHub MCP agent initialized with %d repository inspection tools",
                len(self._mcp_tools),
            )

        except Exception as e:
            logger.error(f"Failed to initialize GitHub MCP agent: {e}")
            self._mcp_tools = []
            self._mcp_client = None

        self._graph = self._create_graph()
        self._loaded = True

    def _create_graph(self) -> CompiledStateGraph:
        """Create the GitHub MCP agent graph."""
        model = get_model(settings.DEFAULT_MODEL)

        return create_agent(
            model=model,
            tools=self._mcp_tools,
            name="github-mcp-agent",
            system_prompt=prompt,
        )


# Create the agent instance
github_mcp_agent = GitHubMCPAgent()
