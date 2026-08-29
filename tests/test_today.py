import re
from datetime import timedelta

import pytest

from app.models import DailyTask
from app.services.sessions import commit_work_session, start_work_session
from app.services.tasks import (
    TASK_PATH_SEPARATOR,
    cancel_task,
    complete_task,
    task_ancestry_label,
)
from app.services.today import (
    add_task_to_day,
    get_daily_tasks_for_date,
    remove_task_from_day,
    update_planned_sessions,
)
from app.time import today


def _collapsed(html: str) -> str:
    return re.sub(r"\s+", " ", html)


def _queue_rows(html: str) -> list[tuple[str, str]]:
    return re.findall(
        r'data-queue-position="(\d+)"\s+'
        r'data-task-title="([^"]*)"',
        html,
    )


def _queue_ancestry(html: str) -> str | None:
    match = re.search(
        r'class="task-ancestry"[^>]*>(.*?)</span>',
        html,
        re.S,
    )
    if match is None:
        return None

    return re.sub(r"\s+", " ", match.group(1)).strip()


def test_removed_daily_task_can_be_replanned(
    db,
    make_task,
):
    task = make_task("Carry me")
    target_date = today()

    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    remove_task_from_day(db, daily_task)

    replanned = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=3,
    )

    assert replanned.id == daily_task.id
    assert replanned.state == "planned"
    assert replanned.planned_sessions == 3


@pytest.mark.parametrize(
    "concluded_state",
    ["completed", "abandoned"],
)
def test_concluded_daily_task_cannot_be_replanned(
    db,
    make_task,
    make_daily_task,
    concluded_state,
):
    task = make_task("Already finished")
    target_date = today()

    make_daily_task(
        task,
        target_date=target_date,
        state=concluded_state,
    )

    with pytest.raises(
        ValueError,
        match="already been concluded",
    ):
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
        )


@pytest.mark.parametrize(
    "conclude",
    [complete_task, cancel_task],
)
def test_task_list_conclusion_drops_daily_task_from_executable_queue(
    db,
    make_task,
    conclude,
):
    task = make_task("On today")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    conclude(db, task)

    executable = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )
    assert executable == []

    preserved = db.get(DailyTask, daily_task.id)
    assert preserved is not None
    assert preserved.state == "planned"


@pytest.mark.parametrize("planned_sessions", [0, -1, -3])
def test_add_task_to_day_rejects_non_positive_planned_sessions(
    db,
    make_task,
    planned_sessions,
):
    task = make_task("Needs a plan")
    target_date = today()

    with pytest.raises(
        ValueError,
        match="at least 1",
    ):
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
            planned_sessions=planned_sessions,
        )

    assert db.query(DailyTask).count() == 0


def test_add_task_to_day_allows_uncommitted_planned_sessions(
    db,
    make_task,
):
    task = make_task("No count yet")
    target_date = today()

    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=None,
    )

    assert daily_task.planned_sessions is None
    assert daily_task.state == "planned"


@pytest.mark.parametrize("planned_sessions", [0, -1])
def test_update_planned_sessions_rejects_non_positive_count(
    db,
    make_task,
    planned_sessions,
):
    task = make_task("Already planned")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=2,
    )

    with pytest.raises(
        ValueError,
        match="at least 1",
    ):
        update_planned_sessions(
            db=db,
            daily_task=daily_task,
            planned_sessions=planned_sessions,
        )

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 2


def test_cannot_remove_daily_task_with_running_work_session(
    db,
    make_task,
    make_work_session,
):
    task = make_task("In progress")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    work_session = make_work_session(daily_task)

    with pytest.raises(
        ValueError,
        match="work session is running",
    ):
        remove_task_from_day(db, daily_task)

    db.refresh(daily_task)
    db.refresh(work_session)
    assert daily_task.state == "planned"
    assert work_session.session_state == "running"
    assert work_session.ended_at is None
    assert work_session.outcome is None


def test_can_remove_daily_task_after_running_session_is_committed(
    db,
    make_task,
):
    task = make_task("Done for now")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    work_session = start_work_session(
        db=db,
        target_date=target_date,
    )

    commit_work_session(
        db=db,
        work_session=work_session,
        outcome="progress",
    )

    removed = remove_task_from_day(db, daily_task)

    assert removed.state == "removed"
    db.refresh(work_session)
    assert work_session.session_state == "completed"
    assert work_session.outcome == "progress"


def test_add_task_to_day_unique_constraint_keeps_session_usable(
    db,
    make_task,
    monkeypatch,
):
    task = make_task("Once only")
    target_date = today()
    first = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )
    first_id = first.id

    monkeypatch.setattr(
        "app.services.today._find_daily_task",
        lambda db, task_id, target_date: None,
    )

    with pytest.raises(
        ValueError,
        match="already on the selected day",
    ):
        add_task_to_day(
            db=db,
            task=task,
            target_date=target_date,
            planned_sessions=1,
        )

    persisted = db.get(DailyTask, first_id)
    assert persisted is not None
    assert persisted.state == "planned"

    matching = (
        db.query(DailyTask)
        .filter(
            DailyTask.task_id == task.id,
            DailyTask.date == target_date,
        )
        .count()
    )
    assert matching == 1


def test_today_page_null_planned_sessions_is_renderable(
    client,
    db,
    make_task,
):
    task = make_task("Needs a plan")
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=None,
    )

    page = client.get("/today/page")

    assert page.status_code == 200
    assert "Needs a plan" in page.text
    assert "Planned sessions" in page.text
    assert "Save plan" in page.text
    assert "Update plan" not in page.text
    assert "Planned sessions not set" in page.text
    assert "Nothing selected for this day." not in page.text
    assert "0 planned sessions" in _collapsed(page.text)


def test_today_page_stored_planned_sessions_is_renderable(
    client,
    db,
    make_task,
):
    task = make_task("Counted in")
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=2,
    )

    page = client.get("/today/page")

    assert page.status_code == 200
    assert "Counted in" in page.text
    assert "Planned sessions" in page.text
    assert "Update plan" in page.text
    assert 'value="2"' in page.text
    assert "Save plan" not in page.text
    assert "2 planned sessions" in _collapsed(page.text)


def test_today_page_empty_queue_uses_selection_wording(
    client,
):
    page = client.get("/today/page")

    assert page.status_code == 200
    assert "Nothing selected for this day." in page.text
    assert "Nothing committed to this day." not in page.text
    assert "0 planned sessions · 0 min" in _collapsed(
        page.text
    )
    assert "Including breaks between sessions" not in page.text


def test_commit_sessions_writes_planned_count_without_changing_state(
    client,
    db,
    make_task,
):
    task = make_task("Lock the count")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=None,
    )

    response = client.post(
        f"/today/{daily_task.id}/commit-sessions",
        data={
            "planned_sessions": "3",
            "target_date": str(target_date),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/today/page?target_date={target_date}"
    )

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 3
    assert daily_task.state == "planned"

    page = client.get(
        f"/today/page?target_date={target_date}",
    )

    assert page.status_code == 200
    assert "Planned sessions" in page.text
    assert "Update plan" in page.text
    assert 'value="3"' in page.text
    assert "Save plan" not in page.text


def test_commit_sessions_can_overwrite_an_existing_planned_count(
    client,
    db,
    make_task,
):
    task = make_task("Revise the plan")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{daily_task.id}/commit-sessions",
        data={
            "planned_sessions": "4",
            "target_date": str(target_date),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 4
    assert daily_task.state == "planned"

    page = client.get(
        f"/today/page?target_date={target_date}",
    )

    assert page.status_code == 200
    assert 'value="4"' in page.text
    assert "Planned sessions" in page.text
    assert "Update plan" in page.text


def test_today_page_queue_numbers_follow_existing_order(
    client,
    db,
    make_task,
):
    first = make_task("Alpha item")
    second = make_task("Beta item")
    target_date = today()
    add_task_to_day(
        db=db,
        task=first,
        target_date=target_date,
        planned_sessions=1,
    )
    add_task_to_day(
        db=db,
        task=second,
        target_date=target_date,
        planned_sessions=1,
    )

    page = client.get("/today/page")

    assert page.status_code == 200
    assert _queue_rows(page.text) == [
        ("1", "Alpha item"),
        ("2", "Beta item"),
    ]


def test_today_page_queue_numbers_follow_reorder(
    client,
    db,
    make_task,
):
    first = make_task("Alpha item")
    second = make_task("Beta item")
    target_date = today()
    add_task_to_day(
        db=db,
        task=first,
        target_date=target_date,
        planned_sessions=1,
    )
    later = add_task_to_day(
        db=db,
        task=second,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{later.id}/move",
        data={"direction": "up"},
        follow_redirects=False,
    )

    assert response.status_code == 303

    page = client.get("/today/page")

    assert page.status_code == 200
    assert _queue_rows(page.text) == [
        ("1", "Beta item"),
        ("2", "Alpha item"),
    ]


def test_today_page_row_shows_planned_session_count_and_focus_time(
    client,
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    task = make_task("Deep work")
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=2,
    )

    page = client.get("/today/page")
    text = _collapsed(page.text)

    assert page.status_code == 200
    assert "2 planned sessions · 80 min" in text


def test_today_page_summary_totals_use_configuration(
    client,
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    monkeypatch.setenv("BREAK_DURATION_MINUTES", "7")
    first = make_task("One block")
    second = make_task("Two blocks")
    target_date = today()
    add_task_to_day(
        db=db,
        task=first,
        target_date=target_date,
        planned_sessions=1,
    )
    add_task_to_day(
        db=db,
        task=second,
        target_date=target_date,
        planned_sessions=3,
    )

    page = client.get("/today/page")
    text = _collapsed(page.text)

    assert page.status_code == 200
    assert "4 planned sessions · 160 min (2 hr 40 min)" in text
    assert (
        "Including breaks between sessions: "
        "181 min (3 hr 1 min)"
    ) in text


def test_today_page_null_planned_sessions_are_omitted_from_totals(
    client,
    db,
    make_task,
    monkeypatch,
):
    monkeypatch.setenv("FOCUS_SESSION_MINUTES", "40")
    counted = make_task("Counted row")
    unset = make_task("Unset row")
    target_date = today()
    add_task_to_day(
        db=db,
        task=counted,
        target_date=target_date,
        planned_sessions=2,
    )
    add_task_to_day(
        db=db,
        task=unset,
        target_date=target_date,
        planned_sessions=None,
    )

    page = client.get("/today/page")
    text = _collapsed(page.text)

    assert page.status_code == 200
    assert "Planned sessions not set" in page.text
    assert "2 planned sessions · 80 min" in text
    assert "3 planned sessions" not in text
    assert "120 min" not in text
    assert _queue_rows(page.text) == [
        ("1", "Counted row"),
        ("2", "Unset row"),
    ]


def _plan_for_label(day) -> str:
    return f"Plan for {day.strftime('%A %d %B %Y')}"


def _date_navigation(html: str) -> str:
    match = re.search(
        r'aria-label="Date navigation"(.*?)</nav>',
        html,
        re.S,
    )
    assert match is not None
    return match.group(1)


def test_today_page_current_day_uses_daily_plan_wording(
    client,
):
    current = today()
    page = client.get("/today/page")
    text = _collapsed(page.text)
    nav = _date_navigation(page.text)

    assert page.status_code == 200
    assert "Daily Plan | Akrasia_Zero" in page.text
    assert _plan_for_label(current) in text
    assert "Today's Tasks" not in page.text
    assert re.search(
        r'href="/today/page"\s+class="date-nav-link"\s*>\s*Today',
        nav,
    )


def test_today_page_past_and_future_days_use_plan_for_date(
    client,
):
    current = today()
    past = current - timedelta(days=1)
    future = current + timedelta(days=1)

    past_page = client.get(
        f"/today/page?target_date={past}",
    )
    future_page = client.get(
        f"/today/page?target_date={future}",
    )

    past_text = _collapsed(past_page.text)
    future_text = _collapsed(future_page.text)

    assert past_page.status_code == 200
    assert future_page.status_code == 200
    assert _plan_for_label(past) in past_text
    assert _plan_for_label(future) in future_text
    assert _plan_for_label(current) not in past_text
    assert _plan_for_label(current) not in future_text
    assert "Today's Tasks" not in past_page.text
    assert "Today's Tasks" not in future_page.text


def test_today_page_today_navigation_stays_available(
    client,
):
    past = today() - timedelta(days=2)
    page = client.get(
        f"/today/page?target_date={past}",
    )
    nav = _date_navigation(page.text)

    assert page.status_code == 200
    assert re.search(
        r'href="/today/page"\s+class="date-nav-link"\s*>\s*Today',
        nav,
    )


def test_today_page_previous_and_next_navigation_unchanged(
    client,
):
    current = today()
    previous = current - timedelta(days=1)
    following = current + timedelta(days=1)

    page = client.get("/today/page")
    nav = _collapsed(_date_navigation(page.text))

    assert page.status_code == 200
    assert (
        f'href="/today/page?target_date={previous}"'
        in nav
    )
    assert "← Previous" in nav
    assert (
        f'href="/today/page?target_date={following}"'
        in nav
    )
    assert "Next →" in nav

    past_page = client.get(
        f"/today/page?target_date={previous}",
    )
    past_nav = _collapsed(
        _date_navigation(past_page.text)
    )
    earlier = previous - timedelta(days=1)

    assert (
        f'href="/today/page?target_date={earlier}"'
        in past_nav
    )
    assert (
        f'href="/today/page?target_date={current}"'
        in past_nav
    )


def test_today_page_root_task_has_no_ancestry_breadcrumb(
    client,
    db,
    make_task,
):
    task = make_task("Root work")
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=1,
    )

    page = client.get("/today/page")
    text = _collapsed(page.text)

    assert page.status_code == 200
    assert _queue_rows(page.text) == [
        ("1", "Root work"),
    ]
    assert "task-ancestry" not in page.text
    assert TASK_PATH_SEPARATOR not in text
    assert "Root work" in page.text


def test_today_page_shows_one_level_nested_ancestry(
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

    page = client.get("/today/page")

    assert page.status_code == 200
    assert _queue_rows(page.text) == [
        ("1", "Write Installation Documents"),
    ]
    assert _queue_ancestry(page.text) == (
        task_ancestry_label(child) + TASK_PATH_SEPARATOR
    ).strip()
    assert "Write Installation Documents" in page.text


def test_today_page_shows_deeper_nested_ancestry(
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

    page = client.get("/today/page")

    assert page.status_code == 200
    assert _queue_rows(page.text) == [
        ("1", "Write Installation Documents"),
    ]
    assert _queue_ancestry(page.text) == (
        task_ancestry_label(leaf) + TASK_PATH_SEPARATOR
    ).strip()
    assert "Write Installation Documents" in page.text

