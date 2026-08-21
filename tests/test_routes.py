import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.services.today import add_task_to_day
from app.time import today


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_create_task_api_rejects_empty_title_with_409(client):
    response = client.post("/tasks", params={"title": ""})

    assert response.status_code == 409
    assert "cannot be empty" in response.json()["detail"]


def test_add_to_today_rejects_zero_planned_sessions_with_409(
    client,
    make_task,
):
    task = make_task("Needs sessions")

    response = client.post(
        "/today/add",
        params={
            "task_id": task.id,
            "planned_sessions": 0,
        },
    )

    assert response.status_code == 409
    assert "at least 1" in response.json()["detail"]


def test_add_to_today_missing_task_returns_404(client):
    response = client.post(
        "/today/add",
        params={"task_id": 99999},
    )

    assert response.status_code == 404


def test_add_to_today_malformed_task_id_returns_422(client):
    response = client.post(
        "/today/add",
        params={"task_id": "not-an-id"},
    )

    assert response.status_code == 422


def test_remove_from_today_rejects_running_session_with_409(
    client,
    db,
    make_task,
    make_work_session,
):
    task = make_task("Busy")
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=1,
    )
    make_work_session(daily_task)

    response = client.delete(f"/today/{daily_task.id}")

    assert response.status_code == 409
    assert "work session is running" in response.json()["detail"]
