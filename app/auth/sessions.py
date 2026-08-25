"""Server-side browser sessions.

The browser holds a high-entropy opaque token. PostgreSQL stores
only SHA-256(token). Idle timeout is 30 days and is refreshed on
a bounded interval. Absolute lifetime is 90 days and never
extends.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import BrowserSession
from app.time import utc_now


SESSION_IDLE_TIMEOUT = timedelta(days=30)
SESSION_ABSOLUTE_LIFETIME = timedelta(days=90)
SESSION_REFRESH_INTERVAL = timedelta(hours=1)
USER_AGENT_MAX_LENGTH = 512
TOKEN_BYTES = 32


@dataclass(frozen=True)
class IssuedSession:
    raw_token: str
    raw_csrf: str
    session: BrowserSession


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()


def bound_user_agent(user_agent: str | None) -> str | None:
    if user_agent is None:
        return None

    stripped = user_agent.strip()
    if not stripped:
        return None

    return stripped[:USER_AGENT_MAX_LENGTH]


def is_session_valid(
    session: BrowserSession,
    *,
    now: datetime | None = None,
) -> bool:
    current = now or utc_now()

    if session.revoked_at is not None:
        return False

    if current >= session.idle_expires_at:
        return False

    if current >= session.absolute_expires_at:
        return False

    return True


def issue_session(
    db: Session,
    *,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    current = now or utc_now()
    raw_token = generate_token()
    raw_csrf = generate_token()

    session = BrowserSession(
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        created_at=current,
        last_seen_at=current,
        idle_expires_at=current + SESSION_IDLE_TIMEOUT,
        absolute_expires_at=(
            current + SESSION_ABSOLUTE_LIFETIME
        ),
        user_agent=bound_user_agent(user_agent),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    return IssuedSession(
        raw_token=raw_token,
        raw_csrf=raw_csrf,
        session=session,
    )


def lookup_session(
    db: Session,
    raw_token: str | None,
    *,
    now: datetime | None = None,
) -> BrowserSession | None:
    if not raw_token:
        return None

    token_hash = hash_token(raw_token)
    session = (
        db.query(BrowserSession)
        .filter(BrowserSession.token_hash == token_hash)
        .one_or_none()
    )

    if session is None:
        return None

    if not is_session_valid(session, now=now):
        return None

    return session


def csrf_token_matches(
    session: BrowserSession,
    submitted: str | None,
) -> bool:
    if not submitted:
        return False

    return secrets.compare_digest(
        hash_token(submitted),
        session.csrf_token_hash,
    )


def refresh_session(
    db: Session,
    session: BrowserSession,
    *,
    now: datetime | None = None,
) -> bool:
    """Refresh idle expiry when last_seen_at is stale.

    Returns True when a write occurred. Never extends
    absolute_expires_at.

    The write uses a separate SQLAlchemy session so this
    commit cannot flush pending Task / DailyTask /
    WorkSession changes on ``db``.
    """
    current = now or utc_now()

    if current - session.last_seen_at < SESSION_REFRESH_INTERVAL:
        return False

    new_last_seen = current
    new_idle = current + SESSION_IDLE_TIMEOUT

    with Session(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
    ) as isolated:
        isolated.execute(
            update(BrowserSession)
            .where(BrowserSession.id == session.id)
            .values(
                last_seen_at=new_last_seen,
                idle_expires_at=new_idle,
            )
        )
        isolated.commit()

    db.expire(session)
    return True


def revoke_session(
    db: Session,
    session: BrowserSession,
    *,
    now: datetime | None = None,
) -> None:
    if session.revoked_at is not None:
        return

    session.revoked_at = now or utc_now()
    db.commit()


def revoke_all_sessions(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    current = now or utc_now()
    count = (
        db.query(BrowserSession)
        .filter(BrowserSession.revoked_at.is_(None))
        .update(
            {BrowserSession.revoked_at: current},
            synchronize_session=False,
        )
    )
    db.commit()
    return count


def delete_expired_sessions(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    current = now or utc_now()
    count = (
        db.query(BrowserSession)
        .filter(
            (BrowserSession.idle_expires_at <= current)
            | (BrowserSession.absolute_expires_at <= current)
            | BrowserSession.revoked_at.isnot(None)
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return count
