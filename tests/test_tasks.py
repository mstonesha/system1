import re

import pytest

from app.models import Task
from app.services.tasks import (
    ALL_AREAS_FILTER,
    DEFAULT_TASK_AREA,
    TASK_AREAS,
    TASK_PATH_SEPARATOR,
    cancel_task,
    complete_task,
    create_task,
    filter_task_tree_by_area,
    normalize_area_filter,
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
