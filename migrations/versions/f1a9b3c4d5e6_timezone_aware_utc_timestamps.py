"""Timezone-aware UTC timestamps

Revision ID: f1a9b3c4d5e6
Revises: e8c41d2a7b90
Create Date: 2026-08-21 21:07:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a9b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e8c41d2a7b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UTC_USING = "{column} AT TIME ZONE 'UTC'"


def _to_timestamptz(
    table_name: str,
    column_name: str,
    *,
    existing_nullable: bool,
) -> None:
    op.alter_column(
        table_name,
        column_name,
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(timezone=False),
        existing_nullable=existing_nullable,
        postgresql_using=UTC_USING.format(column=column_name),
    )


def _to_timestamp(
    table_name: str,
    column_name: str,
    *,
    existing_nullable: bool,
) -> None:
    op.alter_column(
        table_name,
        column_name,
        type_=sa.DateTime(timezone=False),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=existing_nullable,
        postgresql_using=UTC_USING.format(column=column_name),
    )


def upgrade() -> None:
    _to_timestamptz("tasks", "created_at", existing_nullable=False)
    _to_timestamptz("tasks", "completed_at", existing_nullable=True)
    _to_timestamptz(
        "daily_tasks",
        "created_at",
        existing_nullable=False,
    )
    _to_timestamptz(
        "work_sessions",
        "started_at",
        existing_nullable=False,
    )
    _to_timestamptz(
        "work_sessions",
        "ended_at",
        existing_nullable=True,
    )
    _to_timestamptz(
        "work_sessions",
        "created_at",
        existing_nullable=False,
    )


def downgrade() -> None:
    _to_timestamp(
        "work_sessions",
        "created_at",
        existing_nullable=False,
    )
    _to_timestamp(
        "work_sessions",
        "ended_at",
        existing_nullable=True,
    )
    _to_timestamp(
        "work_sessions",
        "started_at",
        existing_nullable=False,
    )
    _to_timestamp(
        "daily_tasks",
        "created_at",
        existing_nullable=False,
    )
    _to_timestamp("tasks", "completed_at", existing_nullable=True)
    _to_timestamp("tasks", "created_at", existing_nullable=False)
