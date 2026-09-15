"""pgmq-backed queue client."""

from typing import Any, Protocol


class PgmqConnection(Protocol):
    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...


class PgmqQueueClient:
    """Adapt a psycopg connection to the worker queue interface."""

    def __init__(self, connection: PgmqConnection):
        self._connection = connection

    async def read(self, queue: str, visibility_timeout: int, quantity: int) -> list[dict[str, Any]]:
        result = await self._connection.execute(
            "select msg_id, message from pgmq.read(%s, %s, %s)",
            (queue, visibility_timeout, quantity),
        )
        rows = await result.fetchall() if hasattr(result, "fetchall") else result
        return [{"msg_id": row["msg_id"], "message": row["message"]} for row in (rows or [])]

    async def archive(self, queue: str, msg_id: int) -> None:
        await self._connection.execute("select pgmq.archive(%s, %s)", (queue, msg_id))
