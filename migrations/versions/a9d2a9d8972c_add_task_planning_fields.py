"""Add task planning fields

Revision ID: a9d2a9d8972c
Revises: 5bf791956b3a
Create Date: 2026-08-18 13:21:56.914716

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9d2a9d8972c'
down_revision: Union[str, Sequence[str], None] = '5bf791956b3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "priority",
            sa.Integer(),
            server_default="3",
            nullable=False,
        ),
    )

    op.add_column(
        "tasks",
        sa.Column(
            "estimated_sessions",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "tasks",
        sa.Column(
            "due_date",
            sa.Date(),
            nullable=True,
        ),
    )

    op.add_column(
        "tasks",
        sa.Column(
            "completed_at",
            sa.DateTime(),
            nullable=True,
        ),
    )

    op.add_column(
        "tasks",
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )

    op.alter_column(
        "tasks",
        "priority",
        server_default=None,
    )

    op.alter_column(
        "tasks",
        "sort_order",
        server_default=None,
    )