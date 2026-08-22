"""Dataset C — task_age_abandonment.

Tasks that stay unresolved longer after first appearing on Today
are progressively more likely to be abandoned. Age is measured from
the first DailyTask date, not Task.created_at.

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
from evaluation.age import AGE_BUCKETS
from evaluation.catalog import (
    DEPENDENCY_CATEGORIES,
    DEPENDENCY_TITLE_STEMS,
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
from evaluation.result import GenerationResult


SCENARIO_NAME = "task_age_abandonment"
DEFAULT_SEED = 20250901
START_DATE = date(2025, 9, 1)
WORKING_WEEKS = 24
INTERRUPTION_RATE = 0.16
INTERMEDIATE_OUTCOME_WEIGHTS = (
    ("progress", 0.62),
    ("stuck", 0.22),
    ("paused", 0.16),
)
ABANDON_RATE_BY_BUCKET = {
    "0-2": 0.07,
    "3-7": 0.10,
    "8-14": 0.175,
    "15-30": 0.33,
    "31+": 0.55,
}
BUCKET_QUOTAS = {
    "0-2": 38,
    "3-7": 42,
    "8-14": 42,
    "15-30": 38,
    "31+": 32,
}


@dataclass
class LeafSpec:
    category: str
    first_date: date
    last_date: date
    work_dates: list[date]
    fate: str
    backlog_days: int
    parent_key: int | None = None


@dataclass
class TaskPlan:
    spec: LeafSpec
    task: Task
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


def generate_task_age_abandonment(
    db: Session,
    *,
    seed: int = DEFAULT_SEED,
    timezone_name: str,
) -> GenerationResult:
    rng = Random(seed)
    zone = ZoneInfo(timezone_name)
    days = working_days(START_DATE, WORKING_WEEKS)
    generator = _TaskAgeAbandonmentGenerator(
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


class _TaskAgeAbandonmentGenerator:
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
        self.by_date: dict[date, list[TaskPlan]] = {
            day: [] for day in days
        }
        self.sort_order = 0
        self.category_bag: list[str] = []
        self.stem_index = {
            category: 0 for category in DEPENDENCY_CATEGORIES
        }
        self.used_titles: set[str] = set()
        self.parent_ids: list[int] = []

    def run(self) -> None:
        self._create_parents()
        specs = self._plan_leaves()
        for spec in specs:
            plan = self._create_leaf(spec)
            for work_date in spec.work_dates:
                self.by_date[work_date].append(plan)

        for day in self.days:
            scheduled = self.by_date[day]
            if not scheduled:
                continue
            self._generate_day(day, scheduled)

        self.db.flush()

    def _create_parents(self) -> None:
        for _ in range(12):
            created_on = self.rng.choice(self.days[:40])
            task = self._make_task(
                category=self._next_category(),
                created_on=created_on,
                backlog_days=self._sample_backlog_days(),
                parent_id=None,
            )
            self.parent_ids.append(task.id)

    def _plan_leaves(self) -> list[LeafSpec]:
        specs: list[LeafSpec] = []
        for bucket, quota in BUCKET_QUOTAS.items():
            bucket_specs: list[LeafSpec] = []
            for _ in range(quota):
                spec = self._plan_one_leaf(bucket)
                if spec is not None:
                    bucket_specs.append(spec)
            self._assign_bucket_fates(bucket, bucket_specs)
            specs.extend(bucket_specs)
        self.rng.shuffle(specs)
        return specs

    def _assign_bucket_fates(
        self,
        bucket: str,
        bucket_specs: list[LeafSpec],
    ) -> None:
        if not bucket_specs:
            return
        target = ABANDON_RATE_BY_BUCKET[bucket]
        abandon_n = round(len(bucket_specs) * target)
        abandon_n = min(abandon_n, len(bucket_specs) - 1)
        if target > 0:
            abandon_n = max(abandon_n, 1)
        self.rng.shuffle(bucket_specs)
        for index, spec in enumerate(bucket_specs):
            spec.fate = "abandoned" if index < abandon_n else "complete"

    def _plan_one_leaf(self, bucket: str) -> LeafSpec | None:
        first, last = self._pick_span(bucket)
        if first is None or last is None:
            return None
        work_dates = self._sample_work_dates(bucket, first, last)
        parent_key = None
        if self.parent_ids and self.rng.random() < 0.18:
            parent_key = self.rng.choice(self.parent_ids)
        return LeafSpec(
            category=self._next_category(),
            first_date=first,
            last_date=last,
            work_dates=work_dates,
            fate="complete",
            backlog_days=self._sample_backlog_days(),
            parent_key=parent_key,
        )

    def _pick_span(
        self,
        bucket: str,
    ) -> tuple[date | None, date | None]:
        low, high = {
            name: (lo, hi) for name, lo, hi in AGE_BUCKETS
        }[bucket]
        if high is None:
            high = 52
        first_pool = self.days
        if bucket == "31+":
            cutoff = self.days[int(len(self.days) * 0.62)]
            first_pool = [day for day in self.days if day <= cutoff]
        if not first_pool:
            return None, None
        for _ in range(80):
            first = self.rng.choice(first_pool)
            last_weekday = self.rng.randint(0, 4)
            candidates = [
                last
                for last in self.days
                if last.weekday() == last_weekday
                and low <= (last - first).days <= high
            ]
            if candidates:
                return first, self.rng.choice(candidates)
        fallback = [
            (first, last)
            for first in first_pool
            for last in self.days
            if low <= (last - first).days <= high
        ]
        if not fallback:
            return None, None
        return self.rng.choice(fallback)

    def _sample_work_dates(
        self,
        bucket: str,
        first: date,
        last: date,
    ) -> list[date]:
        if first == last:
            return [first]
        interior = [day for day in self.days if first < day < last]
        extras_by_bucket = {
            "0-2": (0, 1),
            "3-7": (1, 2),
            "8-14": (2, 4),
            "15-30": (3, 6),
            "31+": (4, 8),
        }
        low_k, high_k = extras_by_bucket[bucket]
        if not interior:
            return [first, last]
        k = self.rng.randint(low_k, high_k)
        k = min(k, len(interior))
        extra = self.rng.sample(interior, k) if k else []
        return sorted({first, last, *extra})

    def _sample_backlog_days(self) -> int:
        roll = self.rng.random()
        if roll < 0.22:
            return 0
        if roll < 0.62:
            return self.rng.randint(1, 7)
        if roll < 0.90:
            return self.rng.randint(8, 30)
        return self.rng.randint(31, 55)

    def _next_category(self) -> str:
        if not self.category_bag:
            self.category_bag = list(DEPENDENCY_CATEGORIES)
            self.rng.shuffle(self.category_bag)
        return self.category_bag.pop()

    def _next_title(self, category: str) -> str:
        stems = DEPENDENCY_TITLE_STEMS[category]
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

    def _make_task(
        self,
        *,
        category: str,
        created_on: date,
        backlog_days: int,
        parent_id: int | None,
    ) -> Task:
        created_date = created_on - timedelta(days=backlog_days)
        created_at = local_to_utc(
            combine_local(
                created_date,
                time(
                    self.rng.randint(7, 11),
                    self.rng.randint(0, 59),
                    self.rng.randint(0, 59),
                ),
                self.zone,
            )
        )
        due_date = None
        if self.rng.random() < 0.24:
            due_date = created_on + timedelta(
                days=self.rng.randint(5, 28)
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
        return task

    def _create_leaf(self, spec: LeafSpec) -> TaskPlan:
        task = self._make_task(
            category=spec.category,
            created_on=spec.first_date,
            backlog_days=spec.backlog_days,
            parent_id=spec.parent_key,
        )
        plan = TaskPlan(spec=spec, task=task)
        self.plans[task.id] = plan
        return plan

    def _generate_day(
        self,
        day: date,
        scheduled: list[TaskPlan],
    ) -> None:
        required = [
            plan
            for plan in scheduled
            if day in {plan.spec.first_date, plan.spec.last_date}
        ]
        optional = [
            plan for plan in scheduled if plan not in required
        ]
        self.rng.shuffle(optional)

        quiet = self.rng.random() < 0.04
        n_sessions = (
            self.rng.randint(3, 5)
            if quiet
            else self.rng.randint(8, 12)
        )
        capacity = min(7, max(len(required), n_sessions))
        chosen = list(required)
        for plan in optional:
            if len(chosen) >= capacity:
                break
            chosen.append(plan)
        if len(chosen) > n_sessions:
            n_sessions = len(chosen)

        daily_rows: list[DailyTask] = []
        for index, plan in enumerate(chosen):
            created_at = local_to_utc(
                combine_local(
                    day,
                    time(5, 20 + index, self.rng.randint(0, 59)),
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
        session_plans = self._plan_session_times(day, n_sessions)
        holders = self._assign_sessions(daily_rows, session_plans)

        for daily, session_plan in holders:
            session_plan.interrupted = (
                self.rng.random() < INTERRUPTION_RATE
            )
            session_plan.outcome = self._weighted_choice(
                dict(INTERMEDIATE_OUTCOME_WEIGHTS)
            )
            self._insert_session(daily, session_plan)

        self.db.flush()
        self._apply_terminals(day, chosen, daily_rows)

    def _plan_session_times(
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
                slots[index] = SessionPlan(
                    day_part=part,
                    started_at_local=started_local,
                    ended_at_local=started_local
                    + timedelta(seconds=actual),
                    planned_duration_seconds=planned,
                    actual_duration_seconds=actual,
                    outcome="progress",
                    interrupted=False,
                )

        planned_sessions = [slot for slot in slots if slot is not None]
        planned_sessions.sort(key=lambda item: item.started_at_local)
        self._resolve_overlaps(planned_sessions, day)
        for session in planned_sessions:
            session.day_part = classify_day_part(
                session.started_at_local
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
                + timedelta(seconds=session.actual_duration_seconds)
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

    def _apply_terminals(
        self,
        day: date,
        chosen: list[TaskPlan],
        daily_rows: list[DailyTask],
    ) -> None:
        daily_by_task = {
            daily.task_id: daily for daily in daily_rows
        }
        for plan in chosen:
            if plan.spec.last_date != day or plan.ended:
                continue
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
                if session.outcome in {"complete", "abandoned"}:
                    session.outcome = "progress"
            if plan.spec.fate == "complete":
                last.outcome = "complete"
                plan.task.status = "completed"
                plan.task.completed_at = last.ended_at
                daily.state = "completed"
            else:
                last.outcome = "abandoned"
                plan.task.status = "cancelled"
                plan.task.completed_at = None
                daily.state = "abandoned"
            plan.ended = True

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        draw = self.rng.random()
        total = 0.0
        items = list(weights.items())
        for name, weight in items[:-1]:
            total += weight
            if draw < total:
                return name
        return items[-1][0]
