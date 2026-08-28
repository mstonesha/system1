"""Isolated pg_dump / pg_restore round-trip on system1_test."""

import os
import shutil
import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import Task


RESTORE_DATABASE_NAME = "system1_restore_verify"


def _pg_env(url):
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    env.pop("PGDATABASE", None)
    return env


def _pg_args(url, *extra):
    args = [
        "--host",
        url.host,
        "--port",
        str(url.port or 5432),
        "--username",
        url.username,
    ]
    args.extend(extra)
    return args


def test_pg_dump_restore_roundtrip_on_isolated_database(
    db,
    make_task,
    tmp_path,
):
    if shutil.which("pg_dump") is None:
        pytest.fail(
            "pg_dump is required in the test image "
            "(INSTALL_DEV postgresql-client)"
        )
    if shutil.which("pg_restore") is None:
        pytest.fail("pg_restore is required in the test image")

    marker = "backup-restore-marker-task"
    make_task(marker)
    db.expire_all()
    assert db.query(Task).filter_by(title=marker).count() == 1

    url = make_url(os.environ["DATABASE_URL"])
    dump_path = tmp_path / "akrasia-test.dump"
    env = _pg_env(url)

    subprocess.run(
        [
            "pg_dump",
            *_pg_args(
                url,
                "--dbname",
                url.database,
                "--format=custom",
                "--no-owner",
                "--file",
                str(dump_path),
            ),
        ],
        check=True,
        env=env,
        capture_output=True,
    )
    assert dump_path.is_file()
    assert dump_path.stat().st_size > 0

    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :name "
                    "AND pid <> pg_backend_pid()"
                ),
                {"name": RESTORE_DATABASE_NAME},
            )
            connection.execute(
                text(
                    f'DROP DATABASE IF EXISTS "{RESTORE_DATABASE_NAME}"'
                )
            )
            connection.execute(
                text(
                    f'CREATE DATABASE "{RESTORE_DATABASE_NAME}"'
                )
            )

        subprocess.run(
            [
                "pg_restore",
                *_pg_args(
                    url,
                    "--dbname",
                    RESTORE_DATABASE_NAME,
                    "--no-owner",
                    "--exit-on-error",
                    str(dump_path),
                ),
            ],
            check=True,
            env=env,
            capture_output=True,
        )

        restored_engine = create_engine(
            url.set(database=RESTORE_DATABASE_NAME)
        )
        try:
            Restored = sessionmaker(bind=restored_engine)
            restored = Restored()
            try:
                assert restored.query(Task).filter_by(
                    title=marker
                ).count() == 1
            finally:
                restored.close()
        finally:
            restored_engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :name "
                    "AND pid <> pg_backend_pid()"
                ),
                {"name": RESTORE_DATABASE_NAME},
            )
            connection.execute(
                text(
                    f'DROP DATABASE IF EXISTS "{RESTORE_DATABASE_NAME}"'
                )
            )
        admin.dispose()
