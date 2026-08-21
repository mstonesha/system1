from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models import DailyTask, WorkSession


def get_completed_sessions_for_date(
    db: Session,
    target_date: date,
) -> list[WorkSession]:
    start_of_day = datetime.combine(
        target_date,
        time.min,
    )

    end_of_day = datetime.combine(
        target_date,
        time.max,
    )

    return (
        db.query(WorkSession)
        .filter(
            WorkSession.session_state == "completed",
            WorkSession.started_at >= start_of_day,
            WorkSession.started_at <= end_of_day,
        )
        .order_by(WorkSession.started_at)
        .all()
    )


def build_daily_summary(
    sessions: list[WorkSession],
) -> dict:
    total_focus_seconds = sum(
        session.actual_duration_seconds or 0
        for session in sessions
    )

    outcome_counts = {
        "progress": 0,
        "complete": 0,
        "stuck": 0,
        "paused": 0,
        "abandoned": 0,
    }

    for session in sessions:
        if session.outcome in outcome_counts:
            outcome_counts[session.outcome] += 1

    interrupted_count = sum(
        1
        for session in sessions
        if session.interrupted
    )

    return {
        "total_focus_seconds": total_focus_seconds,
        "total_focus_minutes": total_focus_seconds // 60,
        "session_count": len(sessions),
        "interrupted_count": interrupted_count,
        "progress_count": outcome_counts["progress"],
        "completed_count": outcome_counts["complete"],
        "stuck_count": outcome_counts["stuck"],
        "paused_count": outcome_counts["paused"],
        "abandoned_count": outcome_counts["abandoned"],
    }


def get_daily_task_performance(
    db: Session,
    target_date: date,
) -> list[dict]:
    previous_date = target_date - timedelta(days=1)
    next_date = target_date + timedelta(days=1)

    daily_tasks = (
        db.query(DailyTask)
        .filter(
            DailyTask.date == target_date,
        )
        .order_by(
            DailyTask.sort_order,
            DailyTask.created_at,
        )
        .all()
    )

    previous_day_tasks = (
        db.query(DailyTask)
        .filter(
            DailyTask.date == previous_date,
        )
        .all()
    )

    next_day_tasks = (
        db.query(DailyTask)
        .filter(
            DailyTask.date == next_date,
        )
        .all()
    )

    previous_task_ids = {
        daily_task.task_id
        for daily_task in previous_day_tasks
    }

    next_task_ids = {
        daily_task.task_id
        for daily_task in next_day_tasks
    }

    performance = []

    for daily_task in daily_tasks:
        completed_sessions = [
            session
            for session in daily_task.work_sessions
            if session.session_state == "completed"
        ]

        completed_sessions.sort(
            key=lambda session: session.started_at
        )

        actual_sessions = len(
            completed_sessions
        )

        actual_focus_seconds = sum(
            session.actual_duration_seconds or 0
            for session in completed_sessions
        )

        latest_outcome = None

        if completed_sessions:
            latest_outcome = (
                completed_sessions[-1].outcome
            )

        performance.append({
            "task_title": daily_task.task.title,
            "planned_sessions": (
                daily_task.planned_sessions or 0
            ),
            "actual_sessions": actual_sessions,
            "actual_focus_minutes": (
                actual_focus_seconds // 60
            ),
            "state": daily_task.state,
            "latest_outcome": latest_outcome,
            "present_previous_day": (
                daily_task.task_id
                in previous_task_ids
            ),
            "present_next_day": (
                daily_task.task_id
                in next_task_ids
            ),
        })

    return performance

def build_daily_review(
    db: Session,
    target_date: date,
) -> dict:
    sessions = get_completed_sessions_for_date(
        db=db,
        target_date=target_date,
    )

    summary = build_daily_summary(
        sessions=sessions,
    )

    task_performance = get_daily_task_performance(
        db=db,
        target_date=target_date,
    )

    return {
        "date": target_date,
        "summary": summary,
        "task_performance": task_performance,
        "sessions": sessions,
    }