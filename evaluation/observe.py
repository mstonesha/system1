"""Operator diagnostics for evaluation datasets.

These summaries are for humans inspecting a generated evaluation
database. They must not load ground-truth files and are not an
agent API.
"""

from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DailyTask, Task, WorkSession
from evaluation.age import AGE_BUCKETS, classify_age_days
from evaluation.catalog import parse_task_category
from evaluation.change import (
    focus_session_rows,
    infer_start_monday,
    last_n_weeks,
    morning_afternoon_summary,
    preceding_n_weeks,
    rolling_gap,
    rows_for_dates,
    rows_for_weeks,
    weekly_focus_rows,
)
from evaluation.clock import DAY_PARTS, classify_day_part
from evaluation.planning import (
    completed_task_effort_records,
    effort_summary,
    regime_positive_rates,
    unused_planned_breakdown,
    weekly_workload_rows,
)


POSITIVE_OUTCOMES = frozenset({"progress", "complete"})
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
STUCK_TITLE_SAMPLE_LIMIT = 12


def session_observation_rows(db: Session) -> list[dict]:
    zone = ZoneInfo(get_settings().timezone)
    rows = []
    query = (
        db.query(WorkSession, DailyTask, Task)
        .join(DailyTask, WorkSession.daily_task_id == DailyTask.id)
        .join(Task, DailyTask.task_id == Task.id)
    )
    for work, daily, task in query:
        local = work.started_at.astimezone(zone)
        rows.append(
            {
                "work": work,
                "daily": daily,
                "task": task,
                "local": local,
                "day_part": classify_day_part(local),
                "weekday": local.weekday(),
                "weekday_name": WEEKDAYS[local.weekday()],
                "positive": work.outcome in POSITIVE_OUTCOMES,
                "stuck": work.outcome == "stuck",
                "category": parse_task_category(task.title),
                "title": task.title,
            }
        )
    return rows


def bounded_stuck_titles(
    db: Session,
    *,
    limit: int = STUCK_TITLE_SAMPLE_LIMIT,
) -> list[str]:
    """Return a small mixed sample of titles that have a stuck session.

    The sample is bounded and deterministic. It is not a dump of
    full history and is not the hidden ground-truth file.
    """
    titles_in_order: list[str] = []
    seen: set[str] = set()
    query = (
        db.query(Task.title)
        .join(DailyTask, DailyTask.task_id == Task.id)
        .join(WorkSession, WorkSession.daily_task_id == DailyTask.id)
        .filter(WorkSession.outcome == "stuck")
        .order_by(Task.title, Task.id)
    )
    for (title,) in query:
        if title in seen:
            continue
        seen.add(title)
        titles_in_order.append(title)

    hr = [
        title
        for title in titles_in_order
        if parse_task_category(title) == "HR"
    ]
    other_by_category: dict[str, list[str]] = {}
    for title in titles_in_order:
        category = parse_task_category(title)
        if category == "HR":
            continue
        other_by_category.setdefault(category or "unknown", []).append(
            title
        )

    hr_take = min(len(hr), max(3, limit // 2))
    sample = hr[:hr_take]
    categories = sorted(other_by_category)
    indexes = {category: 0 for category in categories}
    while len(sample) < limit and categories:
        added = False
        for category in categories:
            if len(sample) >= limit:
                break
            index = indexes[category]
            titles = other_by_category[category]
            if index < len(titles):
                sample.append(titles[index])
                indexes[category] = index + 1
                added = True
        if not added:
            break
    if len(sample) < min(limit, len(titles_in_order)):
        sample.extend(hr[hr_take : limit - len(sample) + hr_take])
    return sample[:limit]


def print_observed_rates(db: Session) -> None:
    rows = session_observation_rows(db)
    by_weekday = defaultdict(list)
    by_part = defaultdict(list)
    by_cell = defaultdict(list)
    by_interrupted = defaultdict(list)
    by_category_positive = defaultdict(list)
    by_category_stuck = defaultdict(list)
    by_category_interrupted = defaultdict(list)
    interruption_by_part = defaultdict(list)
    category_by_part = defaultdict(list)

    for row in rows:
        weekday = row["weekday_name"]
        part = row["day_part"]
        category = row["category"] or "unknown"
        by_weekday[weekday].append(row["positive"])
        by_part[part].append(row["positive"])
        by_cell[(weekday, part)].append(row["positive"])
        by_interrupted[row["work"].interrupted].append(row["positive"])
        by_category_positive[category].append(row["positive"])
        by_category_stuck[category].append(row["stuck"])
        by_category_interrupted[category].append(row["work"].interrupted)
        interruption_by_part[part].append(row["work"].interrupted)
        category_by_part[part].append(category)

    def _fmt(values: list) -> str:
        if not values:
            return "n=0"
        rate = sum(values) / len(values)
        return f"{rate:6.1%}  n={len(values)}"

    print()
    print("positive outcome rate by weekday")
    for weekday in WEEKDAYS:
        print(f"  {weekday}: {_fmt(by_weekday[weekday])}")

    print("positive outcome rate by day-part")
    for part in DAY_PARTS:
        print(f"  {part}: {_fmt(by_part[part])}")

    print("positive outcome rate by weekday + day-part")
    for weekday in WEEKDAYS:
        for part in DAY_PARTS:
            print(
                f"  {weekday} {part}: "
                f"{_fmt(by_cell[(weekday, part)])}"
            )

    print("positive outcome rate by interrupted")
    print(f"  uninterrupted: {_fmt(by_interrupted[False])}")
    print(f"  interrupted: {_fmt(by_interrupted[True])}")

    print("interruption rate by day-part")
    for part in DAY_PARTS:
        print(f"  {part}: {_fmt(interruption_by_part[part])}")

    categories = sorted(by_category_positive)
    print("positive outcome rate by category")
    for category in categories:
        print(f"  {category}: {_fmt(by_category_positive[category])}")

    print("stuck rate by category")
    for category in categories:
        print(f"  {category}: {_fmt(by_category_stuck[category])}")

    print("interruption rate by category")
    for category in categories:
        print(
            f"  {category}: {_fmt(by_category_interrupted[category])}"
        )

    print("category mix by day-part")
    for part in DAY_PARTS:
        labels = category_by_part[part]
        total = len(labels) or 1
        mix = ", ".join(
            f"{name}={labels.count(name) / total:.0%}"
            for name in sorted(set(labels))
        )
        print(f"  {part}: {mix}")

    print("bounded stuck-title sample")
    for title in bounded_stuck_titles(db):
        print(f"  - {title}")

    print_task_age_tables(db)
    print_planning_tables(db)
    print_behaviour_change_tables(db)


def terminal_task_records(db: Session) -> list[dict]:
    """Task-level terminal outcomes with both age definitions."""
    zone = ZoneInfo(get_settings().timezone)
    records = []
    tasks = (
        db.query(Task)
        .filter(Task.status.in_(("completed", "cancelled")))
        .order_by(Task.id)
        .all()
    )
    for task in tasks:
        dailies = sorted(task.daily_tasks, key=lambda item: item.date)
        if not dailies:
            continue
        terminal_dailies = [
            daily
            for daily in dailies
            if daily.state in {"completed", "abandoned"}
        ]
        if not terminal_dailies:
            continue
        first_date = dailies[0].date
        last_date = max(daily.date for daily in terminal_dailies)
        created_date = task.created_at.astimezone(zone).date()
        sessions = [
            session
            for daily in dailies
            for session in daily.work_sessions
        ]
        if not sessions:
            continue
        last_daily = max(terminal_dailies, key=lambda item: item.date)
        last_sessions = sorted(
            last_daily.work_sessions,
            key=lambda item: item.started_at,
        )
        last_session = last_sessions[-1] if last_sessions else None
        local_last = (
            last_session.started_at.astimezone(zone)
            if last_session is not None
            else None
        )
        interrupted_share = (
            sum(session.interrupted for session in sessions)
            / len(sessions)
        )
        records.append(
            {
                "task": task,
                "category": parse_task_category(task.title),
                "abandoned": task.status == "cancelled",
                "first_today": first_date,
                "terminal_date": last_date,
                "execution_age_days": (last_date - first_date).days,
                "created_age_days": (last_date - created_date).days,
                "backlog_days": (first_date - created_date).days,
                "execution_bucket": classify_age_days(
                    (last_date - first_date).days
                ),
                "created_bucket": classify_age_days(
                    max(0, (last_date - created_date).days)
                ),
                "weekday": last_date.weekday(),
                "day_part": (
                    classify_day_part(local_last)
                    if local_last is not None
                    else None
                ),
                "planned_sessions": last_daily.planned_sessions or 0,
                "interrupted_share": interrupted_share,
                "last_interrupted": (
                    last_session.interrupted
                    if last_session is not None
                    else False
                ),
            }
        )
    return records


def print_task_age_tables(db: Session) -> None:
    records = terminal_task_records(db)
    if not records:
        return

    def _bucket_table(key: str, heading: str) -> None:
        print(heading)
        print(
            "  bucket  n_terminal  completed  abandoned  rate"
        )
        for name, _low, _high in AGE_BUCKETS:
            matched = [row for row in records if row[key] == name]
            if not matched:
                print(f"  {name:<6}  n=0")
                continue
            abandoned = sum(row["abandoned"] for row in matched)
            completed = len(matched) - abandoned
            rate = abandoned / len(matched)
            print(
                f"  {name:<6}  {len(matched):10d}  "
                f"{completed:9d}  {abandoned:9d}  {rate:6.1%}"
            )

    _bucket_table(
        "execution_bucket",
        "terminal abandonment by execution age "
        "(first DailyTask → terminal)",
    )
    _bucket_table(
        "created_bucket",
        "terminal abandonment by Task.created_at age "
        "(backlog-inclusive)",
    )

    print("terminal abandonment by category")
    categories = sorted(
        {row["category"] or "unknown" for row in records}
    )
    for category in categories:
        matched = [
            row
            for row in records
            if (row["category"] or "unknown") == category
        ]
        rate = sum(row["abandoned"] for row in matched) / len(matched)
        print(
            f"  {category}: {rate:6.1%}  n={len(matched)}"
        )

    print("terminal abandonment by last-session interrupted")
    for label, value in (("uninterrupted", False), ("interrupted", True)):
        matched = [
            row for row in records if row["last_interrupted"] is value
        ]
        if not matched:
            print(f"  {label}: n=0")
            continue
        rate = sum(row["abandoned"] for row in matched) / len(matched)
        print(f"  {label}: {rate:6.1%}  n={len(matched)}")

    print("terminal abandonment by weekday of terminal date")
    for weekday, name in enumerate(WEEKDAYS):
        matched = [row for row in records if row["weekday"] == weekday]
        if not matched:
            print(f"  {name}: n=0")
            continue
        rate = sum(row["abandoned"] for row in matched) / len(matched)
        print(f"  {name}: {rate:6.1%}  n={len(matched)}")

    print("terminal abandonment by day-part of last session")
    for part in DAY_PARTS:
        matched = [row for row in records if row["day_part"] == part]
        if not matched:
            print(f"  {part}: n=0")
            continue
        rate = sum(row["abandoned"] for row in matched) / len(matched)
        print(f"  {part}: {rate:6.1%}  n={len(matched)}")

    print("terminal abandonment by planned_sessions on terminal DailyTask")
    planned_values = sorted({row["planned_sessions"] for row in records})
    for planned in planned_values:
        matched = [
            row for row in records if row["planned_sessions"] == planned
        ]
        rate = sum(row["abandoned"] for row in matched) / len(matched)
        print(f"  planned={planned}: {rate:6.1%}  n={len(matched)}")

    print("sample size: long-backlog young-execution tasks")
    mixed = [
        row
        for row in records
        if row["execution_age_days"] <= 7
        and row["backlog_days"] >= 14
    ]
    if mixed:
        rate = sum(row["abandoned"] for row in mixed) / len(mixed)
        print(
            f"  execution age ≤7 and backlog ≥14: "
            f"{rate:6.1%} abandoned  n={len(mixed)}"
        )
    else:
        print("  n=0")


def print_planning_tables(db: Session) -> None:
    first = db.query(DailyTask.date).order_by(DailyTask.date).first()
    if first is None:
        return
    start = first[0]
    start = start.fromordinal(start.toordinal() - start.weekday())
    totals = unused_planned_breakdown(db)
    weeks = weekly_workload_rows(db, start)
    efforts = completed_task_effort_records(db)
    summary = effort_summary(efforts)
    regimes = regime_positive_rates(weeks)

    print("planned vs actual sessions")
    print(f"  planned_sessions: {totals['planned_sessions']}")
    print(f"  actual_sessions: {totals['actual_sessions']}")
    print(f"  execution_ratio: {totals['execution_ratio']:.3f}")
    print(
        "  planned_over_actual_gap: "
        f"{totals['planned_over_actual_gap']:.1%}"
    )
    print("unused planned sessions")
    print(
        f"  unused_total: {totals['unused_planned_sessions']}"
    )
    print(
        "  unused_due_to_completion: "
        f"{totals['unused_due_to_completion']}"
    )
    print(
        "  unused_while_task_remained_active: "
        f"{totals['unused_while_task_remained_active']}"
    )
    print(
        "  unused_with_terminal_abandonment: "
        f"{totals['unused_with_terminal_abandonment']}"
    )
    print("DailyTask execution cases")
    print(
        f"  case_a_active_shortfall: "
        f"{totals['case_a_active_shortfall']}"
    )
    print(
        f"  case_b_early_completion: "
        f"{totals['case_b_early_completion']}"
    )
    print(f"  case_c_extra_work: {totals['case_c_extra_work']}")
    print(
        f"  case_d_executed_as_planned: "
        f"{totals['case_d_executed_as_planned']}"
    )

    print("completed Task estimated_sessions vs actual lifetime sessions")
    if summary["n"]:
        print(f"  n_completed_with_estimate: {summary['n']}")
        print(f"  mean_estimated: {summary['mean_estimated']:.2f}")
        print(f"  mean_actual: {summary['mean_actual']:.2f}")
        print(f"  mean_ratio: {summary['mean_ratio']:.3f}")
        print(f"  median_ratio: {summary['median_ratio']:.3f}")
        print(
            "  below/near/above estimate: "
            f"{summary['below']}/{summary['near']}/{summary['above']}"
        )
    else:
        print("  n=0")

    print("weekly workload")
    print(
        "  week  start       dailies  planned  actual  "
        "pos_rate  regime"
    )
    for row in weeks:
        print(
            f"  {row['week']:4d}  "
            f"{row['week_start'].isoformat()}  "
            f"{row['daily_task_count']:7d}  "
            f"{row['planned_sessions']:7d}  "
            f"{row['actual_sessions']:6d}  "
            f"{row['positive_rate']:7.1%}  "
            f"{row['observed_regime']}"
        )

    print("observed regime summary")
    for name in ("normal", "transitional", "heavy"):
        stats = regimes.get(name)
        if not stats:
            continue
        print(
            f"  {name}: pos={stats['positive_rate']:.1%}  "
            f"weeks={stats['weeks']}  "
            f"mean_planned={stats['mean_planned']:.1f}  "
            f"mean_dailies={stats['mean_daily_tasks']:.1f}  "
            f"n={stats['session_count']}"
        )


def print_behaviour_change_tables(db: Session) -> None:
    start = infer_start_monday(db)
    if start is None:
        return
    rows = focus_session_rows(db)
    if not rows:
        return
    weekly = weekly_focus_rows(rows, start)
    last_date = max(row["local_date"] for row in rows)

    def _print_window(title: str, matched: list[dict]) -> None:
        summary = morning_afternoon_summary(matched)
        print(
            f"  {title}: "
            f"morning {summary['morning_rate']:6.1%} "
            f"n={summary['morning_n']}  "
            f"afternoon {summary['afternoon_rate']:6.1%} "
            f"n={summary['afternoon_n']}  "
            f"gap {summary['gap']:+6.1%}"
        )

    print("morning vs afternoon by window")
    _print_window("lifetime", rows)
    _print_window("weeks 1-22 historical", rows_for_weeks(rows, start, 1, 22))
    _print_window("weeks 23-28 transition", rows_for_weeks(rows, start, 23, 28))
    _print_window("weeks 29-36 recent", rows_for_weeks(rows, start, 29, 36))
    _print_window("last 16 weeks", last_n_weeks(rows, start, 16))
    _print_window("last 8 weeks", last_n_weeks(rows, start, 8))
    _print_window(
        "preceding 16 weeks",
        preceding_n_weeks(
            rows,
            start,
            recent_weeks=8,
            preceding_weeks=16,
        ),
    )
    _print_window("last 6 weeks", last_n_weeks(rows, start, 6))
    _print_window(
        "previous 12 weeks",
        preceding_n_weeks(
            rows,
            start,
            recent_weeks=6,
            preceding_weeks=12,
        ),
    )
    _print_window(
        "last 30 days",
        rows_for_dates(rows, last_date - timedelta(days=29), last_date),
    )
    _print_window(
        "previous 90 days",
        rows_for_dates(
            rows,
            last_date - timedelta(days=119),
            last_date - timedelta(days=30),
        ),
    )

    print("weekly morning vs afternoon")
    print(
        "  week  start       morn_n  morn_rate  aft_n  aft_rate   gap"
    )
    for row in weekly:
        print(
            f"  {row['week']:4d}  {row['week_start'].isoformat()}  "
            f"{row['morning_n']:6d}  {row['morning_rate']:9.1%}  "
            f"{row['afternoon_n']:5d}  {row['afternoon_rate']:8.1%}  "
            f"{row['gap']:+6.1%}"
        )

    print("rolling 4-week morning-minus-afternoon gap")
    for row in rolling_gap(weekly, 4):
        print(
            f"  through week {row['through_week']:2d}: "
            f"gap {row['gap']:+6.1%}  "
            f"morning n={row['morning_n']}  "
            f"afternoon n={row['afternoon_n']}"
        )

    print("rolling 6-week morning-minus-afternoon gap")
    for row in rolling_gap(weekly, 6):
        print(
            f"  through week {row['through_week']:2d}: "
            f"gap {row['gap']:+6.1%}  "
            f"morning n={row['morning_n']}  "
            f"afternoon n={row['afternoon_n']}"
        )

    print("interruption rate by evaluator phase window")
    for title, matched in (
        ("weeks 1-22", rows_for_weeks(rows, start, 1, 22)),
        ("weeks 23-28", rows_for_weeks(rows, start, 23, 28)),
        ("weeks 29-36", rows_for_weeks(rows, start, 29, 36)),
    ):
        if not matched:
            continue
        rate = sum(row["interrupted"] for row in matched) / len(matched)
        print(f"  {title}: interrupted={rate:.1%}  n={len(matched)}")
