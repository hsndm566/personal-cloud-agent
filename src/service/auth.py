"""Authentication helpers for the service boundary."""

from typing import Any

import jwt
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from langchain_core.runnables import RunnableConfig
from pydantic import SecretStr


def _split_values(value: str | None) -> list[str]:
    """Parse comma-separated Clerk settings without accepting empty values."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def authenticate_request(
    credentials: HTTPAuthorizationCredentials | None,
    *,
    auth_secret: SecretStr | None,
    clerk_jwt_key: SecretStr | None,
    clerk_issuer: str | None,
    clerk_authorized_parties: str | None,
    clerk_audience: str | None,
) -> str | None:
    """Authenticate a request and return its trusted user ID when available.

    Clerk takes precedence when configured. The legacy shared bearer secret remains
    available for local or single-owner deployments, but it does not establish a
    per-user identity and therefore returns ``None``.
    """
    if clerk_jwt_key is not None:
        if credentials is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        try:
            decode_kwargs: dict[str, Any] = {
                "algorithms": ["RS256"],
                "options": {"require": ["exp", "sub"]},
            }
            if clerk_issuer:
                decode_kwargs["issuer"] = clerk_issuer
            if clerk_audience:
                decode_kwargs["audience"] = clerk_audience
            else:
                decode_kwargs["options"]["verify_aud"] = False

            claims = jwt.decode(
                credentials.credentials,
                clerk_jwt_key.get_secret_value(),
                **decode_kwargs,
            )
            user_id = claims.get("sub")
            if not isinstance(user_id, str) or not user_id:
                raise jwt.InvalidTokenError("Clerk token has no valid subject")

            authorized_parties = _split_values(clerk_authorized_parties)
            authorized_party = claims.get("azp")
            if authorized_parties and authorized_party not in authorized_parties:
                raise jwt.InvalidTokenError("Clerk token has an unauthorized party")
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc

        return user_id

    if auth_secret:
        expected = auth_secret.get_secret_value()
        if not credentials or credentials.credentials != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return None


def require_matching_user_id(
    authenticated_user_id: str | None, requested_user_id: str | None
) -> str | None:
    """Prevent a Clerk-authenticated caller from selecting another user ID."""
    if (
        authenticated_user_id is not None
        and requested_user_id is not None
        and requested_user_id != authenticated_user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_id does not match the authenticated Clerk user",
        )
    return authenticated_user_id or requested_user_id


def metadata_user_id(metadata: dict[str, Any] | None) -> str | None:
    """Read a checkpoint owner without trusting caller-controlled input."""
    if not metadata:
        return None
    user_id = metadata.get("user_id")
    return user_id if isinstance(user_id, str) else None


async def ensure_thread_owner(
    checkpointer: Any, thread_id: str | None, authenticated_user_id: str | None
) -> None:
    """Reject access to a thread unless its checkpoint belongs to the JWT subject."""
    if not authenticated_user_id or not thread_id:
        return
    if not checkpointer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")

    checkpoint = await checkpointer.aget_tuple(
        RunnableConfig(configurable={"thread_id": thread_id})
    )
    if metadata_user_id(checkpoint.metadata if checkpoint else None) != authenticated_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
