import re
from datetime import timedelta

import pytest

from app.models import DailyTask, Task
from app.services.sessions import commit_work_session, start_work_session
from app.services.tasks import (
    ALL_AREAS_FILTER,
    DEFAULT_TASK_AREA,
    TASK_AREAS,
    TASK_PATH_SEPARATOR,
    cancel_task,
    complete_task,
    task_ancestry_label,
)
from app.services.today import (
    add_task_to_day,
    get_daily_tasks_for_date,
    remove_task_from_day,
    reorder_daily_tasks,
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


def _source_tree_titles(html: str) -> list[str]:
    return re.findall(
        r'class="today-tree-title"\s*>\s*([^<]+?)\s*<',
        html,
    )


def _mixed_area_source_tasks(db, make_task):
    areas = [
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    ]
    parent_area, child_area, sibling_area = areas[:3]
    parent = make_task(
        "Filtered parent",
        area=parent_area,
    )
    matching_child = make_task(
        "Matching child",
        parent=parent,
        area=child_area,
    )
    sibling = make_task(
        "Unrelated sibling",
        parent=parent,
        area=sibling_area,
    )
    general = make_task(
        "General root",
        area=DEFAULT_TASK_AREA,
    )
    return {
        "parent": parent,
        "matching_child": matching_child,
        "sibling": sibling,
        "general": general,
        "parent_area": parent_area,
        "child_area": child_area,
        "sibling_area": sibling_area,
    }


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


def test_today_page_commit_forms_use_htmx_row_swap(
    client,
    db,
    make_task,
):
    task = make_task("Queued item")
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=None,
    )

    page = client.get("/today/page")

    assert page.status_code == 200
    assert (
        f'hx-post="/today/{daily_task.id}/commit-sessions"'
        in page.text
    )
    assert (
        f'hx-target="#today-queue-item-{daily_task.id}"'
        in page.text
    )
    assert 'hx-swap="outerHTML"' in page.text
    assert "source-tasks-heading" in page.text
    assert "today-plan-summary" in page.text
    assert "hx-swap-oob" not in page.text


def test_htmx_save_plan_returns_row_and_oob_summary(
    client,
    db,
    make_task,
):
    saved = make_task("Saved item")
    other = make_task("Other item")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=saved,
        target_date=target_date,
        planned_sessions=None,
    )
    add_task_to_day(
        db=db,
        task=other,
        target_date=target_date,
        planned_sessions=2,
    )

    response = client.post(
        f"/today/{daily_task.id}/commit-sessions",
        data={
            "planned_sessions": "3",
            "target_date": str(target_date),
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == [
        ("1", "Saved item"),
    ]
    assert 'id="today-queue-item-' in response.text
    assert "Save plan" not in response.text
    assert "Update plan" in response.text
    assert 'value="3"' in response.text
    assert "3 planned sessions · 75 min" in text
    assert "Planned sessions not set" not in response.text
    assert "source-tasks-heading" not in response.text
    assert 'data-task-title="Other item"' not in response.text
    assert 'id="today-plan-summary"' in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert "5 planned sessions · 125 min" in text
    assert (
        "Including breaks between sessions: 145 min"
        in text
    )
    assert 'name="csrf_token"' in response.text

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 3


def test_htmx_update_plan_returns_row_and_oob_summary(
    client,
    db,
    make_task,
):
    task = make_task("Revise item")
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
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == [
        ("1", "Revise item"),
    ]
    assert "Save plan" not in response.text
    assert "Update plan" in response.text
    assert 'value="4"' in response.text
    assert "4 planned sessions · 100 min" in text
    assert "1 planned session · 25 min" not in text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert (
        "Including breaks between sessions: 115 min"
        in text
    )
    assert "source-tasks-heading" not in response.text

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 4


def test_htmx_commit_sessions_rejects_non_positive_count(
    client,
    db,
    make_task,
):
    task = make_task("Keep unset")
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
            "planned_sessions": "0",
            "target_date": str(target_date),
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert (
        response.text
        == "Planned sessions must be at least 1."
    )
    assert "source-tasks-heading" not in response.text
    assert "Daily Plan" not in response.text
    assert "today-plan-summary" not in response.text

    db.refresh(daily_task)
    assert daily_task.planned_sessions is None


def test_commit_sessions_non_htmx_rejects_non_positive_count(
    client,
    db,
    make_task,
):
    task = make_task("Keep original")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=2,
    )

    response = client.post(
        f"/today/{daily_task.id}/commit-sessions",
        data={
            "planned_sessions": "0",
            "target_date": str(target_date),
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Planned sessions must be at least 1.",
    }

    db.refresh(daily_task)
    assert daily_task.planned_sessions == 2


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


def _move_button(html: str, title: str, direction: str) -> str:
    match = re.search(
        rf'aria-label="Move {re.escape(title)} {direction}"(.*?)>',
        html,
        re.S,
    )
    assert match is not None
    return match.group(1)


def test_today_page_move_forms_use_htmx_queue_swap(
    client,
    db,
    make_task,
):
    task = make_task("Queued item")
    add_task_to_day(
        db=db,
        task=task,
        target_date=today(),
        planned_sessions=1,
    )

    page = client.get("/today/page")

    assert page.status_code == 200
    assert 'hx-target="ol.today-queue"' in page.text
    assert 'hx-swap="outerHTML"' in page.text
    assert 'hx-post="/today/' in page.text
    assert 'source-tasks-heading' in page.text
    assert "today-plan-summary" in page.text


def test_htmx_move_up_returns_reordered_queue_partial(
    client,
    db,
    make_task,
):
    first = make_task("Alpha item")
    second = make_task("Beta item")
    third = make_task("Gamma item")
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
    later = add_task_to_day(
        db=db,
        task=third,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{later.id}/move",
        data={"direction": "up"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert _queue_rows(response.text) == [
        ("1", "Alpha item"),
        ("2", "Gamma item"),
        ("3", "Beta item"),
    ]
    assert 'class="today-queue"' in response.text
    assert "source-tasks-heading" not in response.text
    assert "today-plan-summary" not in response.text
    assert "disabled" in _move_button(
        response.text,
        "Alpha item",
        "up",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Alpha item",
        "down",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Gamma item",
        "up",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Gamma item",
        "down",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Beta item",
        "up",
    )
    assert "disabled" in _move_button(
        response.text,
        "Beta item",
        "down",
    )
    assert 'name="csrf_token"' in response.text


def test_htmx_move_down_returns_reordered_queue_partial(
    client,
    db,
    make_task,
):
    first = make_task("Alpha item")
    second = make_task("Beta item")
    third = make_task("Gamma item")
    target_date = today()
    earliest = add_task_to_day(
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
    add_task_to_day(
        db=db,
        task=third,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{earliest.id}/move",
        data={"direction": "down"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert _queue_rows(response.text) == [
        ("1", "Beta item"),
        ("2", "Alpha item"),
        ("3", "Gamma item"),
    ]
    assert "source-tasks-heading" not in response.text
    assert "today-plan-summary" not in response.text
    assert "disabled" in _move_button(
        response.text,
        "Beta item",
        "up",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Beta item",
        "down",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Alpha item",
        "up",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Alpha item",
        "down",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Gamma item",
        "up",
    )
    assert "disabled" in _move_button(
        response.text,
        "Gamma item",
        "down",
    )


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
    assert "Today’s Tasks" not in page.text
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
    assert "Today’s Tasks" not in past_page.text
    assert "Today’s Tasks" not in future_page.text


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
    assert re.search(
        r'<span aria-hidden="true">←</span>\s*Previous',
        nav,
    )
    assert (
        f'href="/today/page?target_date={following}"'
        in nav
    )
    assert re.search(
        r'Next\s*<span aria-hidden="true">→</span>',
        nav,
    )

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


def test_today_page_selection_checkboxes_have_names_and_hit_area(
    client,
    make_task,
):
    make_task("Selectable work")

    page = client.get("/today/page")

    assert page.status_code == 200
    assert 'class="today-checkbox-control"' in page.text
    assert 'aria-label="Add Selectable work to this day"' in page.text
    assert 'id="select-task-' in page.text


def test_today_page_queue_controls_have_accessible_names(
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
    html = page.text

    assert page.status_code == 200
    assert 'aria-label="Move Alpha item up"' in html
    assert 'aria-label="Move Alpha item down"' in html
    assert 'aria-label="Move Beta item up"' in html
    assert 'aria-label="Move Beta item down"' in html
    assert re.search(
        r'aria-label="Move Alpha item up"[^>]*>\s*'
        r'<span aria-hidden="true">↑</span>',
        html,
    )
    assert re.search(
        r'aria-label="Move Alpha item down"[^>]*>\s*'
        r'<span aria-hidden="true">↓</span>',
        html,
    )
    assert "Queue position" in html
    assert 'aria-label="Update planned sessions for Alpha item"' in html


def test_today_page_selection_forms_use_htmx_workspace_bodies(
    client,
    make_task,
):
    make_task("Selectable work")

    page = client.get("/today/page")

    assert page.status_code == 200
    assert 'hx-post="/today/add-from-page"' in page.text
    assert 'hx-target="#today-source-body"' in page.text
    assert 'hx-swap="outerHTML"' in page.text
    assert 'hx-trigger="change"' in page.text
    assert 'id="today-source-body"' in page.text
    assert 'id="today-queue-body"' in page.text
    assert "hx-swap-oob" not in page.text
    assert "this.form.submit()" not in page.text
    assert 'id="area-filter"' in page.text
    assert 'hx-include="#area-filter"' in page.text
    area_filter_at = page.text.index('id="area-filter"')
    source_at = page.text.index('id="today-source-body"')
    assert area_filter_at < source_at
    assert 'hx-get="/today/source-tree"' in page.text
    assert 'hx-target="#today-source-body"' in page.text


def test_today_page_reading_order_puts_queue_before_source_tree(
    client,
):
    page = client.get("/today/page")
    queue_at = page.text.find('id="today-queue-heading"')
    source_at = page.text.find('id="source-tasks-heading"')

    assert page.status_code == 200
    assert queue_at != -1
    assert source_at != -1
    assert queue_at < source_at
    assert 'class="today-pane today-queue-pane"' in page.text
    assert 'class="today-pane today-source-pane"' in page.text


def test_add_from_page_non_htmx_redirects(
    client,
    db,
    make_task,
):
    task = make_task("New pick")
    target_date = today()

    response = client.post(
        "/today/add-from-page",
        data={
            "task_id": str(task.id),
            "target_date": str(target_date),
            "planned_sessions": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/today/page?target_date={target_date}"
    )

    queued = get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )
    assert [item.task.title for item in queued] == [
        "New pick",
    ]
    assert queued[0].planned_sessions == 1


def test_remove_from_page_non_htmx_redirects(
    client,
    db,
    make_task,
):
    task = make_task("Take off")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{daily_task.id}/remove-from-page",
        data={"target_date": str(target_date)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/today/page?target_date={target_date}"
    )
    assert get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    ) == []


def test_htmx_add_to_day_syncs_tree_queue_and_summary(
    client,
    db,
    make_task,
):
    existing = make_task("Already queued")
    added = make_task("Newly selected")
    target_date = today()
    add_task_to_day(
        db=db,
        task=existing,
        target_date=target_date,
        planned_sessions=2,
    )

    response = client.post(
        "/today/add-from-page",
        data={
            "task_id": str(added.id),
            "target_date": str(target_date),
            "planned_sessions": "1",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == [
        ("1", "Already queued"),
        ("2", "Newly selected"),
    ]
    assert (
        'aria-label="Remove Newly selected from this day"'
        in response.text
    )
    assert (
        'aria-label="Add Newly selected to this day"'
        not in response.text
    )
    assert re.search(
        r'id="select-task-'
        + str(added.id)
        + r'".*?checked',
        response.text,
        re.S,
    )
    assert "today-tree-item-selected" in response.text
    assert "1 planned session · 25 min" in text
    assert "3 planned sessions · 75 min" in text
    assert 'id="today-source-body"' in response.text
    assert 'id="today-queue-body"' in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert "source-tasks-heading" not in response.text
    assert "date-navigation" not in response.text
    assert "Nothing selected for this day." not in response.text
    assert 'name="csrf_token"' in response.text


def test_htmx_remove_from_day_syncs_tree_queue_and_summary(
    client,
    db,
    make_task,
):
    kept = make_task("Stay on plan")
    removed = make_task("Drop from plan")
    target_date = today()
    add_task_to_day(
        db=db,
        task=kept,
        target_date=target_date,
        planned_sessions=2,
    )
    daily_task = add_task_to_day(
        db=db,
        task=removed,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{daily_task.id}/remove-from-page",
        data={"target_date": str(target_date)},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == [
        ("1", "Stay on plan"),
    ]
    assert (
        'aria-label="Add Drop from plan to this day"'
        in response.text
    )
    assert (
        'aria-label="Remove Drop from plan from this day"'
        not in response.text
    )
    assert 'data-task-title="Drop from plan"' not in response.text
    assert "2 planned sessions · 50 min" in text
    assert "3 planned sessions" not in text
    assert "Nothing selected for this day." not in response.text
    assert "source-tasks-heading" not in response.text
    assert "date-navigation" not in response.text


def test_htmx_remove_last_item_shows_empty_queue_and_zero_summary(
    client,
    db,
    make_task,
):
    task = make_task("Only item")
    target_date = today()
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=2,
    )

    response = client.post(
        f"/today/{daily_task.id}/remove-from-page",
        data={"target_date": str(target_date)},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == []
    assert "Nothing selected for this day." in response.text
    assert 'class="today-queue"' not in response.text
    assert "0 planned sessions · 0 min" in text
    assert (
        'aria-label="Add Only item to this day"'
        in response.text
    )


def test_htmx_add_to_day_uses_carry_forward_planned_sessions(
    client,
    db,
    make_task,
):
    task = make_task("Unfinished work")
    target_date = today()
    yesterday = target_date - timedelta(days=1)
    add_task_to_day(
        db=db,
        task=task,
        target_date=yesterday,
        planned_sessions=3,
    )

    page = client.get("/today/page")
    assert "carry-forward-marker" in page.text
    assert "Yesterday · 3 planned sessions" in _collapsed(
        page.text
    )
    assert (
        'aria-label="Add Unfinished work to this day"'
        in page.text
    )

    response = client.post(
        "/today/add-from-page",
        data={
            "task_id": str(task.id),
            "target_date": str(target_date),
            "planned_sessions": "3",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == [
        ("1", "Unfinished work"),
    ]
    assert "carry-forward-marker" not in response.text
    assert (
        'aria-label="Remove Unfinished work from this day"'
        in response.text
    )
    assert "3 planned sessions · 75 min" in text
    assert "Update plan" in response.text


def test_htmx_remove_from_day_restores_carry_forward_marker(
    client,
    db,
    make_task,
):
    task = make_task("Still unfinished")
    target_date = today()
    yesterday = target_date - timedelta(days=1)
    add_task_to_day(
        db=db,
        task=task,
        target_date=yesterday,
        planned_sessions=3,
    )
    daily_task = add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=3,
    )

    response = client.post(
        f"/today/{daily_task.id}/remove-from-page",
        data={"target_date": str(target_date)},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    text = _collapsed(response.text)
    assert _queue_rows(response.text) == []
    assert "carry-forward-marker" in response.text
    assert "Yesterday · 3 planned sessions" in text
    assert (
        'aria-label="Add Still unfinished to this day"'
        in response.text
    )
    assert "today-tree-item-carry" in response.text
    assert "0 planned sessions · 0 min" in text


def test_htmx_add_to_day_conflict_returns_html(
    client,
    db,
    make_task,
):
    task = make_task("Already there")
    target_date = today()
    add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        "/today/add-from-page",
        data={
            "task_id": str(task.id),
            "target_date": str(target_date),
            "planned_sessions": "1",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert (
        response.text
        == "This task is already on the selected day."
    )
    assert "source-tasks-heading" not in response.text
    assert "Daily Plan" not in response.text
    assert "date-navigation" not in response.text


def test_add_from_page_non_htmx_conflict_still_json(
    client,
    db,
    make_task,
):
    task = make_task("Already there")
    target_date = today()
    add_task_to_day(
        db=db,
        task=task,
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        "/today/add-from-page",
        data={
            "task_id": str(task.id),
            "target_date": str(target_date),
            "planned_sessions": "1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "This task is already on the selected day.",
    }


def test_htmx_remove_from_day_conflict_returns_html(
    client,
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
    make_work_session(daily_task)

    response = client.post(
        f"/today/{daily_task.id}/remove-from-page",
        data={"target_date": str(target_date)},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert (
        response.text
        == "Cannot remove a task from the day while a work session is running."
    )
    assert "source-tasks-heading" not in response.text
    assert "Daily Plan" not in response.text

    db.refresh(daily_task)
    assert daily_task.state == "planned"


def _planned_queue(db, target_date):
    return get_daily_tasks_for_date(
        db=db,
        target_date=target_date,
    )


def _add_named_queue(db, make_task, titles, target_date):
    return [
        add_task_to_day(
            db=db,
            task=make_task(title),
            target_date=target_date,
            planned_sessions=1,
        )
        for title in titles
    ]


def test_reorder_daily_tasks_applies_full_permutation(
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )
    first.task.sort_order = 9
    db.commit()

    reordered = reorder_daily_tasks(
        db=db,
        target_date=target_date,
        ordered_ids=[third.id, first.id, second.id],
    )

    assert [item.task.title for item in reordered] == [
        "Gamma",
        "Alpha",
        "Beta",
    ]
    assert [item.sort_order for item in reordered] == [0, 1, 2]
    assert [item.state for item in reordered] == [
        "planned",
        "planned",
        "planned",
    ]
    assert [item.planned_sessions for item in reordered] == [
        1,
        1,
        1,
    ]

    db.refresh(first.task)
    assert first.task.sort_order == 9


def test_reorder_daily_tasks_moves_first_to_last(
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )

    reordered = reorder_daily_tasks(
        db=db,
        target_date=target_date,
        ordered_ids=[second.id, third.id, first.id],
    )

    assert [item.task.title for item in reordered] == [
        "Beta",
        "Gamma",
        "Alpha",
    ]
    assert [item.sort_order for item in reordered] == [0, 1, 2]


def test_reorder_daily_tasks_moves_last_to_first(
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )

    reordered = reorder_daily_tasks(
        db=db,
        target_date=target_date,
        ordered_ids=[third.id, first.id, second.id],
    )

    assert [item.task.title for item in reordered] == [
        "Gamma",
        "Alpha",
        "Beta",
    ]
    assert [item.sort_order for item in reordered] == [0, 1, 2]


def test_reorder_daily_tasks_does_not_change_other_dates(
    db,
    make_task,
):
    target_date = today()
    yesterday = target_date - timedelta(days=1)
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )
    other = add_task_to_day(
        db=db,
        task=make_task("Yesterday item"),
        target_date=yesterday,
        planned_sessions=2,
    )
    other.sort_order = 7
    db.commit()

    reorder_daily_tasks(
        db=db,
        target_date=target_date,
        ordered_ids=[third.id, first.id, second.id],
    )

    db.refresh(other)
    assert other.sort_order == 7
    assert other.date == yesterday
    assert [item.task.title for item in _planned_queue(db, yesterday)] == [
        "Yesterday item",
    ]


def test_reorder_daily_tasks_rejects_duplicates_without_writing(
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )

    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[third.id, third.id, first.id],
        )

    assert [
        item.task.title
        for item in _planned_queue(db, target_date)
    ] == ["Alpha", "Beta", "Gamma"]
    assert [
        item.sort_order
        for item in _planned_queue(db, target_date)
    ] == [0, 1, 2]


def test_reorder_daily_tasks_rejects_omitted_id_without_writing(
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )

    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[third.id, first.id],
        )

    assert [
        item.sort_order
        for item in _planned_queue(db, target_date)
    ] == [0, 1, 2]


def test_reorder_daily_tasks_rejects_empty_payload_when_queue_is_not_empty(
    db,
    make_task,
):
    target_date = today()
    _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta"],
        target_date,
    )

    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[],
        )

    assert [
        item.task.title
        for item in _planned_queue(db, target_date)
    ] == ["Alpha", "Beta"]


def test_reorder_daily_tasks_rejects_wrong_day_and_extra_ids(
    db,
    make_task,
):
    target_date = today()
    yesterday = target_date - timedelta(days=1)
    first, second = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta"],
        target_date,
    )
    other = add_task_to_day(
        db=db,
        task=make_task("Other day"),
        target_date=yesterday,
        planned_sessions=1,
    )

    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[other.id, first.id],
        )

    assert [
        item.task.title
        for item in _planned_queue(db, target_date)
    ] == ["Alpha", "Beta"]
    db.refresh(other)
    assert other.sort_order == 0


def test_reorder_daily_tasks_rejects_removed_completed_and_cancelled(
    db,
    make_task,
    make_daily_task,
):
    target_date = today()
    first, second = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta"],
        target_date,
    )
    removed = add_task_to_day(
        db=db,
        task=make_task("Removed item"),
        target_date=target_date,
        planned_sessions=1,
    )
    remove_task_from_day(db, removed)
    completed = make_daily_task(
        make_task("Completed item"),
        target_date=target_date,
        state="completed",
        sort_order=9,
    )
    cancelled = add_task_to_day(
        db=db,
        task=make_task("Cancelled item"),
        target_date=target_date,
        planned_sessions=1,
    )
    cancel_task(db, cancelled.task)

    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[removed.id, first.id, second.id],
        )
    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[completed.id, first.id, second.id],
        )
    with pytest.raises(ValueError, match="exactly once"):
        reorder_daily_tasks(
            db=db,
            target_date=target_date,
            ordered_ids=[cancelled.id, first.id, second.id],
        )

    queued = _planned_queue(db, target_date)
    assert [item.task.title for item in queued] == [
        "Alpha",
        "Beta",
    ]
    assert [item.sort_order for item in queued] == [0, 1]
    db.refresh(completed)
    assert completed.sort_order == 9
    db.refresh(removed)
    assert removed.state == "removed"


def test_today_page_queue_exposes_drag_handle_and_keeps_move_forms(
    client,
    db,
    make_task,
):
    daily_task = add_task_to_day(
        db=db,
        task=make_task("Draggable item"),
        target_date=today(),
        planned_sessions=1,
    )
    page = client.get("/today/page")

    assert page.status_code == 200
    assert "/static/today_queue.js" in page.text
    assert 'class="queue-drag-handle"' in page.text
    assert 'draggable="true"' in page.text
    assert (
        'aria-label="Drag to reorder Draggable item"'
        in page.text
    )
    assert (
        f'data-daily-task-id="{daily_task.id}"'
        in page.text
    )
    assert 'data-target-date="' in page.text
    assert 'hx-target="ol.today-queue"' in page.text
    assert (
        f'hx-post="/today/{daily_task.id}/move"'
        in page.text
    )
    assert "tabindex" not in page.text.split(
        'class="queue-drag-handle"',
        1,
    )[1].split(">", 1)[0]


def test_reorder_non_htmx_redirects(
    client,
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta", "Gamma"],
        target_date,
    )

    response = client.post(
        "/today/reorder",
        data={
            "target_date": str(target_date),
            "daily_task_id": [
                str(third.id),
                str(first.id),
                str(second.id),
            ],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/today/page?target_date={target_date}"
    )
    assert [
        item.task.title
        for item in _planned_queue(db, target_date)
    ] == ["Gamma", "Alpha", "Beta"]


def test_htmx_reorder_returns_queue_partial_in_submitted_order(
    client,
    db,
    make_task,
):
    target_date = today()
    first, second, third = _add_named_queue(
        db,
        make_task,
        ["Alpha item", "Beta item", "Gamma item"],
        target_date,
    )

    response = client.post(
        "/today/reorder",
        data={
            "target_date": str(target_date),
            "daily_task_id": [
                str(third.id),
                str(first.id),
                str(second.id),
            ],
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert _queue_rows(response.text) == [
        ("1", "Gamma item"),
        ("2", "Alpha item"),
        ("3", "Beta item"),
    ]
    assert 'class="today-queue"' in response.text
    assert "source-tasks-heading" not in response.text
    assert "date-navigation" not in response.text
    assert "today-plan-summary" not in response.text
    assert "disabled" in _move_button(
        response.text,
        "Gamma item",
        "up",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Gamma item",
        "down",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Alpha item",
        "up",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Alpha item",
        "down",
    )
    assert "disabled" not in _move_button(
        response.text,
        "Beta item",
        "up",
    )
    assert "disabled" in _move_button(
        response.text,
        "Beta item",
        "down",
    )
    assert (
        f'hx-post="/today/{third.id}/move"'
        in response.text
    )
    assert 'hx-target="ol.today-queue"' in response.text
    assert 'hx-swap="outerHTML"' in response.text
    assert 'class="queue-drag-handle"' in response.text
    assert 'name="csrf_token"' in response.text


def test_htmx_reorder_conflict_returns_html(
    client,
    db,
    make_task,
):
    target_date = today()
    first, second = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta"],
        target_date,
    )

    response = client.post(
        "/today/reorder",
        data={
            "target_date": str(target_date),
            "daily_task_id": [
                str(first.id),
                str(first.id),
            ],
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "text/html" in response.headers["content-type"]
    assert (
        response.text
        == (
            "Queue order must include each planned task "
            "for this day exactly once."
        )
    )
    assert "source-tasks-heading" not in response.text
    assert "Daily Plan" not in response.text
    assert [
        item.task.title
        for item in _planned_queue(db, target_date)
    ] == ["Alpha", "Beta"]


def test_reorder_non_htmx_conflict_remains_json(
    client,
    db,
    make_task,
):
    target_date = today()
    first, second = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta"],
        target_date,
    )

    response = client.post(
        "/today/reorder",
        data={
            "target_date": str(target_date),
            "daily_task_id": [str(second.id)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Queue order must include each planned task "
            "for this day exactly once."
        ),
    }
    assert [
        item.sort_order
        for item in _planned_queue(db, target_date)
    ] == [0, 1]


def test_reorder_requires_csrf(
    client,
    db,
    make_task,
):
    target_date = today()
    first, second = _add_named_queue(
        db,
        make_task,
        ["Alpha", "Beta"],
        target_date,
    )
    client.inject_csrf = False

    response = client.post(
        "/today/reorder",
        data={
            "target_date": str(target_date),
            "daily_task_id": [
                str(second.id),
                str(first.id),
            ],
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert [
        item.task.title
        for item in _planned_queue(db, target_date)
    ] == ["Alpha", "Beta"]


def test_today_page_all_areas_shows_full_active_source_tree(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)
    page = client.get("/today/page")

    assert page.status_code == 200
    titles = _source_tree_titles(page.text)
    assert titles == [
        tree["parent"].title,
        tree["matching_child"].title,
        tree["sibling"].title,
        tree["general"].title,
    ]
    assert f'value="{ALL_AREAS_FILTER}"' in page.text
    for area in TASK_AREAS:
        assert f'value="{area}"' in page.text


def test_development_child_retains_nonmatching_parent_context(
    client,
    db,
    make_task,
):
    parent_area = next(
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    )
    child_area = next(
        item
        for item in TASK_AREAS
        if item not in {DEFAULT_TASK_AREA, parent_area}
    )
    parent = make_task(
        "Context parent",
        area=parent_area,
    )
    child = make_task(
        "Matching descendant",
        parent=parent,
        area=child_area,
    )
    unrelated = make_task(
        "Unrelated root",
        area=parent_area,
    )

    response = client.get(
        "/today/source-tree",
        params={"area": child_area},
    )

    assert response.status_code == 200
    assert _source_tree_titles(response.text) == [
        parent.title,
        child.title,
    ]
    assert unrelated.title not in response.text
    db.refresh(parent)
    db.refresh(child)
    assert parent.area == parent_area
    assert child.area == child_area


def test_source_tree_filter_keeps_matching_child_and_parent(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)
    target_date = today()

    response = client.get(
        "/today/source-tree",
        params={
            "target_date": str(target_date),
            "area": tree["child_area"],
        },
    )

    assert response.status_code == 200
    assert 'id="today-source-body"' in response.text
    assert 'id="today-queue-body"' not in response.text
    assert "source-tasks-heading" not in response.text
    assert 'id="area-filter"' not in response.text
    titles = _source_tree_titles(response.text)
    assert titles == [
        tree["parent"].title,
        tree["matching_child"].title,
    ]
    assert tree["sibling"].title not in response.text
    assert tree["general"].title not in response.text


def test_source_tree_filter_matching_parent_omits_nonmatching_children(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)

    response = client.get(
        "/today/source-tree",
        params={"area": tree["parent_area"]},
    )

    assert response.status_code == 200
    titles = _source_tree_titles(response.text)
    assert titles == [tree["parent"].title]
    assert tree["matching_child"].title not in response.text
    assert tree["sibling"].title not in response.text


def test_source_tree_filter_each_allowed_area(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)
    expected = {
        tree["parent_area"]: [tree["parent"].title],
        tree["child_area"]: [
            tree["parent"].title,
            tree["matching_child"].title,
        ],
        tree["sibling_area"]: [
            tree["parent"].title,
            tree["sibling"].title,
        ],
        DEFAULT_TASK_AREA: [tree["general"].title],
    }

    for area in TASK_AREAS:
        response = client.get(
            "/today/source-tree",
            params={"area": area},
        )
        assert response.status_code == 200, area
        assert _source_tree_titles(response.text) == (
            expected[area]
        )


def test_source_tree_all_restores_full_tree(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)

    filtered = client.get(
        "/today/source-tree",
        params={"area": tree["child_area"]},
    )
    restored = client.get(
        "/today/source-tree",
        params={"area": ALL_AREAS_FILTER},
    )

    assert filtered.status_code == 200
    assert restored.status_code == 200
    assert tree["sibling"].title not in filtered.text
    assert _source_tree_titles(restored.text) == [
        tree["parent"].title,
        tree["matching_child"].title,
        tree["sibling"].title,
        tree["general"].title,
    ]


def test_source_tree_filter_does_not_mutate_tasks_or_queue(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)
    target_date = today()
    daily = add_task_to_day(
        db=db,
        task=tree["parent"],
        target_date=target_date,
        planned_sessions=2,
    )
    area_before = {
        task.id: task.area
        for task in db.query(Task)
    }
    state_before = daily.state
    planned_before = daily.planned_sessions

    response = client.get(
        "/today/source-tree",
        params={
            "target_date": str(target_date),
            "area": tree["child_area"],
        },
    )
    page = client.get(
        "/today/page",
        params={"target_date": str(target_date)},
    )

    assert response.status_code == 200
    assert 'id="today-queue-body"' not in response.text
    db.expire_all()
    assert {
        task.id: task.area
        for task in db.query(Task)
    } == area_before
    db.refresh(daily)
    assert daily.state == state_before
    assert daily.planned_sessions == planned_before
    assert _queue_rows(page.text) == [
        ("1", tree["parent"].title),
    ]
    assert tree["parent"].title in page.text


def test_source_tree_invalid_area_is_rejected(
    client,
    db,
    make_task,
):
    make_task("Untouched")
    before = db.query(Task).count()

    response = client.get(
        "/today/source-tree",
        params={"area": "not-an-area"},
    )

    assert response.status_code == 409
    assert "Invalid task area" in response.text
    assert db.query(Task).count() == before


def test_htmx_add_while_filtered_preserves_area_and_updates_queue(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)
    target_date = today()
    add_task_to_day(
        db=db,
        task=tree["general"],
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        "/today/add-from-page",
        data={
            "task_id": str(tree["matching_child"].id),
            "target_date": str(target_date),
            "planned_sessions": "1",
            "area": tree["child_area"],
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    titles = _source_tree_titles(response.text)
    assert titles == [
        tree["parent"].title,
        tree["matching_child"].title,
    ]
    assert tree["sibling"].title not in titles
    assert tree["general"].title not in titles
    assert _queue_rows(response.text) == [
        ("1", tree["general"].title),
        ("2", tree["matching_child"].title),
    ]
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert 'id="today-source-body"' in response.text
    assert 'id="today-queue-body"' in response.text


def test_htmx_remove_while_filtered_preserves_area_and_updates_queue(
    client,
    db,
    make_task,
):
    tree = _mixed_area_source_tasks(db, make_task)
    target_date = today()
    kept = add_task_to_day(
        db=db,
        task=tree["general"],
        target_date=target_date,
        planned_sessions=1,
    )
    removed = add_task_to_day(
        db=db,
        task=tree["matching_child"],
        target_date=target_date,
        planned_sessions=1,
    )

    response = client.post(
        f"/today/{removed.id}/remove-from-page",
        data={
            "target_date": str(target_date),
            "area": tree["child_area"],
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    titles = _source_tree_titles(response.text)
    assert titles == [
        tree["parent"].title,
        tree["matching_child"].title,
    ]
    assert tree["sibling"].title not in titles
    assert _queue_rows(response.text) == [
        ("1", tree["general"].title),
    ]
    assert kept.id
    assert 'hx-swap-oob="outerHTML"' in response.text

