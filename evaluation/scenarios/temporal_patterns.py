"""Dataset A — temporal_patterns.

Morning sessions are generally more successful, except Monday
mornings, which are a strong negative exception.

This module must not read evaluation/ground_truth/. Ground truth is
for human/test verification only and must not be supplied to an agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import DailyTask, Task, WorkSession
from evaluation.catalog import (
    CATEGORIES,
    TITLE_STEMS,
    format_task_title,
)
from evaluation.clock import (
    DAY_PART_BOUNDS,
    DAY_PART_WEIGHTS,
    classify_day_part,
    combine_local,
    local_to_utc,
    working_days,
)


SCENARIO_NAME = "temporal_patterns"
DEFAULT_SEED = 20250106
START_DATE = date(2025, 1, 6)
WORKING_WEEKS = 12
INTERRUPTION_RATE = 0.14

POSITIVE_OUTCOMES = frozenset({"progress", "complete"})
NEGATIVE_OUTCOMES = frozenset({"stuck", "paused", "abandoned"})

# Tuesday–Friday positive-outcome rates by local day-part.
TUE_FRI_POSITIVE_RATE = {
    "early_morning": 0.70,
    "morning": 0.78,
    "early_afternoon": 0.58,
    "late_afternoon": 0.48,
    "evening": 0.55,
}

# Monday is slightly weaker overall, with a sharp morning exception.
MONDAY_POSITIVE_RATE = {
    "early_morning": 0.64,
    "morning": 0.38,
    "early_afternoon": 0.54,
    "late_afternoon": 0.44,
    "evening": 0.50,
}

PROGRESS_GIVEN_POSITIVE = 0.70
NEGATIVE_OUTCOME_WEIGHTS = (
    ("stuck", 0.55),
    ("paused", 0.35),
    ("abandoned", 0.10),
)


@dataclass
class GenerationResult:
    scenario: str
    seed: int
    start_date: date
    end_date: date
    task_count: int
    daily_task_count: int
    work_session_count: int


@dataclass
class TaskPlan:
    task: Task
    category: str
    is_parent: bool
    remaining_days: int
    days_worked: int = 0
    ended: bool = False


@dataclass
class SessionPlan:
    day_part: str
    started_at_local: datetime
    ended_at_local: datetime
    planned_duration_seconds: int
    actual_duration_seconds: int
    outcome: str
    interrupted: bool


def generate_temporal_patterns(
    db: Session,
    *,
    seed: int = DEFAULT_SEED,
    timezone_name: str,
) -> GenerationResult:
    rng = Random(seed)
    zone = ZoneInfo(timezone_name)
    days = working_days(START_DATE, WORKING_WEEKS)
    generator = _TemporalPatternGenerator(
        db=db,
        rng=rng,
        zone=zone,
        days=days,
    )
    generator.run()

    return GenerationResult(
        scenario=SCENARIO_NAME,
        seed=seed,
        start_date=days[0],
        end_date=days[-1],
        task_count=db.query(Task).count(),
        daily_task_count=db.query(DailyTask).count(),
        work_session_count=db.query(WorkSession).count(),
    )


class _TemporalPatternGenerator:
    def __init__(
        self,
        db: Session,
        rng: Random,
        zone: ZoneInfo,
        days: list[date],
    ) -> None:
        self.db = db
        self.rng = rng
        self.zone = zone
        self.days = days
        self.plans: dict[int, TaskPlan] = {}
        self.sort_order = 0
        self.category_bag: list[str] = []
        self.stem_index = {category: 0 for category in CATEGORIES}
        self.used_titles: set[str] = set()

    def run(self) -> None:
        self._seed_initial_tasks()
        activity = self._activity_by_day()

        for day in self.days:
            self._spawn_until_pool_filled(created_on=day)
            level = activity[day]
            if level == "empty":
                continue
            self._generate_day(day, quiet=(level == "quiet"))

        self.db.flush()

    def _activity_by_day(self) -> dict[date, str]:
        n_days = len(self.days)
        n_empty = self.rng.randint(2, 4)
        n_quiet = self.rng.randint(2, 4)
        labels = (
            ["empty"] * n_empty
            + ["quiet"] * n_quiet
            + ["normal"] * (n_days - n_empty - n_quiet)
        )
        self.rng.shuffle(labels)
        return dict(zip(self.days, labels))

    def _seed_initial_tasks(self) -> None:
        for _ in range(12):
            created_on = START_DATE - timedelta(
                days=self.rng.randint(1, 12)
            )
            self._spawn_task(created_on=created_on)

    def _spawn_until_pool_filled(self, created_on: date) -> None:
        target = self.rng.randint(10, 14)
        while len(self._active_leaves()) < target:
            self._spawn_task(created_on=created_on)

    def _active_leaves(self) -> list[TaskPlan]:
        return [
            plan
            for plan in self.plans.values()
            if not plan.ended
            and not plan.is_parent
            and plan.task.status == "active"
        ]

    def _active_parents(self) -> list[TaskPlan]:
        return [
            plan
            for plan in self.plans.values()
            if plan.is_parent
            and not plan.ended
            and plan.task.status == "active"
        ]

    def _next_category(self) -> str:
        if not self.category_bag:
            self.category_bag = list(CATEGORIES)
            self.rng.shuffle(self.category_bag)
        return self.category_bag.pop()

    def _next_title(self, category: str) -> str:
        stems = TITLE_STEMS[category]
        stem = stems[self.stem_index[category] % len(stems)]
        self.stem_index[category] += 1
        title = format_task_title(category, stem)
        suffix = 2
        while title in self.used_titles:
            title = format_task_title(
                category,
                stem,
                f"({suffix})",
            )
            suffix += 1
        self.used_titles.add(title)
        return title

    def _spawn_task(self, created_on: date) -> TaskPlan:
        category = self._next_category()
        is_parent = self.rng.random() < 0.12
        parent_id = None

        if not is_parent and self.rng.random() < 0.22:
            parents = self._active_parents()
            if parents:
                parent_id = self.rng.choice(parents).task.id

        created_at = local_to_utc(
            combine_local(
                created_on,
                time(
                    self.rng.randint(7, 18),
                    self.rng.randint(0, 59),
                    self.rng.randint(0, 59),
                ),
                self.zone,
            )
        )

        due_date = None
        if self.rng.random() < 0.28:
            due_date = created_on + timedelta(
                days=self.rng.randint(3, 21)
            )

        task = Task(
            title=self._next_title(category),
            description=None,
            status="active",
            priority=self.rng.randint(1, 5),
            estimated_sessions=self.rng.choice(
                [2, 3, 3, 4, 5, 6, 8]
            ),
            due_date=due_date,
            completed_at=None,
            sort_order=self.sort_order,
            parent_task_id=parent_id,
            created_at=created_at,
        )
        self.sort_order += 1
        self.db.add(task)
        self.db.flush()

        plan = TaskPlan(
            task=task,
            category=category,
            is_parent=is_parent,
            remaining_days=self.rng.randint(1, 5),
        )
        self.plans[task.id] = plan
        return plan

    def _generate_day(self, day: date, *, quiet: bool) -> None:
        leaves = self._active_leaves()
        if not leaves:
            return

        if quiet:
            n_daily = min(len(leaves), self.rng.randint(1, 2))
            n_sessions = self.rng.randint(1, 3)
        else:
            n_daily = min(len(leaves), self.rng.randint(4, 7))
            n_sessions = self.rng.randint(8, 12)

        chosen = self.rng.sample(leaves, k=n_daily)
        self.rng.shuffle(chosen)
        daily_rows: list[DailyTask] = []

        for index, plan in enumerate(chosen):
            created_at = local_to_utc(
                combine_local(
                    day,
                    time(5, 30 + index, self.rng.randint(0, 59)),
                    self.zone,
                )
            )
            daily = DailyTask(
                task_id=plan.task.id,
                date=day,
                planned_sessions=self.rng.choice(
                    [1, 2, 2, 3, 3, 4]
                ),
                state="planned",
                sort_order=index,
                created_at=created_at,
            )
            self.db.add(daily)
            daily_rows.append(daily)

        self.db.flush()

        session_plans = self._plan_sessions(day, n_sessions)
        holders = self._assign_sessions(daily_rows, session_plans)

        for daily, session_plan in holders:
            self._insert_session(daily, session_plan)

        self.db.flush()
        self._apply_day_endings(chosen, daily_rows)

    def _plan_sessions(
        self,
        day: date,
        n_sessions: int,
    ) -> list[SessionPlan]:
        parts = [
            self._weighted_choice(DAY_PART_WEIGHTS)
            for _ in range(n_sessions)
        ]
        grouped: dict[str, list[int]] = {}
        for index, part in enumerate(parts):
            grouped.setdefault(part, []).append(index)

        slots: list[SessionPlan | None] = [None] * n_sessions
        weekday = day.weekday()

        for part, indexes in grouped.items():
            start_bound, end_bound = DAY_PART_BOUNDS[part]
            start_minutes = (
                start_bound.hour * 60 + start_bound.minute
            )
            end_minutes = end_bound.hour * 60 + end_bound.minute
            span = end_minutes - start_minutes
            slot_span = span / len(indexes)

            for offset, index in enumerate(indexes):
                slot_start = start_minutes + int(offset * slot_span)
                jitter_cap = max(1, int(slot_span) - 8)
                minute = slot_start + self.rng.randint(0, jitter_cap)
                minute = min(minute, end_minutes - 8)
                local_time = time(
                    minute // 60,
                    minute % 60,
                    self.rng.randint(0, 59),
                )
                started_local = combine_local(
                    day,
                    local_time,
                    self.zone,
                )
                planned = self.rng.choice(
                    [20, 25, 25, 25, 30]
                ) * 60
                actual = max(
                    8 * 60,
                    int(planned * self.rng.uniform(0.55, 1.0)),
                )
                ended_local = started_local + timedelta(
                    seconds=actual
                )
                slots[index] = SessionPlan(
                    day_part=part,
                    started_at_local=started_local,
                    ended_at_local=ended_local,
                    planned_duration_seconds=planned,
                    actual_duration_seconds=actual,
                    outcome="progress",
                    interrupted=self.rng.random() < INTERRUPTION_RATE,
                )

        planned_sessions = [slot for slot in slots if slot is not None]
        planned_sessions.sort(key=lambda item: item.started_at_local)
        self._resolve_overlaps(planned_sessions, day)
        for session in planned_sessions:
            session.day_part = classify_day_part(
                session.started_at_local
            )
            session.outcome = self._sample_outcome(
                weekday,
                session.day_part,
            )
        return planned_sessions

    def _resolve_overlaps(
        self,
        sessions: list[SessionPlan],
        day: date,
    ) -> None:
        previous_end: datetime | None = None
        latest_start = combine_local(day, time(22, 40), self.zone)
        for session in sessions:
            if (
                previous_end is not None
                and session.started_at_local < previous_end
            ):
                session.started_at_local = previous_end + timedelta(
                    minutes=self.rng.randint(3, 8)
                )
            if session.started_at_local > latest_start:
                session.started_at_local = latest_start
            session.ended_at_local = (
                session.started_at_local
                + timedelta(
                    seconds=session.actual_duration_seconds
                )
            )
            previous_end = session.ended_at_local

    def _assign_sessions(
        self,
        daily_rows: list[DailyTask],
        session_plans: list[SessionPlan],
    ) -> list[tuple[DailyTask, SessionPlan]]:
        holders: list[tuple[DailyTask, SessionPlan]] = []
        shuffled = list(daily_rows)
        self.rng.shuffle(shuffled)
        n_daily = len(shuffled)
        for index, session_plan in enumerate(session_plans):
            if index < n_daily:
                daily = shuffled[index]
            else:
                daily = self.rng.choice(shuffled)
            holders.append((daily, session_plan))
        return holders

    def _insert_session(
        self,
        daily: DailyTask,
        session_plan: SessionPlan,
    ) -> WorkSession:
        started_at = local_to_utc(session_plan.started_at_local)
        ended_at = local_to_utc(session_plan.ended_at_local)
        work_session = WorkSession(
            daily_task_id=daily.id,
            started_at=started_at,
            ended_at=ended_at,
            planned_duration_seconds=(
                session_plan.planned_duration_seconds
            ),
            actual_duration_seconds=(
                session_plan.actual_duration_seconds
            ),
            session_state="completed",
            outcome=session_plan.outcome,
            interrupted=session_plan.interrupted,
            note=None,
            created_at=started_at,
        )
        self.db.add(work_session)
        return work_session

    def _apply_day_endings(
        self,
        chosen: list[TaskPlan],
        daily_rows: list[DailyTask],
    ) -> None:
        daily_by_task = {
            daily.task_id: daily for daily in daily_rows
        }

        for plan in chosen:
            plan.days_worked += 1
            plan.remaining_days -= 1
            daily = daily_by_task[plan.task.id]
            sessions = (
                self.db.query(WorkSession)
                .filter(WorkSession.daily_task_id == daily.id)
                .order_by(WorkSession.started_at)
                .all()
            )
            if not sessions:
                continue

            last = sessions[-1]
            for session in sessions[:-1]:
                if session.outcome == "complete":
                    session.outcome = "progress"
                elif session.outcome == "abandoned":
                    session.outcome = "stuck"

            # Completing a parent while children are active is invalid.
            if plan.is_parent:
                if last.outcome == "complete":
                    last.outcome = "progress"
                elif last.outcome == "abandoned":
                    last.outcome = "stuck"
                continue

            # Keep most tasks alive past the first day so some span dates.
            if plan.days_worked == 1:
                if last.outcome == "complete":
                    last.outcome = "progress"
                if last.outcome == "abandoned":
                    self._abandon_task(plan, daily, last)
                elif plan.remaining_days <= 0:
                    plan.remaining_days = 1
                continue

            if last.outcome == "complete":
                self._complete_task(plan, daily, last)
            elif last.outcome == "abandoned":
                self._abandon_task(plan, daily, last)

    def _complete_task(
        self,
        plan: TaskPlan,
        daily: DailyTask,
        session: WorkSession,
    ) -> None:
        plan.task.status = "completed"
        plan.task.completed_at = session.ended_at
        daily.state = "completed"
        plan.ended = True

    def _abandon_task(
        self,
        plan: TaskPlan,
        daily: DailyTask,
        session: WorkSession,
    ) -> None:
        plan.task.status = "cancelled"
        plan.task.completed_at = None
        daily.state = "abandoned"
        plan.ended = True
        session.outcome = "abandoned"

    def _sample_outcome(self, weekday: int, day_part: str) -> str:
        rates = (
            MONDAY_POSITIVE_RATE
            if weekday == 0
            else TUE_FRI_POSITIVE_RATE
        )
        positive = self.rng.random() < rates[day_part]
        if positive:
            if self.rng.random() < PROGRESS_GIVEN_POSITIVE:
                return "progress"
            return "complete"
        return self._weighted_choice(
            dict(NEGATIVE_OUTCOME_WEIGHTS)
        )

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        draw = self.rng.random()
        total = 0.0
        items = list(weights.items())
        for name, weight in items[:-1]:
            total += weight
            if draw < total:
                return name
        return items[-1][0]
