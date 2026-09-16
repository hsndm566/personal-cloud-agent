import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.store.memory import InMemoryStore

from service.personal_context import router


@pytest.fixture
def client():
    app = FastAPI()
    app.state.personal_context_store = InMemoryStore()

    @app.middleware("http")
    async def test_identity(request, call_next):
        # Simulate the verified identity populated by the production auth dependency.
        request.state.user_id = request.headers.get("x-test-owner")
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_saved_site_context_is_visible_only_to_owner(client):
    path = "/personal-context/knowledge/portfolio"
    body = {"content": "My portfolio projects", "source": "portfolio-sync"}
    assert client.put(path, json=body, headers={"x-test-owner": "owner"}).status_code == 200
    response = client.get(path, headers={"x-test-owner": "owner"})
    assert response.status_code == 200
    assert response.json()["value"]["content"] == body["content"]
    assert response.json()["value"]["updated_at"]
    assert client.get(path, headers={"x-test-owner": "other"}).status_code == 404
    assert client.get(path).status_code == 401
    assert client.put(path, json=body).status_code == 401


def test_context_upsert_updates_existing_snapshot_and_rejects_owner_override(client):
    path = "/personal-context/activity/current-project"
    headers = {"x-test-owner": "owner"}
    for content in ("old", "new"):
        assert client.put(path, json={"content": content, "source": "sync"},
                          headers=headers).status_code == 200
    assert client.get(path, headers=headers).json()["value"]["content"] == "new"
    assert client.put(path, json={"content": "bad", "source": "sync", "user_id": "other"},
                      headers=headers).status_code == 422
