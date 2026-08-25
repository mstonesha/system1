import ast
import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from app.analysis import (
    DEFAULT_LIMIT,
    get_change_summary,
    get_interruption_summary,
    get_planning_summary,
    get_stuck_task_drilldown,
    get_task_age_summary,
    get_temporal_summary,
    get_terminal_task_age_drilldown,
)
from app.api import analysis_models
from app.config import ANALYSIS_API_TOKEN_ENV, get_settings
from app.models import DailyTask, Task, WorkSession
from tests.test_analysis import (
    FROM_DATE,
    TO_DATE,
    _seed_contract_data,
    _snapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = REPO_ROOT / "app" / "analysis"
ANALYSIS_ROUTE = (
    REPO_ROOT / "app" / "routes" / "analysis.py"
)
ANALYSIS_API_DIR = REPO_ROOT / "app" / "api"
PERIOD = {
    "from_date": FROM_DATE.isoformat(),
    "to_date": TO_DATE.isoformat(),
}
ANALYSIS_PATHS = (
    "/api/analysis/temporal",
    "/api/analysis/interruptions",
    "/api/analysis/task-age",
    "/api/analysis/task-age/drilldown",
    "/api/analysis/planning",
    "/api/analysis/change",
    "/api/analysis/stuck-tasks",
)
TEST_ANALYSIS_API_TOKEN = (
    "test-analysis-api-token-not-a-real-secret"
)
AUTH_HEADERS = {
    "Authorization": f"Bearer {TEST_ANALYSIS_API_TOKEN}",
}
INTERPRETIVE_KEYS = {
    "recommendation",
    "conclusion",
    "pattern",
    "hypothesis",
    "significance",
    "confidence",
    "best",
    "overload",
    "risk",
    "interpretation",
    "advice",
}


def _json_default(value):
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value)!r}")


def _contract_json(value):
    return json.loads(
        json.dumps(
            dataclasses.asdict(value),
            default=_json_default,
        )
    )


def _collect_keys(value):
    keys = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def _pydantic_field_names(cls, seen=None):
    from pydantic import BaseModel

    if seen is None:
        seen = set()
    if cls in seen or not isinstance(cls, type):
        return set()
    seen.add(cls)
    names = set()
    if not issubclass(cls, BaseModel):
        return names
    for name, field in cls.model_fields.items():
        names.add(name)
        annotation = field.annotation
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ())
        candidates = args if origin is not None else (annotation,)
        for candidate in candidates:
            names.update(_pydantic_field_names(candidate, seen))
    return names


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(
                alias.name for alias in node.names
            )
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


@pytest.fixture(autouse=True)
def configure_analysis_api_token(monkeypatch):
    monkeypatch.setenv(
        ANALYSIS_API_TOKEN_ENV,
        TEST_ANALYSIS_API_TOKEN,
    )


def test_analysis_router_is_registered(client):
    spec = client.get("/openapi.json").json()
    for path in ANALYSIS_PATHS:
        assert path in spec["paths"]
        assert "get" in spec["paths"][path]
        assert list(spec["paths"][path]) == ["get"]


def test_docs_include_analysis_tag(client):
    docs = client.get("/docs")
    spec = client.get("/openapi.json").json()
    assert docs.status_code == 200
    tagged = {
        tag
        for path in ANALYSIS_PATHS
        for operation in spec["paths"][path].values()
        for tag in operation.get("tags", [])
    }
    assert "Analysis" in tagged


def test_html_routes_still_respond(client):
    assert client.get("/").status_code == 200
    assert client.get("/today/page").status_code == 200
    assert client.get("/review/").status_code == 200
    assert client.get("/timer/").status_code == 200


def test_temporal_endpoint_matches_contract(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    before = _snapshot(db)
    response = client.get(
        "/api/analysis/temporal",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_temporal_summary(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == expected
    assert payload["period"]["timezone"] == (
        get_settings().timezone
    )
    db.expire_all()
    assert _snapshot(db) == before


def test_interruptions_endpoint_matches_contract(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    response = client.get(
        "/api/analysis/interruptions",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_interruption_summary(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert response.status_code == 200
    assert response.json() == expected


def test_task_age_endpoint_matches_contract(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    response = client.get(
        "/api/analysis/task-age",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_task_age_summary(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == expected
    assert "tasks" not in payload
    assert "buckets" in payload


def test_task_age_drilldown_default_and_bounds(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    defaulted = client.get(
        "/api/analysis/task-age/drilldown",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_terminal_task_age_drilldown(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert defaulted.status_code == 200
    assert defaulted.json() == expected
    assert defaulted.json()["limit"] == DEFAULT_LIMIT
    zero = client.get(
        "/api/analysis/task-age/drilldown",
        params={**PERIOD, "limit": 0},
        headers=AUTH_HEADERS,
    )
    high = client.get(
        "/api/analysis/task-age/drilldown",
        params={**PERIOD, "limit": 51},
        headers=AUTH_HEADERS,
    )
    assert zero.status_code == 422
    assert high.status_code == 422


def test_planning_endpoint_keeps_subfamilies(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    response = client.get(
        "/api/analysis/planning",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_planning_summary(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == expected
    assert "daily_planning" in payload
    assert "task_effort" in payload
    assert "weekly_workload" in payload
    assert "planning_score" not in payload


def test_change_endpoint_exposes_actual_windows(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    requested_from = date(2026, 1, 18)
    requested_to = date(2026, 1, 18)
    response = client.get(
        "/api/analysis/change",
        params={
            "from_date": requested_from.isoformat(),
            "to_date": requested_to.isoformat(),
        },
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_change_summary(
            db,
            from_date=requested_from,
            to_date=requested_to,
        )
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == expected
    assert payload["recent_8w"]["from_date"] < (
        requested_from.isoformat()
    )
    assert payload["recent_16w"]["from_date"] < (
        requested_from.isoformat()
    )
    assert payload["preceding_16w"]["from_date"] < (
        payload["recent_16w"]["from_date"]
    )
    assert payload["recent_8w"]["to_date"] == (
        requested_to.isoformat()
    )


def test_stuck_tasks_endpoint_default_and_bounds(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    defaulted = client.get(
        "/api/analysis/stuck-tasks",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    expected = _contract_json(
        get_stuck_task_drilldown(
            db,
            from_date=FROM_DATE,
            to_date=TO_DATE,
        )
    )
    assert defaulted.status_code == 200
    assert defaulted.json() == expected
    assert defaulted.json()["limit"] == DEFAULT_LIMIT
    zero = client.get(
        "/api/analysis/stuck-tasks",
        params={**PERIOD, "limit": 0},
        headers=AUTH_HEADERS,
    )
    high = client.get(
        "/api/analysis/stuck-tasks",
        params={**PERIOD, "limit": 51},
        headers=AUTH_HEADERS,
    )
    assert zero.status_code == 422
    assert high.status_code == 422


def test_malformed_date_returns_422(client):
    response = client.get(
        "/api/analysis/temporal",
        params={
            "from_date": "not-a-date",
            "to_date": TO_DATE.isoformat(),
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 422


def test_reversed_dates_return_400(client):
    response = client.get(
        "/api/analysis/temporal",
        params={
            "from_date": TO_DATE.isoformat(),
            "to_date": FROM_DATE.isoformat(),
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert "from_date" in response.json()["detail"]


def test_timezone_is_server_controlled(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    response = client.get(
        "/api/analysis/temporal",
        params={
            **PERIOD,
            "timezone": "America/New_York",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["period"]["timezone"] == (
        get_settings().timezone
    )
    assert response.json()["period"]["timezone"] == (
        "Europe/London"
    )


def test_get_endpoints_do_not_mutate(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    before = _snapshot(db)
    for path in ANALYSIS_PATHS:
        response = client.get(
            path,
            params=PERIOD,
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
    db.expire_all()
    assert _snapshot(db) == before
    assert db.query(Task).count() == len(before[0])
    assert db.query(DailyTask).count() == len(before[1])
    assert db.query(WorkSession).count() == len(
        before[2]
    )


def test_analysis_route_does_not_import_analytics():
    for name in _imported_modules(ANALYSIS_ROUTE):
        assert name != "app.analytics"
        assert not name.startswith("app.analytics.")


def test_analysis_package_stays_transport_free():
    forbidden = (
        "fastapi",
        "pydantic",
        "app.routes",
        "app.api",
        "evaluation",
        "openai",
    )
    for path in ANALYSIS_DIR.glob("*.py"):
        for name in _imported_modules(path):
            assert not any(
                name == prefix
                or name.startswith(prefix + ".")
                for prefix in forbidden
            ), (path, name)


def test_response_json_has_no_interpretive_keys(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    keys = set()
    for path in ANALYSIS_PATHS:
        payload = client.get(
            path,
            params=PERIOD,
            headers=AUTH_HEADERS,
        ).json()
        keys.update(_collect_keys(payload))
    from pydantic import BaseModel

    for name in dir(analysis_models):
        cls = getattr(analysis_models, name)
        if isinstance(cls, type) and issubclass(cls, BaseModel):
            keys.update(_pydantic_field_names(cls))
    assert INTERPRETIVE_KEYS.isdisjoint(keys)
