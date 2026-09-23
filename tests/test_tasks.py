import re
from datetime import datetime, timezone

import pytest

from app.models import Task
from app.services.tasks import (
    ALL_AREAS_FILTER,
    DEFAULT_TASK_AREA,
    NEWEST_TASK_SORT,
    OLDEST_TASK_SORT,
    TASK_AREAS,
    TASK_PATH_SEPARATOR,
    cancel_task,
    complete_task,
    create_task,
    filter_task_tree_by_area,
    normalize_area_filter,
    normalize_task_sort,
    sort_task_siblings,
    task_ancestry_label,
    task_ancestor_titles,
    task_area_label,
    task_breadcrumb,
    update_task_area,
    update_task_title,
)


def test_complete_task_blocked_by_active_descendants(
    db,
    make_task,
):
    parent = make_task("Parent")
    make_task("Child", parent=parent)

    db.refresh(parent)

    with pytest.raises(
        ValueError,
        match="active subtasks",
    ):
        complete_task(db, parent)

    db.refresh(parent)
    assert parent.status == "active"
    assert parent.completed_at is None


def test_cancel_task_succeeds(db, make_task):
    task = make_task("Drop this")

    cancelled = cancel_task(db, task)

    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is None


def test_create_task_rejects_empty_title(db):
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        create_task(db, title="")

    assert db.query(Task).count() == 0


def test_create_task_rejects_whitespace_only_title(db):
    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        create_task(db, title="   \t\n  ")

    assert db.query(Task).count() == 0


def test_update_task_title_rejects_whitespace_only(db, make_task):
    task = make_task("Keep me")

    with pytest.raises(
        ValueError,
        match="cannot be empty",
    ):
        update_task_title(db, task, "   ")

    db.refresh(task)
    assert task.title == "Keep me"


def test_create_task_rejects_title_longer_than_200(db):
    with pytest.raises(
        ValueError,
        match="200 characters",
    ):
        create_task(db, title="a" * 201)

    assert db.query(Task).count() == 0


def test_update_task_title_rejects_title_longer_than_200(
    db,
    make_task,
):
    task = make_task("Short")

    with pytest.raises(
        ValueError,
        match="200 characters",
    ):
        update_task_title(db, task, "b" * 201)

    db.refresh(task)
    assert task.title == "Short"


def test_create_task_accepts_200_character_title(db):
    title = "c" * 200
    task = create_task(db, title=title)

    assert task.title == title
    assert len(task.title) == 200


def test_create_task_defaults_area_to_general(db, make_task):
    created = create_task(db, title="Unspecified area")
    factory = make_task("Factory task")

    assert created.area == DEFAULT_TASK_AREA
    assert factory.area == DEFAULT_TASK_AREA
    assert created.area is not None
    assert factory.area is not None


def test_create_task_stores_each_allowed_area(db):
    for area in TASK_AREAS:
        task = create_task(
            db,
            title=f"Task for {area}",
            area=area,
        )
        assert task.area == area
        assert task.area is not None


def test_create_task_rejects_invalid_area(db):
    with pytest.raises(
        ValueError,
        match="Invalid task area",
    ):
        create_task(
            db,
            title="Bad area",
            area="not-an-area",
        )

    assert db.query(Task).count() == 0


def test_create_subtask_inherits_parent_area(db):
    parent_area = next(
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    )
    parent = create_task(
        db,
        title="Parent",
        area=parent_area,
    )
    child = create_task(
        db,
        title="Child",
        parent_task_id=parent.id,
    )

    assert child.area == parent.area
    assert child.area == parent_area
    assert child.area is not None


def test_update_task_area_validates_and_does_not_cascade(
    db,
):
    other_areas = [
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    ]
    parent_area, next_area = other_areas[0], other_areas[1]
    parent = create_task(
        db,
        title="Parent",
        area=parent_area,
    )
    child = create_task(
        db,
        title="Child",
        parent_task_id=parent.id,
    )

    updated = update_task_area(db, parent, next_area)

    assert updated.area == next_area
    db.refresh(child)
    assert child.area == parent_area

    with pytest.raises(
        ValueError,
        match="Invalid task area",
    ):
        update_task_area(db, parent, "not-an-area")

    db.refresh(parent)
    assert parent.area == next_area


def test_filter_task_tree_preserves_ancestors_and_omits_unrelated(
    db,
):
    other_areas = [
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    ]
    parent_area, child_area, sibling_area = other_areas[:3]
    parent = create_task(
        db,
        title="Parent",
        area=parent_area,
    )
    matching_child = create_task(
        db,
        title="Matching child",
        parent_task_id=parent.id,
        area=child_area,
    )
    sibling = create_task(
        db,
        title="Sibling",
        parent_task_id=parent.id,
        area=sibling_area,
    )
    tree = [
        {
            "task": parent,
            "children": [
                {"task": matching_child, "children": []},
                {"task": sibling, "children": []},
            ],
        }
    ]

    filtered = filter_task_tree_by_area(tree, child_area)
    assert [node["task"].id for node in filtered] == [
        parent.id
    ]
    assert [
        node["task"].id
        for node in filtered[0]["children"]
    ] == [matching_child.id]

    parent_only = filter_task_tree_by_area(
        tree,
        parent_area,
    )
    assert [node["task"].id for node in parent_only] == [
        parent.id
    ]
    assert parent_only[0]["children"] == []

    unfiltered = filter_task_tree_by_area(tree, None)
    assert len(unfiltered[0]["children"]) == 2


def test_normalize_area_filter_accepts_all_and_rejects_invalid():
    assert normalize_area_filter(None) is None
    assert normalize_area_filter(ALL_AREAS_FILTER) is None
    assert normalize_area_filter("") is None

    for area in TASK_AREAS:
        assert normalize_area_filter(area) == area

    with pytest.raises(
        ValueError,
        match="Invalid task area",
    ):
        normalize_area_filter("not-an-area")


def test_create_form_stores_work_area(client, db):
    work = next(
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    )

    response = client.post(
        "/tasks/create",
        data={
            "title": "Desk task",
            "area": work,
        },
    )

    assert response.status_code == 200
    task = db.query(Task).one()
    assert task.area == work
    assert task_area_label(work) in response.text


def test_create_form_omitted_area_defaults_to_general(
    client,
    db,
):
    response = client.post(
        "/tasks/create",
        data={"title": "No area chosen"},
    )

    assert response.status_code == 200
    task = db.query(Task).one()
    assert task.area == DEFAULT_TASK_AREA


def test_create_form_rejects_invalid_area(client, db):
    response = client.post(
        "/tasks/create",
        data={
            "title": "Bad area",
            "area": "not-an-area",
        },
    )

    assert response.status_code == 409
    assert "Invalid task area" in response.text
    assert db.query(Task).count() == 0


def test_create_form_rejects_all_as_stored_area(client, db):
    response = client.post(
        "/tasks/create",
        data={
            "title": "All is not an area",
            "area": ALL_AREAS_FILTER,
        },
    )

    assert response.status_code == 409
    assert db.query(Task).count() == 0


def test_edit_form_rejects_all_as_stored_area(client, db):
    task = create_task(
        db,
        title="Keep area",
        area=DEFAULT_TASK_AREA,
    )

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": "Keep area",
            "area": ALL_AREAS_FILTER,
        },
    )

    assert response.status_code == 409
    db.refresh(task)
    assert task.area == DEFAULT_TASK_AREA
    assert task.title == "Keep area"


def test_subtask_form_has_no_area_selector_and_inherits(
    client,
    db,
):
    parent_area = next(
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    )
    parent = create_task(
        db,
        title="Parent",
        area=parent_area,
    )

    page = client.get("/")
    html = page.text
    form_at = html.index(
        f'id="subtask-form-{parent.id}"'
    )
    children_at = html.index(
        f'id="children-{parent.id}"',
        form_at,
    )
    subtask_form = html[form_at:children_at]

    assert page.status_code == 200
    assert 'name="area"' not in subtask_form
    assert "<select" not in subtask_form

    created = client.post(
        f"/tasks/{parent.id}/subtasks",
        data={"title": "Child"},
    )

    assert created.status_code == 200
    child = (
        db.query(Task)
        .filter(Task.parent_task_id == parent.id)
        .one()
    )
    assert child.area == parent_area


def test_edit_form_exposes_current_area(client, db):
    area = next(
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    )
    task = create_task(
        db,
        title="Named task",
        area=area,
    )

    page = client.get("/")
    html = page.text

    assert page.status_code == 200
    assert f'id="edit-area-{task.id}"' in html
    assert f'name="area"' in html
    assert (
        f'value="{area}"'
        in html[html.index(f'id="edit-area-{task.id}"'):]
    )
    assert "selected" in html[
        html.index(f'id="edit-area-{task.id}"'):
    ]


def test_edit_form_updates_title_and_area(client, db):
    areas = [
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    ]
    task = create_task(
        db,
        title="Before",
        area=areas[0],
    )

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": "After",
            "area": areas[1],
        },
    )

    assert response.status_code == 200
    db.refresh(task)
    assert task.title == "After"
    assert task.area == areas[1]
    assert task_area_label(areas[1]) in response.text


def test_edit_form_invalid_area_does_not_persist_title(
    client,
    db,
):
    task = create_task(
        db,
        title="Keep title",
        area=DEFAULT_TASK_AREA,
    )

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": "Should not save",
            "area": "not-an-area",
        },
    )

    assert response.status_code == 409
    db.refresh(task)
    assert task.title == "Keep title"
    assert task.area == DEFAULT_TASK_AREA


def test_edit_form_invalid_title_does_not_persist_area(
    client,
    db,
):
    original_area = DEFAULT_TASK_AREA
    next_area = next(
        item
        for item in TASK_AREAS
        if item != original_area
    )
    task = create_task(
        db,
        title="Keep title",
        area=original_area,
    )

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": "   ",
            "area": next_area,
        },
    )

    assert response.status_code == 409
    db.refresh(task)
    assert task.title == "Keep title"
    assert task.area == original_area


def test_edit_form_title_only_leaves_area_unchanged(
    client,
    db,
):
    area = next(
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    )
    task = create_task(
        db,
        title="Old title",
        area=area,
    )

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={"title": "New title"},
    )

    assert response.status_code == 200
    db.refresh(task)
    assert task.title == "New title"
    assert task.area == area


def test_edit_form_parent_area_does_not_cascade(
    client,
    db,
):
    areas = [
        item
        for item in TASK_AREAS
        if item != DEFAULT_TASK_AREA
    ]
    parent = create_task(
        db,
        title="Parent",
        area=areas[0],
    )
    child = create_task(
        db,
        title="Child",
        parent_task_id=parent.id,
    )

    response = client.post(
        f"/tasks/{parent.id}/edit",
        data={
            "title": "Parent",
            "area": areas[1],
        },
    )

    assert response.status_code == 200
    db.refresh(parent)
    db.refresh(child)
    assert parent.area == areas[1]
    assert child.area == areas[0]


def test_task_list_renders_area_labels_on_every_row(
    client,
    db,
):
    titles = {}
    for area in TASK_AREAS:
        task = create_task(
            db,
            title=f"{area} root",
            area=area,
        )
        titles[task.id] = task_area_label(area)

    page = client.get("/")
    html = page.text

    assert page.status_code == 200
    labels = _task_area_labels(html)
    assert labels == [
        titles[task.id]
        for task in db.query(Task).order_by(Task.id)
    ]
    assert "task-status" not in html or _task_status_labels(
        html
    ) == []
    for area in TASK_AREAS:
        assert task_area_label(area) in html
        assert f'value="{area}"' in html


def test_task_list_area_labels_stay_separate_from_status(
    client,
    db,
    make_task,
):
    done = make_task("Done work", area=DEFAULT_TASK_AREA)
    complete_task(db, done)

    tree = client.get(
        "/task-tree",
        params={"show_inactive": True},
    )

    assert tree.status_code == 200
    assert _task_status_labels(tree.text) == ["Completed"]
    assert _task_area_labels(tree.text) == [
        task_area_label(DEFAULT_TASK_AREA)
    ]
    assert 'class="task-area"' in tree.text
    assert 'class="task-status"' in tree.text


def test_root_task_has_no_ancestry(db, make_task):
    task = make_task("Root work")

    assert task_ancestor_titles(task) == []
    assert task_ancestry_label(task) == ""
    assert task_breadcrumb(task) == "Root work"


def test_one_level_nested_task_ancestry(db, make_task):
    parent = make_task("Build Akrasia_Zero")
    child = make_task(
        "Write Installation Documents",
        parent=parent,
    )

    assert task_ancestor_titles(child) == [
        "Build Akrasia_Zero",
    ]
    assert task_ancestry_label(child) == (
        "Build Akrasia_Zero"
    )
    assert task_breadcrumb(child) == (
        "Build Akrasia_Zero"
        + TASK_PATH_SEPARATOR
        + "Write Installation Documents"
    )


def test_deeper_nested_task_ancestry(db, make_task):
    root = make_task("Build Akrasia_Zero")
    branch = make_task(
        "Documentation",
        parent=root,
    )
    leaf = make_task(
        "Write Installation Documents",
        parent=branch,
    )

    assert task_ancestor_titles(leaf) == [
        "Build Akrasia_Zero",
        "Documentation",
    ]
    assert task_breadcrumb(leaf) == (
        "Build Akrasia_Zero"
        + TASK_PATH_SEPARATOR
        + "Documentation"
        + TASK_PATH_SEPARATOR
        + "Write Installation Documents"
    )


def _task_status_labels(html: str) -> list[str]:
    return re.findall(
        r'class="task-status"\s*>\s*([^<]+?)\s*<',
        html,
    )


def _task_area_labels(html: str) -> list[str]:
    return re.findall(
        r'class="task-area"\s*>\s*([^<]+?)\s*<',
        html,
    )


def test_task_list_active_task_has_no_status_label(
    client,
    make_task,
):
    make_task("Quiet work")

    page = client.get("/")

    assert page.status_code == 200
    assert "Quiet work" in page.text
    assert _task_status_labels(page.text) == []


def test_task_list_hides_completed_and_cancelled_by_default(
    client,
    db,
    make_task,
):
    done = make_task("Done work")
    dropped = make_task("Dropped work")
    complete_task(db, done)
    cancel_task(db, dropped)

    page = client.get("/")
    tree = client.get("/task-tree")

    assert page.status_code == 200
    assert "Done work" not in page.text
    assert "Dropped work" not in page.text
    assert tree.status_code == 200
    assert "Done work" not in tree.text
    assert "Dropped work" not in tree.text
    assert _task_status_labels(tree.text) == []


def test_task_list_completed_status_uses_canonical_wording(
    client,
    db,
    make_task,
):
    done = make_task("Done work")
    complete_task(db, done)

    tree = client.get(
        "/task-tree",
        params={"show_inactive": True},
    )

    assert tree.status_code == 200
    assert "Done work" in tree.text
    assert _task_status_labels(tree.text) == ["Completed"]
    assert "Abandoned" not in tree.text


def test_task_list_cancelled_status_uses_canonical_wording(
    client,
    db,
    make_task,
):
    dropped = make_task("Dropped work")
    cancel_task(db, dropped)

    tree = client.get(
        "/task-tree",
        params={"show_inactive": True},
    )

    assert tree.status_code == 200
    assert "Dropped work" in tree.text
    assert _task_status_labels(tree.text) == ["Cancelled"]
    assert "Abandoned" not in tree.text


def test_task_list_hierarchy_remains_intact_with_inactive_child(
    client,
    db,
    make_task,
):
    parent = make_task("Parent work")
    child = make_task("Child work", parent=parent)
    complete_task(db, child)

    hidden = client.get("/")
    shown = client.get(
        "/task-tree",
        params={"show_inactive": True},
    )

    assert hidden.status_code == 200
    assert "Parent work" in hidden.text
    assert "Child work" not in hidden.text
    assert _task_status_labels(hidden.text) == []

    assert shown.status_code == 200
    assert f'id="children-{parent.id}"' in shown.text
    assert "Parent work" in shown.text
    assert "Child work" in shown.text
    assert _task_status_labels(shown.text) == ["Completed"]


def test_task_list_add_new_task_appears_before_tree(
    client,
    make_task,
):
    make_task("Quiet work")

    page = client.get("/")

    assert page.status_code == 200
    html = page.text

    heading_at = html.index('class="task-list-heading"')
    add_at = html.index('class="new-task-area"')
    tree_at = html.index('class="task-tree"')

    assert heading_at < add_at < tree_at
    assert html.count('id="new-task-form"') == 1
    assert 'hx-post="/tasks/create"' in html
    assert 'hx-target="#task-list"' in html


def test_task_list_active_actions_have_visible_text_and_labels(
    client,
    make_task,
):
    make_task("Quiet work")

    page = client.get("/")

    assert page.status_code == 200
    html = page.text

    title_at = html.index('id="task-title-')
    add_at = html.index("+ Add subtask")
    complete_at = html.index('aria-label="Complete Quiet work"')
    cancel_at = html.index('aria-label="Cancel Quiet work"')
    edit_at = html.index('aria-label="Edit Quiet work"')

    assert title_at < add_at < complete_at < cancel_at < edit_at
    assert 'title="Complete task"' in html
    assert 'title="Cancel task"' in html
    assert 'title="Edit task"' in html
    assert 'aria-label="Add subtask to Quiet work"' in html
    assert 'role="group"' in html
    assert 'aria-label="Actions for Quiet work"' in html
    assert 'for="edit-title-' in html
    assert 'for="subtask-title-' in html
    assert 'for="new-task-title"' in html
    assert re.search(
        r'aria-label="Complete Quiet work"[^>]*>\s*'
        r'<span aria-hidden="true">✓</span>\s*</button>',
        html,
    )
    assert re.search(
        r'aria-label="Cancel Quiet work"[^>]*>\s*'
        r'<span aria-hidden="true">×</span>\s*</button>',
        html,
    )
    assert re.search(
        r'aria-label="Edit Quiet work"[^>]*>\s*'
        r'<span aria-hidden="true">✎</span>\s*Edit\s*</button>',
        html,
    )
    assert 'hx-post="/tasks/' in html
    assert "Reopen" not in html


def test_task_list_inactive_rows_keep_labelled_reopen(
    client,
    db,
    make_task,
):
    done = make_task("Done work")
    dropped = make_task("Dropped work")
    complete_task(db, done)
    cancel_task(db, dropped)

    tree = client.get(
        "/task-tree",
        params={"show_inactive": True},
    )

    assert tree.status_code == 200
    assert f'hx-post="/tasks/{done.id}/reopen"' in tree.text
    assert f'hx-post="/tasks/{dropped.id}/reopen"' in tree.text
    assert 'aria-label="Reopen Done work"' in tree.text
    assert 'aria-label="Reopen Dropped work"' in tree.text
    assert 'title="Reopen task"' in tree.text
    assert _task_status_labels(tree.text) == [
        "Completed",
        "Cancelled",
    ]
    assert "+ Add subtask" not in tree.text
    assert 'aria-label="Complete Done work"' not in tree.text


def test_task_list_actions_keep_hierarchy_and_status(
    client,
    db,
    make_task,
):
    parent = make_task("Parent work")
    child = make_task("Child work", parent=parent)
    complete_task(db, child)

    shown = client.get(
        "/task-tree",
        params={"show_inactive": True},
    )

    assert shown.status_code == 200
    assert f'id="children-{parent.id}"' in shown.text
    assert 'aria-label="Complete Parent work"' in shown.text
    assert 'aria-label="Add subtask to Parent work"' in shown.text
    assert 'aria-label="Reopen Child work"' in shown.text
    assert _task_status_labels(shown.text) == ["Completed"]


def _moment(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def _task_titles(html: str) -> list[str]:
    return re.findall(
        r'id="task-title-\d+"[^>]*>\s*([^<]+?)\s*</span>',
        html,
        re.S,
    )


def test_sort_task_siblings_returns_a_copy():
    older = Task(
        id=2,
        title="Older",
        created_at=_moment(1),
        sort_order=8,
    )
    newer = Task(
        id=1,
        title="Newer",
        created_at=_moment(2),
        sort_order=1,
    )
    original = [newer, older]

    ordered = sort_task_siblings(original, OLDEST_TASK_SORT)

    assert [task.title for task in original] == [
        "Newer",
        "Older",
    ]
    assert [task.title for task in ordered] == [
        "Older",
        "Newer",
    ]
    assert older.sort_order == 8
    assert newer.sort_order == 1


def test_normalize_task_sort_defaults_and_rejects():
    assert normalize_task_sort(None) == OLDEST_TASK_SORT
    assert normalize_task_sort("") == OLDEST_TASK_SORT
    assert normalize_task_sort("  ") == OLDEST_TASK_SORT
    assert normalize_task_sort(NEWEST_TASK_SORT) == (
        NEWEST_TASK_SORT
    )

    with pytest.raises(ValueError, match="Invalid task sort"):
        normalize_task_sort("sideways")


def test_task_list_oldest_and_newest_root_order(client, make_task):
    make_task("January", created_at=_moment(1), sort_order=5)
    make_task("March", created_at=_moment(3), sort_order=1)
    make_task("February", created_at=_moment(2), sort_order=9)

    oldest = client.get("/task-tree")
    newest = client.get(
        "/task-tree",
        params={"sort": NEWEST_TASK_SORT},
    )

    assert oldest.status_code == 200
    assert newest.status_code == 200
    assert _task_titles(oldest.text) == [
        "January",
        "February",
        "March",
    ]
    assert _task_titles(newest.text) == [
        "March",
        "February",
        "January",
    ]


def test_task_list_id_tie_break_follows_sort_direction(
    client,
    make_task,
):
    same_time = _moment(10)
    make_task("First tie", created_at=same_time)
    make_task("Second tie", created_at=same_time)

    oldest = client.get("/task-tree")
    newest = client.get(
        "/task-tree",
        params={"sort": NEWEST_TASK_SORT},
    )

    assert _task_titles(oldest.text) == [
        "First tie",
        "Second tie",
    ]
    assert _task_titles(newest.text) == [
        "Second tie",
        "First tie",
    ]


def test_task_list_sorts_each_sibling_group(client, make_task):
    parent_a = make_task(
        "Parent A",
        created_at=_moment(1),
    )
    make_task(
        "Child A1",
        parent=parent_a,
        created_at=_moment(3),
    )
    make_task(
        "Child A2",
        parent=parent_a,
        created_at=_moment(2),
    )
    parent_b = make_task(
        "Parent B",
        created_at=_moment(4),
    )
    child_b1 = make_task(
        "Child B1",
        parent=parent_b,
        created_at=_moment(5),
    )
    make_task(
        "Grandchild later",
        parent=child_b1,
        created_at=_moment(7),
    )
    make_task(
        "Grandchild earlier",
        parent=child_b1,
        created_at=_moment(6),
    )

    oldest = client.get("/task-tree")
    newest = client.get(
        "/task-tree",
        params={"sort": NEWEST_TASK_SORT},
    )

    assert _task_titles(oldest.text) == [
        "Parent A",
        "Child A2",
        "Child A1",
        "Parent B",
        "Child B1",
        "Grandchild earlier",
        "Grandchild later",
    ]
    assert _task_titles(newest.text) == [
        "Parent B",
        "Child B1",
        "Grandchild later",
        "Grandchild earlier",
        "Parent A",
        "Child A1",
        "Child A2",
    ]
    assert f'id="children-{parent_a.id}"' in oldest.text
    assert f'id="children-{child_b1.id}"' in newest.text


def test_shown_inactive_tasks_keep_chronological_position(
    client,
    db,
    make_task,
):
    completed = make_task(
        "Completed early",
        created_at=_moment(1),
    )
    make_task("Active middle", created_at=_moment(2))
    cancelled = make_task(
        "Cancelled late",
        created_at=_moment(3),
    )
    complete_task(db, completed)
    cancel_task(db, cancelled)

    hidden = client.get(
        "/task-tree",
        params={"sort": NEWEST_TASK_SORT},
    )
    shown = client.get(
        "/task-tree",
        params={
            "show_inactive": True,
            "sort": NEWEST_TASK_SORT,
        },
    )

    assert _task_titles(hidden.text) == ["Active middle"]
    assert _task_titles(shown.text) == [
        "Cancelled late",
        "Active middle",
        "Completed early",
    ]
    assert _task_status_labels(shown.text) == [
        "Cancelled",
        "Completed",
    ]


def test_task_sort_control_defaults_outside_the_tree(client):
    page = client.get("/")
    html = page.text
    sort_at = html.index('id="task-sort"')
    list_at = html.index('id="task-list"')
    select_html = html[sort_at:html.index("</select>", sort_at)]

    assert page.status_code == 200
    assert sort_at < list_at
    assert 'hx-get="/task-tree"' in html
    assert 'hx-target="#task-list"' in html
    assert 'hx-include="#show-inactive, #task-sort"' in html
    assert "Oldest first" in select_html
    assert "Newest first" in select_html
    assert 'value="oldest"' in select_html
    assert "selected" in select_html
    leftover = html.replace(
        'hx-include="#show-inactive, #task-sort"',
        "",
    )
    assert 'hx-include="#show-inactive"' not in leftover


def test_task_mutations_preserve_newest_sort(
    client,
    db,
    make_task,
):
    january = make_task("January", created_at=_moment(1))
    february = make_task("February", created_at=_moment(2))
    march = make_task("March", created_at=_moment(3))

    created = client.post(
        "/tasks/create",
        data={
            "title": "April",
            "sort": NEWEST_TASK_SORT,
        },
    )
    assert _task_titles(created.text)[:4] == [
        "April",
        "March",
        "February",
        "January",
    ]

    edited = client.post(
        f"/tasks/{january.id}/edit",
        data={
            "title": "January",
            "sort": NEWEST_TASK_SORT,
        },
    )
    assert _task_titles(edited.text)[0] == "April"
    assert _task_titles(edited.text)[-1] == "January"

    completed = client.post(
        f"/tasks/{february.id}/complete",
        data={
            "show_inactive": "true",
            "sort": NEWEST_TASK_SORT,
        },
    )
    assert _task_titles(completed.text) == [
        "April",
        "March",
        "February",
        "January",
    ]

    cancelled = client.post(
        f"/tasks/{march.id}/cancel",
        data={
            "show_inactive": "true",
            "sort": NEWEST_TASK_SORT,
        },
    )
    assert _task_titles(cancelled.text)[0] == "April"
    assert "March" in _task_titles(cancelled.text)

    reopened = client.post(
        f"/tasks/{february.id}/reopen",
        data={"sort": NEWEST_TASK_SORT},
    )
    assert _task_titles(reopened.text)[0] == "April"
    assert "February" in _task_titles(reopened.text)

    nested = client.post(
        f"/tasks/{january.id}/subtasks",
        data={
            "title": "January child",
            "sort": NEWEST_TASK_SORT,
        },
    )
    titles = _task_titles(nested.text)
    assert titles[0] == "April"
    january_at = titles.index("January")
    assert titles[january_at + 1] == "January child"

    toggled = client.get(
        "/task-tree",
        params={
            "show_inactive": True,
            "sort": NEWEST_TASK_SORT,
        },
    )
    assert _task_titles(toggled.text)[0] == "April"
    db.refresh(january)
    assert january.sort_order == 0


def test_omitted_sort_is_oldest_and_invalid_sort_is_rejected(
    client,
    db,
    make_task,
):
    make_task("January", created_at=_moment(1))
    make_task("March", created_at=_moment(3))
    before = db.query(Task).count()

    omitted = client.get("/task-tree")
    blank = client.get("/task-tree", params={"sort": "  "})
    invalid = client.get(
        "/task-tree",
        params={"sort": "sideways"},
    )
    rejected = client.post(
        "/tasks/create",
        data={
            "title": "Should not persist",
            "sort": "sideways",
        },
    )

    assert _task_titles(omitted.text) == ["January", "March"]
    assert _task_titles(blank.text) == ["January", "March"]
    assert invalid.status_code == 409
    assert "Invalid task sort" in invalid.text
    assert rejected.status_code == 409
    assert db.query(Task).count() == before


def test_sorting_does_not_rewrite_sort_order(client, db, make_task):
    task = make_task(
        "Pinned",
        created_at=_moment(1),
        sort_order=7,
    )

    response = client.get(
        "/task-tree",
        params={"sort": NEWEST_TASK_SORT},
    )

    assert response.status_code == 200
    db.refresh(task)
    assert task.sort_order == 7
