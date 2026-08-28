"""Health endpoint for container and reverse-proxy checks."""

from app.config import get_settings
from tests.conftest import TEST_OPERATOR_PASSWORD
from tests.test_analysis_api import PERIOD, TEST_ANALYSIS_API_TOKEN


HTML_APP_PATHS = (
    "/",
    "/today/page",
    "/timer/",
    "/review/",
)


def test_health_is_ok_without_auth(anonymous_client):
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_exposes_no_sensitive_config(
    anonymous_client,
    monkeypatch,
):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://alice:s3cretpass@db.example:5432/appdb",
    )
    monkeypatch.setenv(
        "AKRASIA_ANALYSIS_API_TOKEN",
        TEST_ANALYSIS_API_TOKEN,
    )
    settings = get_settings()
    response = anonymous_client.get("/health")
    body = response.text
    assert response.json() == {"status": "ok"}
    assert "s3cretpass" not in body
    assert "alice" not in body
    assert "DATABASE_URL" not in body
    assert "postgresql+psycopg://" not in body
    assert TEST_ANALYSIS_API_TOKEN not in body
    assert TEST_OPERATOR_PASSWORD not in body
    if settings.password_hash:
        assert settings.password_hash not in body
    if settings.database_url:
        assert settings.database_url not in body


def test_health_does_not_set_session_cookie(anonymous_client):
    response = anonymous_client.get("/health")
    assert "akrasia_session" not in response.headers.get(
        "set-cookie",
        "",
    ).lower()


def test_html_app_routes_still_require_browser_auth(
    anonymous_client,
):
    for path in HTML_APP_PATHS:
        response = anonymous_client.get(
            path,
            follow_redirects=False,
        )
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login")


def test_login_and_static_remain_public(anonymous_client):
    login = anonymous_client.get("/login")
    static = anonymous_client.get("/static/styles.css")
    assert login.status_code == 200
    assert static.status_code == 200


def test_analysis_api_still_requires_bearer(anonymous_client):
    response = anonymous_client.get(
        "/api/analysis/temporal",
        params=PERIOD,
    )
    assert response.status_code == 401
