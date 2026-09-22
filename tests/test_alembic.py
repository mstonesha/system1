import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

VERIFY_DATABASE_NAME = "system1_alembic_verify"


def _server_url():
    return make_url(os.environ["DATABASE_URL"])


def _verify_url():
    return _server_url().set(database=VERIFY_DATABASE_NAME)


def _admin_engine() -> Engine:
    return create_engine(
        _server_url().set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )


def _drop_verify_database(admin: Engine) -> None:
    with admin.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :name "
                "AND pid <> pg_backend_pid()"
            ),
            {"name": VERIFY_DATABASE_NAME},
        )
        connection.execute(
            text(
                f'DROP DATABASE IF EXISTS "{VERIFY_DATABASE_NAME}"'
            )
        )


def _create_verify_database(admin: Engine) -> None:
    with admin.connect() as connection:
        connection.execute(
            text(
                f'CREATE DATABASE "{VERIFY_DATABASE_NAME}"'
            )
        )


def _run_alembic(connection, revision: str, *, downgrade: bool = False) -> None:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection

    if downgrade:
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


def _schema_facts(connection) -> dict:
    tables = {
        row[0]
        for row in connection.execute(
            text(
                "SELECT tablename "
                "FROM pg_tables "
                "WHERE schemaname = 'public'"
            )
        )
    }
    columns = {
        (row[0], row[1]): row[2]
        for row in connection.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
    }
    constraints = {
        row[0]: row[1]
        for row in connection.execute(
            text(
                "SELECT con.conname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
                "WHERE nsp.nspname = 'public' "
                "AND rel.relname IN "
                "('tasks', 'daily_tasks', 'work_sessions', "
                "'browser_sessions')"
            )
        )
    }
    indexes = {
        row[0]: row[1]
        for row in connection.execute(
            text(
                "SELECT indexname, indexdef "
                "FROM pg_indexes "
                "WHERE schemaname = 'public'"
            )
        )
    }
    alembic_head = connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()

    return {
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "alembic_head": alembic_head,
    }


def _assert_current_schema(facts: dict) -> None:
    assert facts["alembic_head"] == "d4b8c1e0a2f5"
    assert facts["tables"] >= {
        "tasks",
        "daily_tasks",
        "work_sessions",
        "browser_sessions",
        "alembic_version",
    }
    assert "users" not in facts["tables"]

    for table_name, column_name in (
        ("tasks", "created_at"),
        ("tasks", "completed_at"),
        ("daily_tasks", "created_at"),
        ("work_sessions", "started_at"),
        ("work_sessions", "ended_at"),
        ("work_sessions", "created_at"),
    ):
        assert (
            facts["columns"][(table_name, column_name)]
            == "timestamp with time zone"
        )

    assert facts["columns"][("tasks", "due_date")] == "date"
    assert facts["columns"][("tasks", "area")] == "character varying"
    assert facts["columns"][("daily_tasks", "date")] == "date"

    assert "FOREIGN KEY (parent_task_id) REFERENCES tasks(id)" in facts[
        "constraints"
    ]["tasks_parent_task_id_fkey"]
    assert "FOREIGN KEY (task_id) REFERENCES tasks(id)" in facts[
        "constraints"
    ]["daily_tasks_task_id_fkey"]
    assert (
        "FOREIGN KEY (daily_task_id) REFERENCES daily_tasks(id)"
        in facts["constraints"]["work_sessions_daily_task_id_fkey"]
    )
    assert (
        facts["constraints"]["uq_daily_task_task_date"]
        == "UNIQUE (task_id, date)"
    )

    running_index = facts["indexes"]["uq_work_sessions_one_running"]
    assert "UNIQUE INDEX" in running_index
    assert "session_state" in running_index
    assert "WHERE" in running_index
    assert "running" in running_index

    for column_name in (
        "created_at",
        "last_seen_at",
        "idle_expires_at",
        "absolute_expires_at",
        "revoked_at",
    ):
        assert (
            facts["columns"][
                ("browser_sessions", column_name)
            ]
            == "timestamp with time zone"
        )

    assert (
        facts["constraints"]["uq_browser_sessions_token_hash"]
        == "UNIQUE (token_hash)"
    )


def test_alembic_upgrade_head_on_empty_database():
    admin = _admin_engine()

    try:
        _drop_verify_database(admin)
        _create_verify_database(admin)

        verify_engine = create_engine(_verify_url())
        try:
            with verify_engine.connect() as connection:
                _run_alembic(connection, "head")
                connection.commit()
                _assert_current_schema(_schema_facts(connection))

                _run_alembic(
                    connection,
                    "f1a9b3c4d5e6",
                    downgrade=True,
                )
                connection.commit()
                after_one_step = _schema_facts(connection)
                assert after_one_step["alembic_head"] == (
                    "f1a9b3c4d5e6"
                )
                assert "browser_sessions" not in (
                    after_one_step["tables"]
                )

                _run_alembic(connection, "head")
                connection.commit()
                _assert_current_schema(_schema_facts(connection))

                _run_alembic(connection, "base", downgrade=True)
                connection.commit()
                _run_alembic(connection, "head")
                connection.commit()
                _assert_current_schema(_schema_facts(connection))
        finally:
            verify_engine.dispose()
    finally:
        _drop_verify_database(admin)
        admin.dispose()


def test_alembic_task_area_backfills_existing_rows():
    admin = _admin_engine()

    try:
        _drop_verify_database(admin)
        _create_verify_database(admin)

        verify_engine = create_engine(_verify_url())
        try:
            with verify_engine.connect() as connection:
                _run_alembic(connection, "c3f8e2a1b0d4")
                connection.commit()

                connection.execute(
                    text(
                        "INSERT INTO tasks "
                        "(title, status, created_at, "
                        "priority, sort_order) "
                        "VALUES "
                        "('Legacy task', 'active', NOW(), 3, 0)"
                    )
                )
                connection.commit()

                _run_alembic(connection, "head")
                connection.commit()

                area = connection.execute(
                    text(
                        "SELECT area FROM tasks "
                        "WHERE title = 'Legacy task'"
                    )
                ).scalar_one()
                assert area == "general"

                column = connection.execute(
                    text(
                        "SELECT is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'tasks' "
                        "AND column_name = 'area'"
                    )
                ).one()
                assert column.is_nullable == "NO"
                assert column.column_default is None
        finally:
            verify_engine.dispose()
    finally:
        _drop_verify_database(admin)
        admin.dispose()
