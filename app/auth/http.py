"""HTTP cookies, CSRF, and browser-session dependencies.

The ``akrasia_csrf`` cookie is not used to authenticate or to
validate mutations. It is the HttpOnly store of the raw CSRF
token so later HTML can re-render it. PostgreSQL keeps only
``csrf_token_hash``. Validation uses the form field or
``X-CSRF-Token`` header.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.sessions import (
    SESSION_ABSOLUTE_LIFETIME,
    IssuedSession,
    csrf_token_matches,
    issue_session,
    lookup_session,
    refresh_session,
    revoke_session,
)
from app.config import get_settings
from app.database import get_db
from app.models import BrowserSession


SESSION_COOKIE_NAME = "akrasia_session"
CSRF_COOKIE_NAME = "akrasia_csrf"
LOGIN_PATH = "/login"
DEFAULT_NEXT_PATH = "/"
GENERIC_LOGIN_ERROR = "Invalid password."
GENERIC_CSRF_DETAIL = "Invalid request."
STATE_CHANGING_METHODS = frozenset(
    {"POST", "PUT", "PATCH", "DELETE"}
)


class BrowserLoginRequired(Exception):
    def __init__(self, *, url: str, htmx: bool = False):
        self.url = url
        self.htmx = htmx


def cookie_max_age_seconds() -> int:
    return int(SESSION_ABSOLUTE_LIFETIME.total_seconds())


def _cookie_settings() -> dict:
    return {
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": get_settings().cookie_secure,
        "max_age": cookie_max_age_seconds(),
    }


def set_session_cookies(
    response,
    *,
    raw_token: str,
    raw_csrf: str,
) -> None:
    settings = _cookie_settings()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        **settings,
    )
    # Raw CSRF token for HTML re-render only. Not an
    # authentication credential and not accepted alone.
    response.set_cookie(
        CSRF_COOKIE_NAME,
        raw_csrf,
        **settings,
    )


def clear_session_cookies(response) -> None:
    secure = get_settings().cookie_secure
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def safe_next_path(value: str | None) -> str:
    if value is None:
        return DEFAULT_NEXT_PATH

    candidate = value.strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "://" in candidate
        or "\\" in candidate
        or "\n" in candidate
        or "\r" in candidate
        or "\0" in candidate
    ):
        return DEFAULT_NEXT_PATH

    path_only = candidate.split("?", 1)[0]
    if path_only == LOGIN_PATH or path_only.startswith(
        LOGIN_PATH + "/"
    ):
        return DEFAULT_NEXT_PATH

    return candidate


def login_redirect_url(next_path: str | None = None) -> str:
    destination = safe_next_path(next_path)
    if destination == DEFAULT_NEXT_PATH:
        return LOGIN_PATH
    return (
        LOGIN_PATH
        + "?"
        + urlencode({"next": destination})
    )


def requested_path(request: Request) -> str:
    path = request.url.path
    if request.url.query:
        return f"{path}?{request.url.query}"
    return path


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def raise_login_required(request: Request) -> None:
    if request.method in STATE_CHANGING_METHODS:
        raise BrowserLoginRequired(
            url=LOGIN_PATH,
            htmx=_is_htmx(request),
        )

    raise BrowserLoginRequired(
        url=login_redirect_url(requested_path(request)),
        htmx=_is_htmx(request),
    )


async def browser_login_required_handler(
    request: Request,
    exc: BrowserLoginRequired,
):
    if exc.htmx:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Authentication required"},
            headers={"HX-Redirect": LOGIN_PATH},
        )

    return RedirectResponse(
        url=exc.url,
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _submitted_csrf_token(
    request: Request,
) -> str | None:
    header = request.headers.get("X-CSRF-Token")
    if header:
        return header

    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or (
        "multipart/form-data" in content_type
    ):
        form = await request.form()
        value = form.get("csrf_token")
        if isinstance(value, str):
            return value

    return None


async def require_browser_session(
    request: Request,
    db: Session = Depends(get_db),
) -> BrowserSession:
    # Re-render store: not used as the submitted CSRF value.
    request.state.csrf_token = request.cookies.get(
        CSRF_COOKIE_NAME,
        "",
    )
    request.state.browser_session = None

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    session = lookup_session(db, raw_token)

    if session is None:
        raise_login_required(request)

    assert session is not None
    request.state.browser_session = session
    refresh_session(db, session)

    if request.method in STATE_CHANGING_METHODS:
        submitted = await _submitted_csrf_token(request)
        if not csrf_token_matches(session, submitted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=GENERIC_CSRF_DETAIL,
            )

    return session


def replace_browser_session(
    db: Session,
    request: Request,
    *,
    existing_raw_token: str | None,
) -> IssuedSession:
    existing = lookup_session(db, existing_raw_token)
    if existing is not None:
        revoke_session(db, existing)

    return issue_session(
        db,
        user_agent=request.headers.get("user-agent"),
    )
