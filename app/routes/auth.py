"""Password-only login and logout for the single operator."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.http import (
    DEFAULT_NEXT_PATH,
    GENERIC_CSRF_DETAIL,
    GENERIC_LOGIN_ERROR,
    LOGIN_PATH,
    SESSION_COOKIE_NAME,
    clear_session_cookies,
    replace_browser_session,
    safe_next_path,
    set_session_cookies,
)
from app.auth.password import verify_operator_password
from app.auth.sessions import (
    csrf_token_matches,
    lookup_session,
    revoke_session,
)
from app.database import get_db
from app.templating import templates


router = APIRouter(tags=["auth"])


def _login_page(
    request: Request,
    *,
    next_path: str = DEFAULT_NEXT_PATH,
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next_path": safe_next_path(next_path),
            "error": error,
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@router.get(LOGIN_PATH)
def login_page(
    request: Request,
    next: str | None = None,
    db: Session = Depends(get_db),
):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if lookup_session(db, raw_token) is not None:
        return RedirectResponse(
            url=safe_next_path(next),
            status_code=303,
        )

    return _login_page(request, next_path=next)


@router.post(LOGIN_PATH)
def login(
    request: Request,
    password: str = Form(""),
    next: str = Form(DEFAULT_NEXT_PATH),
    db: Session = Depends(get_db),
):
    """Password-only login.

    There is no anonymous pre-login session, so this POST has
    no synchronizer CSRF token. That is a bounded decision for
    the current single-operator app, not an accidental omission.
    Authenticated mutations remain CSRF-protected. A successful
    login always issues a fresh session; a preexisting cookie
    is never promoted.
    """
    destination = safe_next_path(next)

    if not verify_operator_password(password):
        return _login_page(
            request,
            next_path=destination,
            error=GENERIC_LOGIN_ERROR,
            status_code=401,
        )

    issued = replace_browser_session(
        db,
        request,
        existing_raw_token=request.cookies.get(
            SESSION_COOKIE_NAME
        ),
    )
    response = RedirectResponse(
        url=destination,
        status_code=303,
    )
    set_session_cookies(
        response,
        raw_token=issued.raw_token,
        raw_csrf=issued.raw_csrf,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    session = lookup_session(db, raw_token)

    if session is not None:
        form = await request.form()
        submitted = request.headers.get("X-CSRF-Token")
        if not submitted:
            value = form.get("csrf_token")
            submitted = (
                value if isinstance(value, str) else None
            )
        if not csrf_token_matches(session, submitted):
            raise HTTPException(
                status_code=403,
                detail=GENERIC_CSRF_DETAIL,
            )
        revoke_session(db, session)

    response = RedirectResponse(
        url=LOGIN_PATH,
        status_code=303,
    )
    clear_session_cookies(response)
    response.headers["Cache-Control"] = "no-store"
    return response
