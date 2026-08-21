"""One running work session

Revision ID: e8c41d2a7b90
Revises: 506da97ac33d
Create Date: 2026-08-21 20:34:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8c41d2a7b90"
down_revision: Union[str, Sequence[str], None] = "506da97ac33d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_work_sessions_one_running",
        "work_sessions",
        ["session_state"],
        unique=True,
        postgresql_where=sa.text("session_state = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_work_sessions_one_running",
        table_name="work_sessions",
    )
