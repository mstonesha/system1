import ast
import json
import logging
from pathlib import Path

import pytest

from app.api.auth import AUTH_DETAIL, require_analysis_api_token
from app.config import ANALYSIS_API_TOKEN_ENV
from app.models import DailyTask, Task, WorkSession
from tests.test_analysis import (
    _seed_contract_data,
    _snapshot,
)
from tests.test_analysis_api import (
    ANALYSIS_PATHS,
    AUTH_HEADERS,
    PERIOD,
    TEST_ANALYSIS_API_TOKEN,
    _imported_modules,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH_MODULE = REPO_ROOT / "app" / "api" / "auth.py"
ANALYSIS_ROUTE = (
    REPO_ROOT / "app" / "routes" / "analysis.py"
)
ANALYSIS_DIR = REPO_ROOT / "app" / "analysis"
HTML_PATHS = (
    "/",
    "/today/page",
    "/timer/",
    "/review/",
)
WRONG_TOKEN = (
    "wrong-analysis-api-token-not-a-real-secret"
)
GENERIC_401 = {
    "detail": AUTH_DETAIL,
}


@pytest.fixture(autouse=True)
def configure_analysis_api_token(monkeypatch):
    monkeypatch.setenv(
        ANALYSIS_API_TOKEN_ENV,
        TEST_ANALYSIS_API_TOKEN,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_unauthorized(response):
    assert response.status_code == 401
    assert response.json() == GENERIC_401
    assert (
        response.headers["www-authenticate"] == "Bearer"
    )


def test_anonymous_requests_are_unauthorized(client):
    for path in ANALYSIS_PATHS:
        response = client.get(path, params=PERIOD)
        _assert_unauthorized(response)


def test_incorrect_bearer_token_is_unauthorized(client):
    headers = _bearer(WRONG_TOKEN)
    for path in ANALYSIS_PATHS:
        response = client.get(
            path,
            params=PERIOD,
            headers=headers,
        )
        _assert_unauthorized(response)


def test_correct_token_reaches_normal_behaviour(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    for path in ANALYSIS_PATHS:
        response = client.get(
            path,
            params=PERIOD,
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        payload = response.json()
        assert "period" in payload
        dumped = json.dumps(payload)
        assert TEST_ANALYSIS_API_TOKEN not in dumped
        assert WRONG_TOKEN not in dumped


def test_www_authenticate_bearer_on_401(client):
    response = client.get(
        "/api/analysis/temporal",
        params=PERIOD,
    )
    _assert_unauthorized(response)


def test_wrong_auth_scheme_is_rejected(client):
    for header in (
        f"Basic {TEST_ANALYSIS_API_TOKEN}",
        f"Token {TEST_ANALYSIS_API_TOKEN}",
        f"Digest {TEST_ANALYSIS_API_TOKEN}",
    ):
        response = client.get(
            "/api/analysis/temporal",
            params=PERIOD,
            headers={"Authorization": header},
        )
        _assert_unauthorized(response)


def test_empty_and_malformed_bearer_are_rejected(client):
    for header in (
        "Bearer",
        "Bearer ",
        "Bearer   ",
        "",
        "Not a scheme",
        "Bearer\t",
    ):
        response = client.get(
            "/api/analysis/temporal",
            params=PERIOD,
            headers={"Authorization": header},
        )
        _assert_unauthorized(response)


def test_query_parameter_is_not_accepted(client):
    for path in ANALYSIS_PATHS:
        response = client.get(
            path,
            params={
                **PERIOD,
                "token": TEST_ANALYSIS_API_TOKEN,
                "access_token": TEST_ANALYSIS_API_TOKEN,
            },
        )
        _assert_unauthorized(response)


def test_html_home_is_accessible_without_bearer(client):
    response = client.get("/")
    assert response.status_code == 200
    assert TEST_ANALYSIS_API_TOKEN not in response.text


def test_today_page_is_unaffected(client):
    response = client.get("/today/page")
    assert response.status_code == 200
    assert TEST_ANALYSIS_API_TOKEN not in response.text


def test_review_and_timer_remain_unauthenticated(client):
    review = client.get("/review/")
    timer = client.get("/timer/")
    assert review.status_code == 200
    assert timer.status_code == 200
    assert TEST_ANALYSIS_API_TOKEN not in review.text
    assert TEST_ANALYSIS_API_TOKEN not in timer.text


def test_docs_remain_accessible_without_bearer(client):
    docs = client.get("/docs")
    openapi = client.get("/openapi.json")
    assert docs.status_code == 200
    assert openapi.status_code == 200


def test_analysis_router_uses_shared_auth_dependency():
    from app.routes.analysis import router

    assert router.dependencies
    assert any(
        getattr(dep, "dependency", None)
        is require_analysis_api_token
        for dep in router.dependencies
    )


def test_new_analysis_routes_inherit_router_auth():
    source = ANALYSIS_ROUTE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    router_has_auth = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(
            node.func, "attr", None
        )
        if name != "APIRouter":
            continue
        for keyword in node.keywords:
            if keyword.arg != "dependencies":
                continue
            dumped = ast.dump(keyword.value)
            assert "require_analysis_api_token" in dumped
            router_has_auth = True
    assert router_has_auth

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("read_"):
            continue
        dumped = ast.dump(node.args)
        assert "require_analysis_api_token" not in dumped


def test_auth_module_does_not_import_analytics():
    for name in _imported_modules(AUTH_MODULE):
        assert name != "app.analytics"
        assert not name.startswith("app.analytics.")


def test_auth_module_does_not_import_evaluation():
    forbidden = (
        "evaluation",
        "openai",
        "app.analysis",
    )
    for name in _imported_modules(AUTH_MODULE):
        assert not any(
            name == prefix
            or name.startswith(prefix + ".")
            for prefix in forbidden
        ), name


def test_analysis_package_unaware_of_auth():
    forbidden = (
        "fastapi",
        "fastapi.security",
        "app.api.auth",
        "app.api",
        "app.routes",
    )
    for path in ANALYSIS_DIR.glob("*.py"):
        for name in _imported_modules(path):
            assert not any(
                name == prefix
                or name.startswith(prefix + ".")
                for prefix in forbidden
            ), (path, name)
        text = path.read_text(encoding="utf-8")
        assert "require_analysis_api_token" not in text
        assert "HTTPBearer" not in text
        assert "Authorization" not in text


def test_token_comes_from_configuration_not_source():
    auth_source = AUTH_MODULE.read_text(encoding="utf-8")
    config_source = (
        REPO_ROOT / "app" / "config.py"
    ).read_text(encoding="utf-8")
    assert "get_settings" in auth_source
    assert "compare_digest" in auth_source
    assert "provided == expected" not in auth_source
    assert TEST_ANALYSIS_API_TOKEN not in auth_source
    assert WRONG_TOKEN not in auth_source
    assert ANALYSIS_API_TOKEN_ENV in config_source
    assert "AKRASIA_ANALYSIS_API_TOKEN" in config_source


def test_missing_server_token_fails_closed(
    client,
    monkeypatch,
):
    monkeypatch.delenv(
        ANALYSIS_API_TOKEN_ENV,
        raising=False,
    )
    for path in ANALYSIS_PATHS:
        response = client.get(
            path,
            params=PERIOD,
            headers=AUTH_HEADERS,
        )
        _assert_unauthorized(response)


def test_blank_server_token_fails_closed(
    client,
    monkeypatch,
):
    monkeypatch.setenv(ANALYSIS_API_TOKEN_ENV, "  ")
    for path in ANALYSIS_PATHS:
        response = client.get(
            path,
            params=PERIOD,
            headers=AUTH_HEADERS,
        )
        _assert_unauthorized(response)


def test_auth_failure_does_not_mutate_database(
    client,
    db,
    make_task,
    make_daily_task,
):
    _seed_contract_data(db, make_task, make_daily_task)
    before = _snapshot(db)
    for path in ANALYSIS_PATHS:
        response = client.get(path, params=PERIOD)
        _assert_unauthorized(response)
        wrong = client.get(
            path,
            params=PERIOD,
            headers=_bearer(WRONG_TOKEN),
        )
        _assert_unauthorized(wrong)
    db.expire_all()
    assert _snapshot(db) == before
    assert db.query(Task).count() == len(before[0])
    assert db.query(DailyTask).count() == len(before[1])
    assert db.query(WorkSession).count() == len(
        before[2]
    )


def test_authenticated_get_does_not_mutate(
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


def test_analysis_api_stays_get_only(client):
    spec = client.get("/openapi.json").json()
    for path in ANALYSIS_PATHS:
        assert list(spec["paths"][path]) == ["get"]
        posted = client.post(
            path,
            params=PERIOD,
            headers=AUTH_HEADERS,
        )
        assert posted.status_code == 405


def test_openapi_declares_bearer_on_analysis_routes(client):
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    bearer_names = [
        name
        for name, scheme in schemes.items()
        if scheme.get("type") == "http"
        and scheme.get("scheme", "").lower() == "bearer"
    ]
    assert "AnalysisApiBearer" in bearer_names
    assert bearer_names
    dumped = json.dumps(spec)
    assert TEST_ANALYSIS_API_TOKEN not in dumped
    assert WRONG_TOKEN not in dumped
    assert "replace-with-a-long-random-secret" not in dumped

    for name in bearer_names:
        scheme = schemes[name]
        assert "example" not in scheme
        description = scheme.get("description") or ""
        assert TEST_ANALYSIS_API_TOKEN not in description

    for path in ANALYSIS_PATHS:
        security = spec["paths"][path]["get"].get(
            "security"
        )
        assert security, path
        used = {key for item in security for key in item}
        assert any(name in used for name in bearer_names)


def test_openapi_does_not_require_bearer_on_html(client):
    spec = client.get("/openapi.json").json()
    for path in HTML_PATHS:
        if path not in spec["paths"]:
            continue
        for operation in spec["paths"][path].values():
            if not isinstance(operation, dict):
                continue
            used = {
                key
                for item in (operation.get("security") or [])
                for key in item
            }
            assert "AnalysisApiBearer" not in used


def test_tokens_absent_from_captured_auth_logs(
    client,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("app").disabled = False

    client.get(
        "/api/analysis/temporal",
        params=PERIOD,
        headers=_bearer(WRONG_TOKEN),
    )
    client.get(
        "/api/analysis/temporal",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    client.get(
        "/api/analysis/temporal",
        params=PERIOD,
    )

    text = caplog.text
    assert TEST_ANALYSIS_API_TOKEN not in text
    assert WRONG_TOKEN not in text


def test_env_example_uses_placeholders_only():
    text = (
        REPO_ROOT / ".env.example"
    ).read_text(encoding="utf-8")
    assert (
        "AKRASIA_ANALYSIS_API_TOKEN="
        "replace-with-a-long-random-secret"
    ) in text
    assert "sk-proj-" not in text
    assert TEST_ANALYSIS_API_TOKEN not in text
    assert "EVAL_AGENT_API_KEY=replace-with-your-eval-agent-api-key" in text
