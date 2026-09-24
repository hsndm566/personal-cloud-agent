from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from control_plane import RunRecord
from service import app


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


def test_create_run_rejects_blank_goal(test_client):
    plane = AsyncMock()
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value="user_123"):
        response = test_client.post("/runs", json={"goal": "   "})

    assert response.status_code == 422
    plane.create_run.assert_not_awaited()


def test_create_run_hides_foreign_project(test_client):
    plane = AsyncMock()
    plane.create_run.side_effect = KeyError("project not found")
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value="user_123"):
        response = test_client.post(
            "/runs",
            json={"goal": "Inspect", "project_id": str(uuid4())},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_create_run_requires_configured_control_plane(test_client):
    app.state.control_plane = None

    with patch("service.service.authenticate_request", return_value="user_123"):
        response = test_client.post("/runs", json={"goal": "Inspect"})

    assert response.status_code == 503


def test_list_runs_is_owner_scoped(test_client):
    plane = AsyncMock()
    record = _owned_record()
    plane.list_runs.return_value = [record]
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.get("/runs?limit=25&status=queued")

    assert response.status_code == 200
    assert response.json()[0]["id"] == str(record.id)
    plane.list_runs.assert_awaited_once_with(
        owner_id=record.owner_id,
        limit=25,
        status="queued",
    )


def test_run_artifacts_require_owned_run(test_client):
    plane = AsyncMock()
    record = _owned_record()
    plane.get_run_for_owner.return_value = record
    artifact_id = uuid4()
    plane.list_artifacts.return_value = [
        {
            "id": artifact_id,
            "kind": "final_output",
            "uri": None,
            "content": {"content": "done"},
            "created_at": None,
        }
    ]
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.get(f"/runs/{record.id}/artifacts")

    assert response.status_code == 200
    assert response.json()[0]["id"] == str(artifact_id)
    plane.list_artifacts.assert_awaited_once_with(
        run_id=record.id,
        owner_id=record.owner_id,
        limit=100,
    )


def test_cancel_queued_run(test_client):
    plane = AsyncMock()
    record = _owned_record(status="cancelled")
    plane.cancel_run.return_value = record
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.post(f"/runs/{record.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    plane.cancel_run.assert_awaited_once_with(
        run_id=record.id,
        owner_id=record.owner_id,
    )


def test_cancel_running_run_returns_conflict(test_client):
    plane = AsyncMock()
    record = _owned_record(status="running")
    plane.cancel_run.side_effect = RuntimeError("run cannot be cancelled from status running")
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value=record.owner_id):
        response = test_client.post(f"/runs/{record.id}/cancel")

    assert response.status_code == 409


def test_worker_health_reports_freshest_heartbeat(test_client):
    plane = AsyncMock()
    now = datetime.now(UTC) - timedelta(seconds=3)
    plane.latest_worker_heartbeat.return_value = {
        "worker_id": "worker-1",
        "agent_id": "deep-agent",
        "metadata": {"pid": 123},
        "started_at": now,
        "last_seen_at": now,
    }
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value="user_123"):
        response = test_client.get("/runs/system/worker-health")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["worker_id"] == "worker-1"
    assert body["agent_id"] == "deep-agent"
    assert body["age_seconds"] >= 0


def test_worker_health_reports_unavailable_without_heartbeat(test_client):
    plane = AsyncMock()
    plane.latest_worker_heartbeat.return_value = None
    app.state.control_plane = plane

    with patch("service.service.authenticate_request", return_value="user_123"):
        response = test_client.get("/runs/system/worker-health")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "worker_id": None,
        "agent_id": None,
        "last_seen_at": None,
        "age_seconds": None,
    }
