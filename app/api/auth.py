"""Bearer-token authentication for machine API clients.

This is transport-layer authentication for trusted callers of
``/api/analysis/*``. It is not human/browser login.
"""

from typing import Annotated

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.config import get_settings


AUTH_DETAIL = (
    "Invalid or missing authentication credentials"
)

analysis_api_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AnalysisApiBearer",
)


def _unauthorized() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=AUTH_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_analysis_api_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(analysis_api_bearer),
    ],
) -> None:
    expected = get_settings().analysis_api_token
    provided = (
        credentials.credentials
        if credentials is not None
        else ""
    )
    scheme = (
        credentials.scheme
        if credentials is not None
        else ""
    )

    if (
        expected is None
        or credentials is None
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(
            provided,
            expected,
        )
    ):
        _unauthorized()
