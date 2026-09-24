from unittest.mock import AsyncMock, patch
from uuid import uuid4

from service import app
from control_plane import RunRecord


def _owned_record(*, owner_id: str = "user_123", status: str = "queued") -> RunRecord:
    return RunRecord(
        id=uuid4(),
        owner_id=owner_id,
        goal="Inspect the repository",
        thread_id="thread_123",
        status=status,
    )


def test_create_run_requires_authenticated_identity(test_client):
    plane = AsyncMock()
    app.state.control_plane = plane
    with patch("service.service.authenticate_request", return_value=None):
        response = test_client.post("/runs", json={"goal": "Inspect the repository"})

    assert response.status_code == 401
    plane.create_run.assert_not_awaited()


def test_create_run_persists_event_and_enqueues(test_client):
    plane = AsyncMock()
    record = _owned_record()
    plane.create_run.return_value = record
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.post(
            "/runs",
            json={"goal": record.goal, "thread_id": record.thread_id},
        )

    assert response.status_code == 202
    assert response.json()["id"] == str(record.id)
    plane.create_run.assert_awaited_once_with(
        owner_id=record.owner_id,
        goal=record.goal,
        thread_id=record.thread_id,
        project_id=None,
    )
    plane.append_event.assert_awaited_once()
    plane.enqueue_run.assert_awaited_once_with(record)


def test_create_run_marks_failed_when_enqueue_fails(test_client):
    plane = AsyncMock()
    record = _owned_record()
    plane.create_run.return_value = record
    plane.enqueue_run.side_effect = RuntimeError("queue offline")
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.post("/runs", json={"goal": record.goal})

    assert response.status_code == 503
    plane.set_run_status.assert_awaited_once()
    assert plane.set_run_status.call_args.kwargs["status"] == "failed"


def test_get_run_is_owner_scoped(test_client):
    plane = AsyncMock()
    record = _owned_record()
    plane.get_run_for_owner.return_value = record
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.get(f"/runs/{record.id}")

    assert response.status_code == 200
    plane.get_run_for_owner.assert_awaited_once_with(record.id, record.owner_id)


def test_get_run_events_returns_owned_timeline(test_client):
    plane = AsyncMock()
    record = _owned_record()
    plane.get_run_for_owner.return_value = record
    plane.list_events.return_value = [
        {
            "id": 1,
            "event_type": "run.created",
            "state": "queued",
            "payload": {"goal": record.goal},
            "created_at": None,
        }
    ]
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.get(f"/runs/{record.id}/events")

    assert response.status_code == 200
    assert response.json()[0]["event_type"] == "run.created"
    plane.list_events.assert_awaited_once_with(
        run_id=record.id,
        owner_id=record.owner_id,
        limit=100,
    )
