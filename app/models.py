from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Boolean,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.time import utc_now


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)

    title: Mapped[str] = mapped_column(String(200))

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="active",
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        default=3,
    )

    estimated_sessions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    parent: Mapped["Task | None"] = relationship(
        remote_side=[id],
        back_populates="children",
    )

    children: Mapped[list["Task"]] = relationship(
        back_populates="parent",
    )

    daily_tasks: Mapped[list["DailyTask"]] = relationship(
        back_populates="task",
    )

class DailyTask(Base):
    __tablename__ = "daily_tasks"

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "date",
            name="uq_daily_task_task_date",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"),
        nullable=False,
    )

    date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    planned_sessions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    state: Mapped[str] = mapped_column(
        String(30),
        default="planned",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    task: Mapped["Task"] = relationship(
        back_populates="daily_tasks",
    )

    work_sessions: Mapped[list["WorkSession"]] = relationship(
        back_populates="daily_task",
    )

class WorkSession(Base):
    __tablename__ = "work_sessions"

    __table_args__ = (
        Index(
            "uq_work_sessions_one_running",
            "session_state",
            unique=True,
            postgresql_where=text(
                "session_state = 'running'"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    daily_task_id: Mapped[int] = mapped_column(
        ForeignKey("daily_tasks.id"),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    planned_duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1500,
    )

    actual_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    session_state: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="running",
    )

    outcome: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    interrupted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

    daily_task: Mapped["DailyTask"] = relationship(
        back_populates="work_sessions",
    )


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    __table_args__ = (
        UniqueConstraint(
            "token_hash",
            name="uq_browser_sessions_token_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    csrf_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
