from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from service.auth import authenticate_request, require_matching_user_id

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_public_pem = _private_key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _clerk_token(
    user_id: str = "user_clerk_123", authorized_party: str = "http://localhost:3000"
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "azp": authorized_party,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        _private_pem,
        algorithm="RS256",
    )


def _clerk_kwargs() -> dict:
    return {
        "auth_secret": None,
        "clerk_jwt_key": SecretStr(_public_pem.decode()),
        "clerk_issuer": None,
        "clerk_authorized_parties": "http://localhost:3000",
        "clerk_audience": None,
    }


def test_no_auth_secret(mock_settings, mock_agent, test_client):
    """Test that when AUTH_SECRET is not set, all requests are allowed"""
    mock_settings.AUTH_SECRET = None
    response = test_client.post(
        "/invoke",
        json={"message": "test"},
        headers={"Authorization": "Bearer any-token"},
    )
    assert response.status_code == 200

    # Should also work without any auth header
    response = test_client.post("/invoke", json={"message": "test"})
    assert response.status_code == 200


def test_auth_secret_correct(mock_settings, mock_agent, test_client):
    """Test that when AUTH_SECRET is set, requests with correct token are allowed"""
    mock_settings.AUTH_SECRET = SecretStr("test-secret")
    response = test_client.post(
        "/invoke",
        json={"message": "test"},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200


def test_auth_secret_incorrect(mock_settings, mock_agent, test_client):
    """Test that when AUTH_SECRET is set, requests with wrong token are rejected"""
    mock_settings.AUTH_SECRET = SecretStr("test-secret")
    response = test_client.post(
        "/invoke",
        json={"message": "test"},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert response.status_code == 401

    # Should also reject requests with no auth header
    response = test_client.post("/invoke", json={"message": "test"})
    assert response.status_code == 401


def test_clerk_token_returns_subject():
    user_id = authenticate_request(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=_clerk_token()),
        **_clerk_kwargs(),
    )
    assert user_id == "user_clerk_123"


def test_clerk_token_rejects_invalid_signature():
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")

    with patch("service.auth.jwt.decode", side_effect=jwt.InvalidTokenError("invalid")):
        try:
            authenticate_request(credentials, **_clerk_kwargs())
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 401
        else:
            raise AssertionError("Invalid Clerk tokens must be rejected")


def test_clerk_subject_cannot_be_overridden():
    try:
        require_matching_user_id("user_clerk_123", "another-user")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("A Clerk user must not select another user ID")


def test_clerk_user_id_reaches_agent_and_rejects_mismatch(mock_settings, mock_agent, test_client):
    mock_settings.AUTH_SECRET = None
    mock_settings.CLERK_JWT_KEY = SecretStr(_public_pem.decode())
    mock_settings.CLERK_ISSUER = None
    mock_settings.CLERK_AUTHORIZED_PARTIES = "http://localhost:3000"
    mock_settings.CLERK_AUDIENCE = None
    token = _clerk_token()
    headers = {"Authorization": f"Bearer {token}"}

    response = test_client.post(
        "/invoke",
        json={"message": "test", "user_id": "user_clerk_123"},
        headers=headers,
    )
    assert response.status_code == 200
    config = mock_agent.ainvoke.call_args.kwargs["config"]
    assert config["configurable"]["user_id"] == "user_clerk_123"

    response = test_client.post(
        "/invoke",
        json={"message": "test", "user_id": "another-user"},
        headers=headers,
    )
    assert response.status_code == 403


def test_clerk_user_cannot_read_another_users_threads(mock_settings, mock_agent, test_client):
    mock_settings.AUTH_SECRET = None
    mock_settings.CLERK_JWT_KEY = SecretStr(_public_pem.decode())
    mock_settings.CLERK_ISSUER = None
    mock_settings.CLERK_AUTHORIZED_PARTIES = "http://localhost:3000"
    mock_settings.CLERK_AUDIENCE = None

    response = test_client.get(
        "/threads",
        params={"user_id": "another-user"},
        headers={"Authorization": f"Bearer {_clerk_token()}"},
    )
    assert response.status_code == 403


def test_clerk_user_cannot_read_another_users_history(mock_settings, mock_agent, test_client):
    mock_settings.AUTH_SECRET = None
    mock_settings.CLERK_JWT_KEY = SecretStr(_public_pem.decode())
    mock_settings.CLERK_ISSUER = None
    mock_settings.CLERK_AUTHORIZED_PARTIES = "http://localhost:3000"
    mock_settings.CLERK_AUDIENCE = None

    checkpointer = AsyncMock()
    checkpointer.aget_tuple.return_value = Mock(metadata={"user_id": "another-user"})
    mock_agent.checkpointer = checkpointer

    response = test_client.post(
        "/history",
        json={"thread_id": "thread-owned-by-another-user"},
        headers={"Authorization": f"Bearer {_clerk_token()}"},
    )
    assert response.status_code == 404


def test_clerk_user_cannot_resume_another_users_thread(mock_settings, mock_agent, test_client):
    mock_settings.AUTH_SECRET = None
    mock_settings.CLERK_JWT_KEY = SecretStr(_public_pem.decode())
    mock_settings.CLERK_ISSUER = None
    mock_settings.CLERK_AUTHORIZED_PARTIES = "http://localhost:3000"
    mock_settings.CLERK_AUDIENCE = None

    checkpointer = AsyncMock()
    checkpointer.aget_tuple.return_value = Mock(metadata={"user_id": "another-user"})
    mock_agent.checkpointer = checkpointer

    response = test_client.post(
        "/invoke",
        json={"message": "test", "thread_id": "thread-owned-by-another-user"},
        headers={"Authorization": f"Bearer {_clerk_token()}"},
    )
    assert response.status_code == 404
    mock_agent.ainvoke.assert_not_awaited()
