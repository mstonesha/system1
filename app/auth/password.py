"""Argon2id hashing for the single-operator password.

The password itself is never stored. Self-hosters put only the
hash in ``AKRASIA_PASSWORD_HASH``.
"""

from __future__ import annotations

import getpass
import logging
import sys

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.config import get_settings


logger = logging.getLogger("app.auth")

# Library defaults. Tests may hash with cheaper parameters;
# do not lower production cost here.
_hasher = PasswordHasher()


def hash_operator_password(password: str) -> str:
    return _hasher.hash(password)


def verify_operator_password(
    password: str,
    password_hash: str | None = None,
) -> bool:
    expected = password_hash
    if expected is None:
        expected = get_settings().password_hash

    if expected is None:
        logger.warning(
            "Operator password hash is not configured"
        )
        return False

    if not password:
        return False

    try:
        matched = _hasher.verify(expected, password)
    except (
        VerifyMismatchError,
        InvalidHashError,
        VerificationError,
    ):
        return False

    if matched and _hasher.check_needs_rehash(expected):
        logger.warning(
            "Operator password hash can be regenerated "
            "with current Argon2id parameters"
        )

    return matched


def main(argv: list[str] | None = None) -> int:
    del argv
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print(
            "Passwords do not match.",
            file=sys.stderr,
        )
        return 1

    if not password:
        print(
            "Password cannot be empty.",
            file=sys.stderr,
        )
        return 1

    print(hash_operator_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
