import re
from urllib.parse import parse_qs, urlparse

from app.config import get_settings
from app.main import app
from app.services.sessions import get_running_session
from app.services.tasks import TASK_PATH_SEPARATOR
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
    assert "Daily Plan | Akrasia_Zero" in today_page.text

    assert timer.status_code == 200
    assert "Session Timer | Akrasia_Zero" in timer.text
    assert "Go to Daily Plan" in timer.text
    assert "Today's Tasks" not in timer.text
    assert "Today’s Tasks" not in timer.text

    assert review.status_code == 200
    assert "Review | Akrasia_Zero" in review.text
    assert 'class="review-page"' in review.text
    assert 'class="date-nav-link"' in review.text
    assert 'class="daily-summary"' in review.text
    assert "<table>" not in review.text or 'class="table-scroll"' in review.text


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


def _primary_nav_tab_class(html: str, href: str) -> str:
    match = re.search(
        rf'<a\s+href="{re.escape(href)}"\s+class="(nav-tab[^"]*)"',
        html,
    )
    assert match is not None, f"nav tab {href} not found"
    return match.group(1)


def test_task_list_marks_task_list_nav_active_not_sessions(client):
    response = client.get("/")

    assert response.status_code == 200

    task_list = _primary_nav_tab_class(response.text, "/")
    sessions = _primary_nav_tab_class(response.text, "/timer/")

    assert "active" in task_list.split()
    assert "active" not in sessions.split()


def _plan_today_task(
    db,
    make_task,
    title="Focus",
    planned_sessions=1,
):
    task = make_task(title)
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=planned_sessions,
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
    assert "Configured focus:" in response.text
    assert "40 min" in response.text
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
    assert "Configured break:" in page.text
    assert "7 min" in page.text
    assert "Break" in page.text


def test_timer_ready_root_task_has_no_ancestry(
    client,
    db,
    make_task,
):
    _plan_today_task(db, make_task, "Root work")

    page = client.get("/timer/")

    assert page.status_code == 200
    assert "focus-task-ancestry" not in page.text
    assert re.search(
        r'class="focus-task-title"\s*>\s*Root work',
        page.text,
    )


def test_timer_ready_shows_ancestry_as_secondary_context(
    client,
    db,
    make_task,
):
    parent = make_task("Build Akrasia_Zero")
    child = make_task(
        "Write Installation Documents",
        parent=parent,
    )
    add_task_to_day(
        db=db,
        task=child,
        target_date=today(),
        planned_sessions=1,
    )

    page = client.get("/timer/")

    assert page.status_code == 200
    assert re.search(
        r'class="focus-task-ancestry"\s*>\s*Build Akrasia_Zero',
        page.text,
    )
    assert re.search(
        r'class="focus-task-title"\s*>\s*Write Installation Documents',
        page.text,
    )


def test_timer_ready_shows_deeper_nested_ancestry(
    client,
    db,
    make_task,
):
    root = make_task("Build Akrasia_Zero")
    branch = make_task("Documentation", parent=root)
    leaf = make_task(
        "Write Installation Documents",
        parent=branch,
    )
    add_task_to_day(
        db=db,
        task=leaf,
        target_date=today(),
        planned_sessions=1,
    )

    page = client.get("/timer/")
    ancestry = re.search(
        r'class="focus-task-ancestry"\s*>(.*?)</div>',
        page.text,
        re.S,
    )

    assert page.status_code == 200
    assert ancestry is not None
    collapsed = re.sub(r"\s+", " ", ancestry.group(1)).strip()
    assert collapsed == (
        "Build Akrasia_Zero"
        + TASK_PATH_SEPARATOR
        + "Documentation"
    )
    assert re.search(
        r'class="focus-task-title"\s*>\s*Write Installation Documents',
        page.text,
    )


def test_timer_running_shows_ancestry_as_secondary_context(
    client,
    db,
    make_task,
):
    parent = make_task("Build Akrasia_Zero")
    child = make_task(
        "Write Installation Documents",
        parent=parent,
    )
    add_task_to_day(
        db=db,
        task=child,
        target_date=today(),
        planned_sessions=1,
    )
    start = client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )

    assert start.status_code == 303

    page = client.get("/timer/")

    assert page.status_code == 200
    assert re.search(
        r'class="focus-task-ancestry"\s*>\s*Build Akrasia_Zero',
        page.text,
    )
    assert re.search(
        r'class="focus-task-title"\s*>\s*Write Installation Documents',
        page.text,
    )


def test_authenticated_shell_exposes_skip_link_and_current_nav(
    client,
):
    home = client.get("/")
    today_page = client.get("/today/page")
    timer = client.get("/timer/")
    review = client.get("/review/")

    assert home.status_code == 200
    assert 'href="#main-content"' in home.text
    assert 'id="main-content"' in home.text
    assert "Skip to main content" in home.text
    assert re.search(
        r'href="/"\s+class="nav-tab active"\s+aria-current="page"',
        home.text,
    )
    assert home.text.count('aria-current="page"') == 1

    assert today_page.status_code == 200
    assert re.search(
        r'href="/today/page"\s+class="nav-tab active"\s+aria-current="page"',
        today_page.text,
    )
    assert today_page.text.count('aria-current="page"') == 1

    assert timer.status_code == 200
    assert re.search(
        r'href="/timer/"\s+class="nav-tab active"\s+aria-current="page"',
        timer.text,
    )

    assert review.status_code == 200
    assert 'class="review-heading"' in review.text
    assert 'id="daily-summary-heading"' in review.text
    assert 'id="planned-versus-actual-heading"' in review.text
    assert 'id="session-history-heading"' in review.text


def test_timer_ready_controls_keep_visible_labels(
    client,
    db,
    make_task,
):
    _plan_today_task(db, make_task, "Ready work")

    page = client.get("/timer/")

    assert page.status_code == 200
    assert 'for="duration-minutes"' in page.text
    assert "Session Length" in page.text
    assert "Start Focus" in page.text
    assert "Minutes" in page.text
    assert "Next focus session" in page.text
    assert "Configured focus:" in page.text


def test_review_tables_use_column_headers_and_labels(
    client,
    db,
    make_task,
):
    _plan_today_task(db, make_task, "Reviewed work")

    page = client.get("/review/")

    assert page.status_code == 200
    assert 'aria-labelledby="planned-versus-actual-heading"' in page.text
    assert 'scope="col"' in page.text
    assert re.search(
        r'<span aria-hidden="true">←</span>\s*Previous',
        page.text,
    )


def test_timer_running_end_early_control_stays_labelled(
    client,
    db,
    make_task,
):
    _plan_today_task(db, make_task, "Running work")

    start = client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )
    assert start.status_code == 303

    page = client.get("/timer/")

    assert page.status_code == 200
    assert "End session early" in page.text
    assert "Progress" in page.text
    assert "Complete" in page.text
    assert "Interrupted" in page.text


def _timer_text(html: str) -> str:
    return re.sub(r"\s+", " ", html)


def _commit_running_and_open_break(client, db, outcome):
    running = get_running_session(db)
    assert running is not None

    response = client.post(
        "/timer/commit",
        data={
            "session_id": str(running.id),
            "outcome": outcome,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client.get(response.headers["location"])


def test_timer_ready_shows_planned_session_position(
    client,
    db,
    make_task,
):
    _plan_today_task(
        db,
        make_task,
        "Deep work",
        planned_sessions=4,
    )

    page = client.get("/timer/")
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Ready" in page.text
    assert "Next focus session" in page.text
    assert "Session 1 of 4" in text
    assert "3 planned sessions remaining after this one" in text
    assert "Up next: another session of this task" in text
    assert "Up next: Deep work" not in text


def test_timer_ready_shows_later_session_after_completed_work(
    client,
    db,
    make_task,
    make_work_session,
):
    daily_task = add_task_to_day(
        db=db,
        task=make_task("Deep work"),
        target_date=today(),
        planned_sessions=4,
    )
    make_work_session(
        daily_task,
        session_state="completed",
        outcome="progress",
    )

    page = client.get("/timer/")
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Session 2 of 4" in text
    assert "2 planned sessions remaining after this one" in text
    assert "Up next: another session of this task" in text


def test_timer_ready_last_planned_session_does_not_preview_other_task(
    client,
    db,
    make_task,
):
    _plan_today_task(db, make_task, "First work")
    _plan_today_task(db, make_task, "Second work")

    page = client.get("/timer/")
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Session 1 of 1" in text
    assert "Last planned session" in text
    assert "another session of this task" not in text
    assert "Up next: Second work" not in text
    assert re.search(
        r'class="focus-task-title"\s*>\s*First work',
        page.text,
    )


def test_timer_ready_omits_invented_position_when_plan_is_unset(
    client,
    db,
    make_task,
):
    _plan_today_task(
        db,
        make_task,
        "Unplanned work",
        planned_sessions=None,
    )

    page = client.get("/timer/")
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Unplanned work" in page.text
    assert "Session 1 of 1" not in text
    assert "remaining after this one" not in text
    assert "Last planned session" not in text
    assert "Up next:" not in text


def test_timer_running_keeps_focus_wording_and_same_task_next(
    client,
    db,
    make_task,
):
    _plan_today_task(
        db,
        make_task,
        "Focus work",
        planned_sessions=3,
    )
    start = client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )
    assert start.status_code == 303

    page = client.get("/timer/")
    text = _timer_text(page.text)

    assert page.status_code == 200
    label = re.search(
        r'class="focus-state-label"\s*>(.*?)</div>',
        page.text,
        re.S,
    )
    assert label is not None
    assert re.sub(r"\s+", " ", label.group(1)).strip() == "Focus"
    assert "Session in progress" in page.text
    assert "Session 1 of 3" in text
    assert "2 planned sessions remaining after this one" in text
    assert "Up next: another session of this task" in text


def test_timer_break_shows_same_task_after_progress(
    client,
    db,
    make_task,
):
    _plan_today_task(
        db,
        make_task,
        "Deep work",
        planned_sessions=4,
    )
    client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )

    page = _commit_running_and_open_break(
        client,
        db,
        "progress",
    )
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Break" in page.text
    assert "Pause between focus sessions" in page.text
    assert "End break early" in page.text
    assert "Up next: Deep work" in text
    assert "Session 2 of 4" in text
    assert "another session of this task" not in text
    assert "remaining after this one" not in text


def test_timer_break_shows_next_queued_task_after_complete(
    client,
    db,
    make_task,
):
    parent = make_task("Build Akrasia_Zero")
    child = make_task(
        "Write Installation Documents",
        parent=parent,
    )
    add_task_to_day(
        db=db,
        task=make_task("First work"),
        target_date=today(),
        planned_sessions=1,
    )
    add_task_to_day(
        db=db,
        task=child,
        target_date=today(),
        planned_sessions=2,
    )
    client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )

    page = _commit_running_and_open_break(
        client,
        db,
        "complete",
    )
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Break" in page.text
    assert "Up next:" in text
    assert "Build Akrasia_Zero" in text
    assert "Write Installation Documents" in text
    assert "Session 1 of 2" in text
    assert "First work" not in text


def test_timer_break_with_empty_remaining_queue(
    client,
    db,
    make_task,
):
    _plan_today_task(db, make_task, "Only work")
    client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )

    page = _commit_running_and_open_break(
        client,
        db,
        "complete",
    )
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Break" in page.text
    assert "Nothing else queued" in text
    assert "Up next:" not in text
    assert re.search(r"Session \d+ of \d+", text) is None


def test_timer_session_ended_associates_outcome_with_that_session(
    client,
    db,
    make_task,
):
    _plan_today_task(
        db,
        make_task,
        "Ended work",
        planned_sessions=2,
    )
    start = client.post(
        "/timer/start",
        data={"duration_minutes": "25"},
        follow_redirects=False,
    )
    assert start.status_code == 303

    running = get_running_session(db)
    assert running is not None

    ended = client.post(
        "/timer/end-early",
        data={"session_id": str(running.id)},
        follow_redirects=False,
    )
    assert ended.status_code == 303

    page = client.get("/timer/")
    text = _timer_text(page.text)
    label = re.search(
        r'class="focus-state-label"\s*>(.*?)</div>',
        page.text,
        re.S,
    )

    assert page.status_code == 200
    assert label is not None
    assert re.sub(r"\s+", " ", label.group(1)).strip() == (
        "Session ended"
    )
    assert "focus-outcome-state" in page.text
    assert "Record the result of this session" in text
    assert "Session result" in text
    assert "Ended work" in text
    assert "Session 1 of 2" in text
    assert "1 planned session remaining after this one" in text
    assert "Session in progress" not in text


def test_timer_empty_queue_keeps_daily_plan_wording(client):
    page = client.get("/timer/")
    text = _timer_text(page.text)

    assert page.status_code == 200
    assert "Nothing queued" in page.text
    assert "Go to Daily Plan" in text
    assert "Up next:" not in text
    assert re.search(r"Session \d+ of \d+", text) is None
    assert "Today's Tasks" not in text
