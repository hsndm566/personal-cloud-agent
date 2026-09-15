from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.tools import BaseTool

from agents.deep_agent import DeepAgent


@pytest.mark.asyncio
async def test_deep_agent_has_no_tools_without_github_pat(monkeypatch):
    monkeypatch.setattr("agents.deep_agent.settings.GITHUB_PAT", None)
    agent = DeepAgent()
    await agent.load()
    assert agent._mcp_tools == []
    assert agent._loaded is True
    assert agent.get_graph() is not None


@pytest.mark.asyncio
async def test_deep_agent_filters_tools_to_skill_allowlist(monkeypatch):
    class FakeTool(BaseTool):
        name: str
        description: str = "fake"

        def _run(self, *a, **kw):
            return "ok"

    tools = [FakeTool(name=n) for n in (
        "get_repository_tree", "get_file_contents", "search_code", "list_commits",
        "create_or_update_file", "delete_file",
    )]
    monkeypatch.setattr(
        "agents.deep_agent.settings.GITHUB_PAT",
        AsyncMock(get_secret_value=lambda: "fake-pat"),
    )
    with patch("agents.deep_agent.MultiServerMCPClient") as cls:
        cls.return_value.get_tools = AsyncMock(return_value=tools)
        agent = DeepAgent()
        await agent.load()
    names = {tool.name for tool in agent._mcp_tools}
    assert names <= {"get_repository_tree", "get_file_contents", "search_code", "list_commits"}
    assert "create_or_update_file" not in names
    assert "delete_file" not in names
    assert names
