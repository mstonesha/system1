"""Add task area

Revision ID: d4b8c1e0a2f5
Revises: c3f8e2a1b0d4
Create Date: 2026-09-22 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4b8c1e0a2f5"
down_revision: Union[str, Sequence[str], None] = "c3f8e2a1b0d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "area",
            sa.String(length=30),
            server_default=sa.text("'general'"),
            nullable=False,
        ),
    )

    op.alter_column(
        "tasks",
        "area",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("tasks", "area")
