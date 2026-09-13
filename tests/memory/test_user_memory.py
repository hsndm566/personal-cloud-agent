import pytest
from langgraph.store.memory import InMemoryStore

from memory.user_memory import UserMemory, user_namespace


@pytest.mark.asyncio
async def test_user_memory_isolates_users() -> None:
    memory = UserMemory(InMemoryStore())

    await memory.put("user-a", "birthdate", {"birthdate": "1990-01-02"})

    assert (await memory.get("user-a", "birthdate")).value == {"birthdate": "1990-01-02"}
    assert await memory.get("user-b", "birthdate") is None


@pytest.mark.asyncio
async def test_user_memory_search_stays_within_user_namespace() -> None:
    memory = UserMemory(InMemoryStore())

    await memory.put("user-a", "favorite-color", {"color": "blue"})
    await memory.put("user-b", "favorite-color", {"color": "green"})

    results = await memory.search("user-a")

    assert len(results) == 1
    assert results[0].value == {"color": "blue"}


def test_user_namespace_is_canonical_and_validated() -> None:
    assert user_namespace(" user-a ") == ("users", "user-a", "profile")
    assert user_namespace("user-a", "projects") == ("users", "user-a", "projects")

    with pytest.raises(ValueError, match="user_id"):
        user_namespace(" ")
    with pytest.raises(ValueError, match="scope"):
        user_namespace("user-a", " ")
