from urllib.parse import parse_qs, urlparse

from app.config import get_settings
from app.main import app
from app.services.sessions import get_running_session
from app.services.today import add_task_to_day
from app.time import today, utc_now


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


def test_fastapi_metadata_uses_configured_application_name(client):
    settings = get_settings()

    assert app.title == settings.app_name

    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == settings.app_name
    assert response.json()["info"]["title"] == "Akrasia_Zero"


def test_application_titles_use_configured_application_name(client):
    home = client.get("/")
    today_page = client.get("/today/page")
    timer = client.get("/timer/")
    review = client.get("/review/")

    assert home.status_code == 200
    assert "Task List | Akrasia_Zero" in home.text
    assert "Akrasia_Zero:" in home.text

    assert today_page.status_code == 200
    assert "Today's Tasks | Akrasia_Zero" in today_page.text

    assert timer.status_code == 200
    assert "Session Timer | Akrasia_Zero" in timer.text

    assert review.status_code == 200
    assert "Review | Akrasia_Zero" in review.text


def test_application_shell_uses_configured_names(
    client,
    monkeypatch,
):
    monkeypatch.setenv("APP_NAME", "FocusLab")
    monkeypatch.setenv("DISPLAY_NAME", "Ada Lovelace")

    response = client.get("/")

    assert response.status_code == 200
    assert "FocusLab" in response.text
    assert "Task List | FocusLab" in response.text
    assert "FocusLab:" in response.text
    assert "Ada Lovelace" in response.text
    assert "Matt Stone" not in response.text
    assert "Akrasia_Zero" not in response.text

    timer = client.get("/timer/")
    assert "Session Timer | FocusLab" in timer.text


def _plan_today_task(db, make_task, title="Focus"):
    task = make_task(title)
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=1,
    )
    return task


def test_timer_ready_screen_uses_configured_focus_duration(
    client,
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    _plan_today_task(db, make_task)

    response = client.get("/timer/")

    assert response.status_code == 200
    assert 'value="40"' in response.text
    assert "40:00" in response.text
    assert 'value="25"' not in response.text


def test_timer_start_uses_configured_default_when_duration_absent(
    client,
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    _plan_today_task(db, make_task)

    response = client.post(
        "/timer/start",
        data={},
        follow_redirects=False,
    )

    assert response.status_code == 303

    running = get_running_session(db)
    assert running is not None
    assert running.planned_duration_seconds == 40 * 60


def test_timer_break_uses_configured_duration(
    client,
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("BREAK_DURATION_MINUTES", "7")
    _plan_today_task(db, make_task)

    start_response = client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )
    assert start_response.status_code == 303

    running = get_running_session(db)
    assert running is not None

    commit_response = client.post(
        "/timer/commit",
        data={
            "session_id": str(running.id),
            "outcome": "progress",
        },
        follow_redirects=False,
    )

    assert commit_response.status_code == 303

    location = commit_response.headers["location"]
    break_until = int(
        parse_qs(urlparse(location).query)["break_until"][0]
    )
    expected = int(utc_now().timestamp()) + 7 * 60
    assert abs(break_until - expected) <= 2

    page = client.get(location)
    assert page.status_code == 200
    assert 'data-break-duration-seconds="420"' in page.text
