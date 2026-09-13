"""User-scoped access to the LangGraph long-term memory store."""

from typing import Any

from langgraph.store.base import BaseStore

USER_NAMESPACE = "users"
DEFAULT_SCOPE = "profile"


def user_namespace(user_id: str, scope: str = DEFAULT_SCOPE) -> tuple[str, ...]:
    """Return the canonical namespace for one user's memory scope."""
    normalized_user_id = user_id.strip()
    normalized_scope = scope.strip()
    if not normalized_user_id:
        raise ValueError("user_id must not be empty")
    if not normalized_scope:
        raise ValueError("scope must not be empty")
    return (USER_NAMESPACE, normalized_user_id, normalized_scope)


class UserMemory:
    """Keep user-memory reads and writes behind one isolation boundary."""

    def __init__(self, store: BaseStore):
        self._store = store

    async def get(self, user_id: str, key: str, *, scope: str = DEFAULT_SCOPE) -> Any:
        """Read a value from the user's isolated namespace."""
        return await self._store.aget(user_namespace(user_id, scope), key=key)

    async def put(
        self,
        user_id: str,
        key: str,
        value: dict[str, Any],
        *,
        scope: str = DEFAULT_SCOPE,
    ) -> None:
        """Write a value to the user's isolated namespace."""
        await self._store.aput(user_namespace(user_id, scope), key, value)

    async def search(
        self,
        user_id: str,
        *,
        query: str | None = None,
        scope: str = DEFAULT_SCOPE,
        limit: int = 10,
    ) -> list[Any]:
        """Search only the selected user's namespace."""
        return await self._store.asearch(user_namespace(user_id, scope), query=query, limit=limit)


__all__ = ["DEFAULT_SCOPE", "USER_NAMESPACE", "UserMemory", "user_namespace"]
