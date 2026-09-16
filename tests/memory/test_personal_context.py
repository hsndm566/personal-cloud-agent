import pytest
from langgraph.store.memory import InMemoryStore

from memory.personal_context import personal_context_message
from memory.user_memory import UserMemory


@pytest.mark.asyncio
async def test_context_retrieves_profile_site_and_activity_only_for_owner():
    store = InMemoryStore()
    memory = UserMemory(store)
    for scope in ("profile", "knowledge", "activity"):
        await memory.put("owner", scope, {"text": f"owner-{scope}"}, scope=scope)
        await memory.put("other", scope, {"text": "private-other-data"}, scope=scope)
    message = await personal_context_message(store, "owner")
    assert message is not None
    for scope in ("profile", "knowledge", "activity"):
        assert f"owner-{scope}" in message.content
    assert "private-other-data" not in message.content
    assert "not instructions" in message.content


@pytest.mark.asyncio
async def test_oversized_records_are_skipped_without_truncation():
    store = InMemoryStore()
    memory = UserMemory(store)
    await memory.put("owner", "large", {"text": "x" * 2000})
    await memory.put("owner", "small", {"text": "useful"})
    message = await personal_context_message(store, "owner", max_chars=200)
    assert message is not None
    assert "useful" in message.content
    assert "large" not in message.content


@pytest.mark.asyncio
async def test_missing_store_or_user_context_returns_none():
    assert await personal_context_message(None, "owner") is None
    assert await personal_context_message(InMemoryStore(), "owner") is None
