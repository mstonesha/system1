import ast
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.auth.http import (
    CSRF_COOKIE_NAME,
    GENERIC_CSRF_DETAIL,
    GENERIC_LOGIN_ERROR,
    SESSION_COOKIE_NAME,
    safe_next_path,
)
from app.auth.password import (
    hash_operator_password,
    verify_operator_password,
)
from app.auth.sessions import (
    SESSION_ABSOLUTE_LIFETIME,
    SESSION_IDLE_TIMEOUT,
    SESSION_REFRESH_INTERVAL,
    hash_token,
    is_session_valid,
    issue_session,
    lookup_session,
    refresh_session,
    revoke_all_sessions,
    revoke_session,
)
from app.config import PASSWORD_HASH_ENV, get_settings
from app.models import BrowserSession, Task
from app.time import utc_now
from tests.conftest import TEST_OPERATOR_PASSWORD
from tests.test_analysis_api import (
    ANALYSIS_PATHS,
    AUTH_HEADERS,
    PERIOD,
    TEST_ANALYSIS_API_TOKEN,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = REPO_ROOT / "app" / "auth"
WRONG_PASSWORD = "wrong-operator-password-not-a-secret"


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


def _set_cookie_headers(response):
    if hasattr(response.headers, "get_list"):
        return response.headers.get_list("set-cookie")
    value = response.headers.get("set-cookie")
    return [value] if value else []


def _cookie_header(response, name):
    prefix = name + "="
    for header in _set_cookie_headers(response):
        if header.lower().startswith(prefix.lower()):
            return header
    return None


def test_password_hash_verification_succeeds():
    password_hash = hash_operator_password(
        TEST_OPERATOR_PASSWORD
    )
    assert password_hash.startswith("$argon2id$")
    assert verify_operator_password(
        TEST_OPERATOR_PASSWORD,
        password_hash,
    )


def test_wrong_password_fails_verification():
    password_hash = hash_operator_password(
        TEST_OPERATOR_PASSWORD
    )
    assert not verify_operator_password(
        WRONG_PASSWORD,
        password_hash,
    )


def test_password_cli_prints_only_the_hash(monkeypatch, capsys):
    monkeypatch.setattr(
        "app.auth.password.getpass.getpass",
        lambda prompt="": TEST_OPERATOR_PASSWORD,
    )
    from app.auth.password import main

    assert main() == 0
    output = capsys.readouterr()
    assert output.out.strip().startswith("$argon2id$")
    assert TEST_OPERATOR_PASSWORD not in output.out
    assert TEST_OPERATOR_PASSWORD not in output.err


def test_successful_login_creates_hashed_session(
    anonymous_client,
    db,
):
    before = db.query(BrowserSession).count()
    response = anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    db.expire_all()
    rows = db.query(BrowserSession).all()
    assert len(rows) == before + 1
    raw = anonymous_client.cookies.get(SESSION_COOKIE_NAME)
    assert raw
    row = rows[-1]
    assert raw not in row.token_hash
    assert hash_token(raw) == row.token_hash
    assert row.revoked_at is None


def test_session_cookie_flags_on_login(anonymous_client):
    response = anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert header is not None
    lowered = header.lower()
    parts = [part.strip() for part in lowered.split(";")]
    assert "httponly" in parts
    assert "samesite=lax" in parts
    assert "secure" not in parts
    csrf_header = _cookie_header(response, CSRF_COOKIE_NAME)
    assert csrf_header is not None
    csrf_parts = [
        part.strip() for part in csrf_header.lower().split(";")
    ]
    assert "httponly" in csrf_parts
    assert "samesite=lax" in csrf_parts
    assert "secure" not in csrf_parts


def test_secure_cookie_follows_config(
    anonymous_client,
    monkeypatch,
):
    monkeypatch.setenv("AKRASIA_COOKIE_SECURE", "true")
    response = anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert header is not None
    assert "secure" in header.lower()


def test_login_next_local_path_redirects(
    anonymous_client,
):
    response = anonymous_client.post(
        "/login",
        data={
            "password": TEST_OPERATOR_PASSWORD,
            "next": "/today/page",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/today/page"


def test_login_rejects_open_redirect(anonymous_client):
    response = anonymous_client.post(
        "/login",
        data={
            "password": TEST_OPERATOR_PASSWORD,
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_safe_next_path_rejects_external_targets():
    assert safe_next_path("//evil.example") == "/"
    assert safe_next_path("https://evil.example") == "/"
    assert safe_next_path("/\\evil") == "/"
    assert safe_next_path("/login") == "/"
    assert safe_next_path("/today/page") == "/today/page"


def test_wrong_password_does_not_create_session(
    anonymous_client,
    db,
):
    before = db.query(BrowserSession).count()
    response = anonymous_client.post(
        "/login",
        data={"password": WRONG_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert GENERIC_LOGIN_ERROR in response.text
    assert "username" not in response.text.lower()
    assert WRONG_PASSWORD not in response.text
    assert TEST_OPERATOR_PASSWORD not in response.text
    db.expire_all()
    assert db.query(BrowserSession).count() == before
    assert not anonymous_client.cookies.get(SESSION_COOKIE_NAME)


def test_missing_password_config_fails_closed(
    anonymous_client,
    db,
    monkeypatch,
    caplog,
):
    monkeypatch.delenv(PASSWORD_HASH_ENV, raising=False)
    caplog.set_level(logging.WARNING, logger="app.auth")
    logging.getLogger("app.auth").disabled = False
    response = anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert GENERIC_LOGIN_ERROR in response.text
    assert get_settings().password_hash is None
    assert db.query(BrowserSession).count() == 0
    assert TEST_OPERATOR_PASSWORD not in caplog.text
    assert "not configured" in caplog.text.lower()


def test_unauthenticated_html_get_redirects_to_login(
    anonymous_client,
):
    for path in ("/", "/today/page", "/timer/", "/review/"):
        response = anonymous_client.get(
            path,
            follow_redirects=False,
        )
        assert response.status_code == 303, path
        assert response.headers["location"].startswith(
            "/login"
        )


def test_unauthenticated_post_does_not_mutate(
    anonymous_client,
    db,
):
    before = db.query(Task).count()
    response = anonymous_client.post(
        "/tasks/create",
        data={"title": "Should not persist"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    db.expire_all()
    assert db.query(Task).count() == before


def test_authenticated_html_get_succeeds(client):
    for path in ("/", "/today/page", "/timer/", "/review/"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_static_assets_available_without_login(
    anonymous_client,
):
    response = anonymous_client.get("/static/styles.css")
    assert response.status_code == 200
    assert "login-form" in response.text or "body" in response.text


def test_login_page_has_no_username_or_register(
    anonymous_client,
):
    response = anonymous_client.get("/login")
    assert response.status_code == 200
    text = response.text.lower()
    assert 'name="password"' in response.text
    assert "username" not in text
    assert "register" not in text
    assert "sign up" not in text
    assert "forgot" not in text


def test_login_issues_fresh_token_not_fixation(
    anonymous_client,
    db,
):
    planted = issue_session(db, user_agent="attacker")
    anonymous_client.cookies.set(
        SESSION_COOKIE_NAME,
        planted.raw_token,
    )
    response = anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert header is not None
    new_raw = header.split(";", 1)[0].split("=", 1)[1]
    assert new_raw
    assert new_raw != planted.raw_token
    db.expire_all()
    old = (
        db.query(BrowserSession)
        .filter_by(id=planted.session.id)
        .one()
    )
    assert old.revoked_at is not None
    assert lookup_session(db, planted.raw_token) is None
    assert lookup_session(db, new_raw) is not None


def test_logout_revokes_current_session_only(
    anonymous_client,
    db,
):
    first = issue_session(db, user_agent="one")
    second = issue_session(db, user_agent="two")
    anonymous_client.cookies.set(
        SESSION_COOKIE_NAME,
        first.raw_token,
    )
    anonymous_client.cookies.set(
        CSRF_COOKIE_NAME,
        first.raw_csrf,
    )
    response = anonymous_client.post(
        "/logout",
        data={"csrf_token": first.raw_csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    db.expire_all()
    assert lookup_session(db, first.raw_token) is None
    assert lookup_session(db, second.raw_token) is not None
    home = anonymous_client.get("/", follow_redirects=False)
    assert home.status_code == 303


def test_logout_clears_cookie(anonymous_client, db):
    issued = issue_session(db)
    anonymous_client.cookies.set(
        SESSION_COOKIE_NAME,
        issued.raw_token,
    )
    anonymous_client.cookies.set(
        CSRF_COOKIE_NAME,
        issued.raw_csrf,
    )
    response = anonymous_client.post(
        "/logout",
        data={"csrf_token": issued.raw_csrf},
        follow_redirects=False,
    )
    header = _cookie_header(response, SESSION_COOKIE_NAME)
    assert header is not None
    assert "max-age=0" in header.lower() or "max-age=0" in header.lower()


def test_revoke_all_invalidates_multiple_sessions(db):
    one = issue_session(db, user_agent="one")
    two = issue_session(db, user_agent="two")
    count = revoke_all_sessions(db)
    assert count == 2
    assert lookup_session(db, one.raw_token) is None
    assert lookup_session(db, two.raw_token) is None


def test_idle_and_absolute_expiry_enforced(db):
    now = utc_now()
    issued = issue_session(db, now=now)
    issued.session.idle_expires_at = now - timedelta(seconds=1)
    db.commit()
    assert lookup_session(db, issued.raw_token, now=now) is None

    issued = issue_session(db, now=now)
    issued.session.idle_expires_at = (
        now + timedelta(days=10)
    )
    issued.session.absolute_expires_at = (
        now - timedelta(seconds=1)
    )
    db.commit()
    assert lookup_session(db, issued.raw_token, now=now) is None


def test_refresh_extends_idle_not_absolute(db):
    now = utc_now()
    issued = issue_session(db, now=now)
    original_absolute = issued.session.absolute_expires_at
    issued.session.last_seen_at = (
        now - SESSION_REFRESH_INTERVAL - timedelta(minutes=1)
    )
    db.commit()
    later = now + timedelta(hours=2)
    wrote = refresh_session(db, issued.session, now=later)
    assert wrote is True
    db.refresh(issued.session)
    assert issued.session.absolute_expires_at == original_absolute
    assert issued.session.idle_expires_at == (
        later + SESSION_IDLE_TIMEOUT
    )
    assert original_absolute == (
        now + SESSION_ABSOLUTE_LIFETIME
    )


def test_refresh_skips_recent_activity(db):
    now = utc_now()
    issued = issue_session(db, now=now)
    idle_before = issued.session.idle_expires_at
    wrote = refresh_session(
        db,
        issued.session,
        now=now + timedelta(minutes=5),
    )
    assert wrote is False
    db.refresh(issued.session)
    assert issued.session.idle_expires_at == idle_before


def test_revoked_session_fails_lookup(db):
    issued = issue_session(db)
    revoke_session(db, issued.session)
    assert lookup_session(db, issued.raw_token) is None
    assert not is_session_valid(issued.session)


def test_csrf_required_on_state_changing_form(
    client,
    db,
):
    # Session and CSRF cookies are present; the cookie is not
    # accepted as the submitted CSRF value.
    client.inject_csrf = False
    before = db.query(Task).count()
    response = client.post(
        "/tasks/create",
        data={"title": "No CSRF"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == GENERIC_CSRF_DETAIL
    db.expire_all()
    assert db.query(Task).count() == before


def test_cancel_stale_requires_csrf(client, db, make_task):
    client.inject_csrf = False
    task = make_task(
        "Old untouched",
        created_at=utc_now() - timedelta(days=40),
    )

    response = client.post("/tasks/cancel-stale")

    assert response.status_code == 403
    assert response.json()["detail"] == GENERIC_CSRF_DETAIL
    db.expire_all()
    assert db.get(Task, task.id).status == "active"


def test_wrong_csrf_is_forbidden(client, db):
    client.inject_csrf = False
    before = db.query(Task).count()
    response = client.post(
        "/tasks/create",
        data={
            "title": "Bad CSRF",
            "csrf_token": "not-the-session-csrf-token",
        },
    )
    assert response.status_code == 403
    db.expire_all()
    assert db.query(Task).count() == before


def test_correct_csrf_allows_task_create(client, db):
    response = client.post(
        "/tasks/create",
        data={"title": "Protected create"},
    )
    assert response.status_code == 200
    assert db.query(Task).filter_by(
        title="Protected create"
    ).count() == 1


def test_htmx_post_accepts_csrf_header(client, db):
    response = client.post(
        "/tasks/create",
        data={"title": "HTMX create"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert db.query(Task).filter_by(
        title="HTMX create"
    ).count() == 1


def test_csrf_does_not_apply_to_analysis_gets(
    client,
):
    for path in ANALYSIS_PATHS:
        response = client.get(path, params=PERIOD)
        assert response.status_code == 401


def test_browser_session_does_not_authenticate_api(
    client,
):
    response = client.get(
        "/api/analysis/temporal",
        params=PERIOD,
    )
    assert response.status_code == 401


def test_bearer_token_works_without_browser_login(
    anonymous_client,
    db,
):
    response = anonymous_client.get(
        "/api/analysis/temporal",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert TEST_ANALYSIS_API_TOKEN not in response.text


def test_login_does_not_change_bearer_api(
    anonymous_client,
):
    anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    response = anonymous_client.get(
        "/api/analysis/temporal",
        params=PERIOD,
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    still_401 = anonymous_client.get(
        "/api/analysis/temporal",
        params=PERIOD,
    )
    assert still_401.status_code == 401


def test_tokens_absent_from_login_logs(
    anonymous_client,
    db,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("app").disabled = False
    anonymous_client.post(
        "/login",
        data={"password": WRONG_PASSWORD},
        follow_redirects=False,
    )
    response = anonymous_client.post(
        "/login",
        data={"password": TEST_OPERATOR_PASSWORD},
        follow_redirects=False,
    )
    raw = anonymous_client.cookies.get(SESSION_COOKIE_NAME)
    text = caplog.text
    assert TEST_OPERATOR_PASSWORD not in text
    assert WRONG_PASSWORD not in text
    if raw:
        assert raw not in text
    settings = get_settings()
    if settings.password_hash:
        assert settings.password_hash not in text
    db.expire_all()
    row = db.query(BrowserSession).one()
    assert row.token_hash not in text
    assert row.csrf_token_hash not in text
    csrf = None
    csrf_header = _cookie_header(response, CSRF_COOKIE_NAME)
    if csrf_header:
        csrf = csrf_header.split(";", 1)[0].split("=", 1)[1]
    if csrf:
        assert csrf not in text


def test_auth_package_does_not_import_evaluation():
    forbidden = (
        "evaluation",
        "openai",
        "app.analytics",
        "app.analysis",
    )
    for path in AUTH_DIR.glob("*.py"):
        for name in _imported_modules(path):
            assert not any(
                name == prefix
                or name.startswith(prefix + ".")
                for prefix in forbidden
            ), (path, name)


def test_no_users_model_or_registration_route():
    models = (
        REPO_ROOT / "app" / "models.py"
    ).read_text(encoding="utf-8")
    assert "class User" not in models
    auth_routes = (
        REPO_ROOT / "app" / "routes" / "auth.py"
    ).read_text(encoding="utf-8")
    assert "/register" not in auth_routes
    from app.routes.auth import router as auth_router

    paths = [route.path for route in auth_router.routes]
    assert "/login" in paths
    assert "/logout" in paths
    assert "/register" not in paths


def test_production_password_hasher_uses_library_defaults():
    from argon2 import PasswordHasher
    from app.auth import password as password_mod

    library = PasswordHasher()
    assert password_mod._hasher.time_cost == library.time_cost
    assert password_mod._hasher.memory_cost == library.memory_cost
    assert password_mod._hasher.parallelism == library.parallelism


def test_low_cost_test_hashes_would_need_production_rehash():
    from argon2 import PasswordHasher

    production = PasswordHasher()
    low = PasswordHasher(
        time_cost=1,
        memory_cost=8,
        parallelism=1,
    )
    encoded = low.hash("test-only")
    assert production.check_needs_rehash(encoded)


def test_authenticated_html_contains_csrf_not_session_secret(
    client,
):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert client.csrf_token in html
    assert 'name="csrf_token"' in html
    assert "X-CSRF-Token" in html
    assert client.raw_session_token not in html
    assert TEST_OPERATOR_PASSWORD not in html


def test_raw_csrf_is_not_stored_in_postgresql(client, db):
    row = db.query(BrowserSession).one()
    assert row.csrf_token_hash == hash_token(client.csrf_token)
    assert row.token_hash == hash_token(client.raw_session_token)
    assert client.csrf_token not in row.csrf_token_hash
    assert client.csrf_token not in row.token_hash
    assert client.raw_session_token not in row.token_hash
    assert client.raw_session_token not in row.csrf_token_hash


def test_authenticated_html_does_not_log_secrets(
    client,
    caplog,
):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("app").disabled = False
    response = client.get("/")
    assert response.status_code == 200
    assert client.csrf_token in response.text
    text = caplog.text
    assert client.csrf_token not in text
    assert client.raw_session_token not in text
    assert TEST_OPERATOR_PASSWORD not in text


def test_correct_hidden_form_csrf_succeeds_without_header(
    client,
    db,
):
    client.inject_csrf = False
    response = client.post(
        "/tasks/create",
        data={
            "title": "Form field CSRF",
            "csrf_token": client.csrf_token,
        },
    )
    assert response.status_code == 200
    assert db.query(Task).filter_by(
        title="Form field CSRF"
    ).count() == 1


def test_correct_htmx_header_csrf_succeeds_without_form_field(
    client,
    db,
):
    client.inject_csrf = False
    response = client.post(
        "/tasks/create",
        data={"title": "Header CSRF"},
        headers={
            "HX-Request": "true",
            "X-CSRF-Token": client.csrf_token,
        },
    )
    assert response.status_code == 200
    assert db.query(Task).filter_by(
        title="Header CSRF"
    ).count() == 1


def test_refresh_persists_without_domain_mutation(db):
    now = utc_now()
    issued = issue_session(db, now=now)
    original_absolute = issued.session.absolute_expires_at
    issued.session.last_seen_at = (
        now - SESSION_REFRESH_INTERVAL - timedelta(minutes=1)
    )
    db.commit()
    later = now + timedelta(hours=2)
    assert refresh_session(db, issued.session, now=later) is True

    probe = Session(bind=db.get_bind())
    try:
        row = probe.get(BrowserSession, issued.session.id)
        assert row.last_seen_at == later
        assert row.idle_expires_at == later + SESSION_IDLE_TIMEOUT
        assert row.absolute_expires_at == original_absolute
        assert probe.query(Task).count() == 0
    finally:
        probe.close()


def test_refresh_does_not_commit_unrelated_pending_task(db):
    now = utc_now()
    issued = issue_session(db, now=now)
    original_absolute = issued.session.absolute_expires_at
    issued.session.last_seen_at = (
        now - SESSION_REFRESH_INTERVAL - timedelta(minutes=1)
    )
    db.commit()
    db.add(Task(title="Must not persist from refresh"))
    later = now + timedelta(hours=2)
    assert refresh_session(db, issued.session, now=later) is True

    probe = Session(bind=db.get_bind())
    try:
        assert probe.query(Task).filter_by(
            title="Must not persist from refresh"
        ).count() == 0
        row = probe.get(BrowserSession, issued.session.id)
        assert row.last_seen_at == later
        assert row.absolute_expires_at == original_absolute
    finally:
        probe.close()

    db.rollback()


def test_http_get_within_refresh_interval_does_not_write(
    anonymous_client,
    db,
):
    issued = issue_session(db)
    last_seen = issued.session.last_seen_at
    idle = issued.session.idle_expires_at
    absolute = issued.session.absolute_expires_at
    anonymous_client.cookies.set(
        SESSION_COOKIE_NAME,
        issued.raw_token,
    )
    anonymous_client.cookies.set(
        CSRF_COOKIE_NAME,
        issued.raw_csrf,
    )
    response = anonymous_client.get("/")
    assert response.status_code == 200
    db.expire_all()
    row = db.get(BrowserSession, issued.session.id)
    assert row.last_seen_at == last_seen
    assert row.idle_expires_at == idle
    assert row.absolute_expires_at == absolute


def test_http_get_outside_interval_refreshes_idle_not_absolute(
    anonymous_client,
    db,
):
    now = utc_now()
    issued = issue_session(db, now=now)
    stale = now - SESSION_REFRESH_INTERVAL - timedelta(minutes=5)
    issued.session.last_seen_at = stale
    original_absolute = issued.session.absolute_expires_at
    db.commit()
    anonymous_client.cookies.set(
        SESSION_COOKIE_NAME,
        issued.raw_token,
    )
    anonymous_client.cookies.set(
        CSRF_COOKIE_NAME,
        issued.raw_csrf,
    )
    response = anonymous_client.get("/")
    assert response.status_code == 200
    db.expire_all()
    row = db.get(BrowserSession, issued.session.id)
    assert row.last_seen_at > stale
    assert row.idle_expires_at > now
    assert row.absolute_expires_at == original_absolute
