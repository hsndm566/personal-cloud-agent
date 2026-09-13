"""Tests for the GitHub MCP Agent."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.tools import Tool

from agents.github_mcp_agent.github_mcp_agent import (
    REPOSITORY_INSPECTION_TOOLS,
    GitHubMCPAgent,
    prompt,
)
from core.settings import settings


class TestGitHubMCPAgent:
    """Test the GitHub MCP Agent functionality."""

    def test_initialization(self):
        """Test that agent initializes correctly."""
        agent = GitHubMCPAgent()
        assert not agent._loaded
        assert agent._mcp_tools == []
        assert agent._mcp_client is None

    def test_repository_inspection_scope_is_minimal(self):
        """Keep milestone-one GitHub access intentionally narrow."""
        assert REPOSITORY_INSPECTION_TOOLS == frozenset(
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

    @pytest.mark.asyncio
    async def test_load_without_github_pat(self):
        """Test load when GITHUB_PAT is not set."""
        agent = GitHubMCPAgent()

        with patch.object(settings, "GITHUB_PAT", None):
            await agent.load()

        assert agent._loaded
        assert agent._mcp_tools == []
        assert agent._mcp_client is None
        assert agent._graph is not None

    @pytest.mark.asyncio
    async def test_load_with_github_pat_uses_server_and_client_read_only_filters(self):
        """Expose only reviewed repository-inspection tools and configure MCP read-only mode."""
        agent = GitHubMCPAgent()
        mock_client = Mock()

        allowed_tool = Tool(
            name="get_file_contents", description="Read repository files", func=lambda x: x
        )
        write_tool = Tool(name="push_files", description="Push files", func=lambda x: x)
        unknown_tool = Tool(name="future_tool", description="New upstream tool", func=lambda x: x)
        mock_tools = [allowed_tool, write_tool, unknown_tool]

        with (
            patch.object(
                settings, "GITHUB_PAT", Mock(get_secret_value=Mock(return_value="test_token"))
            ),
            patch.object(settings, "MCP_GITHUB_SERVER_URL", "https://api.githubcopilot.com/mcp/"),
            patch(
                "agents.github_mcp_agent.github_mcp_agent.MultiServerMCPClient"
            ) as mock_client_class,
            patch(
                "agents.github_mcp_agent.github_mcp_agent.StreamableHttpConnection"
            ) as mock_connection,
            patch("agents.github_mcp_agent.github_mcp_agent.get_model") as mock_get_model,
        ):
            mock_client_class.return_value = mock_client
            mock_client.get_tools = AsyncMock(return_value=mock_tools)
            mock_get_model.return_value = Mock()

            await agent.load()

        assert agent._loaded
        assert agent._mcp_tools == [allowed_tool]
        assert agent._mcp_client == mock_client
        assert agent._graph is not None

        mock_connection.assert_called_once()
        headers = mock_connection.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test_token"
        assert headers["X-MCP-Readonly"] == "true"
        assert headers["X-MCP-Tools"] == ",".join(sorted(REPOSITORY_INSPECTION_TOOLS))

    @pytest.mark.asyncio
    async def test_unknown_tools_fail_closed(self):
        """New upstream tools are unavailable until explicitly reviewed."""
        agent = GitHubMCPAgent()
        mock_client = Mock()
        unknown_tool = Tool(name="brand_new_tool", description="Unknown", func=lambda x: x)

        with (
            patch.object(
                settings, "GITHUB_PAT", Mock(get_secret_value=Mock(return_value="test_token"))
            ),
            patch.object(settings, "MCP_GITHUB_SERVER_URL", "https://api.githubcopilot.com/mcp/"),
            patch(
                "agents.github_mcp_agent.github_mcp_agent.MultiServerMCPClient"
            ) as mock_client_class,
            patch("agents.github_mcp_agent.github_mcp_agent.StreamableHttpConnection"),
            patch("agents.github_mcp_agent.github_mcp_agent.get_model") as mock_get_model,
        ):
            mock_client_class.return_value = mock_client
            mock_client.get_tools = AsyncMock(return_value=[unknown_tool])
            mock_get_model.return_value = Mock()

            await agent.load()

        assert agent._mcp_tools == []
        assert agent._graph is not None

    @pytest.mark.asyncio
    async def test_load_with_mcp_error(self):
        """Test load when MCP client creation fails."""
        agent = GitHubMCPAgent()

        with (
            patch.object(
                settings, "GITHUB_PAT", Mock(get_secret_value=Mock(return_value="test_token"))
            ),
            patch.object(settings, "MCP_GITHUB_SERVER_URL", "https://api.githubcopilot.com/mcp/"),
            patch(
                "agents.github_mcp_agent.github_mcp_agent.MultiServerMCPClient",
                side_effect=Exception("Connection failed"),
            ),
            patch("agents.github_mcp_agent.github_mcp_agent.get_model") as mock_get_model,
        ):
            mock_get_model.return_value = Mock()

            await agent.load()

        assert agent._loaded
        assert agent._mcp_tools == []
        assert agent._mcp_client is None
        assert agent._graph is not None

    def test_create_graph(self):
        """Test graph creation."""
        agent = GitHubMCPAgent()
        agent._mcp_tools = [Mock(), Mock()]

        with (
            patch("agents.github_mcp_agent.github_mcp_agent.get_model") as mock_get_model,
            patch("agents.github_mcp_agent.github_mcp_agent.create_agent") as mock_create_agent,
        ):
            mock_model = Mock()
            mock_get_model.return_value = mock_model
            mock_graph = Mock()
            mock_create_agent.return_value = mock_graph

            graph = agent._create_graph()

            assert graph == mock_graph
            mock_create_agent.assert_called_once_with(
                model=mock_model,
                tools=agent._mcp_tools,
                name="github-mcp-agent",
                system_prompt=prompt,
            )

    def test_get_graph_not_loaded(self):
        """Test get_graph when agent is not loaded."""
        agent = GitHubMCPAgent()

        with pytest.raises(RuntimeError, match="Agent not loaded. Call load\\(\\) first."):
            agent.get_graph()

    def test_get_graph_loaded(self):
        """Test get_graph when agent is loaded."""
        agent = GitHubMCPAgent()
        agent._loaded = True
        agent._graph = Mock()

        graph = agent.get_graph()

        assert graph == agent._graph
