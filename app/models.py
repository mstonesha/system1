from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
        DateTime,
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
        DateTime,
        default=datetime.utcnow,
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
        DateTime,
        default=datetime.utcnow,
    )

    task: Mapped["Task"] = relationship(
        back_populates="daily_tasks",
    )
