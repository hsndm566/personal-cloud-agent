import pytest

from memory.mem0_adapter import Mem0Memory


class FakeMem0:
    def __init__(self) -> None:
        self.add_calls: list[tuple[object, dict[str, object]]] = []
        self.search_calls: list[tuple[object, dict[str, object]]] = []

    def add(self, messages, **kwargs):
        self.add_calls.append((messages, kwargs))
        return {"ok": True}

    def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return [{"memory": "prefer free infrastructure"}]


@pytest.mark.asyncio
async def test_mem0_memory_scopes_user_and_project() -> None:
    client = FakeMem0()
    memory = Mem0Memory(client)

    await memory.add([{"role": "user", "content": "Prefer free infrastructure."}], user_id="u1", project_id="p1")
    results = await memory.search("infrastructure", user_id="u1", project_id="p1")

    assert results[0]["memory"] == "prefer free infrastructure"
    assert client.add_calls[0][1]["user_id"] == "u1"
    assert client.add_calls[0][1]["agent_id"] == "p1"
    assert client.search_calls[0][1]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_mem0_memory_rejects_invalid_scope_and_limit() -> None:
    memory = Mem0Memory(FakeMem0())

    with pytest.raises(ValueError, match="user_id"):
        await memory.search("query", user_id=" ")
    with pytest.raises(ValueError, match="between"):
        await memory.search("query", user_id="u1", limit=101)
