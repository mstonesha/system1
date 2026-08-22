"""Dataset D — planning_workload.

The user tends to commit more daily sessions than are actually
undertaken, mildly underestimates Task effort, and has poorer
session outcomes in unusually heavy planned weeks. Early completion
must not be treated as the same as unused deferred capacity.

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
    DEPENDENCY_CATEGORIES,
    DEPENDENCY_TITLE_STEMS,
    format_task_title,
    parse_task_category,
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


SCENARIO_NAME = "planning_workload"
DEFAULT_SEED = 20260302
START_DATE = date(2026, 3, 2)
WORKING_WEEKS = 26
INTERRUPTION_RATE = 0.16
PARENT_COUNT = 10
MAX_LEAVES = 250
MAX_PLANNED_SESSIONS = 5
NORMAL_PLANNED_RANGE = (48, 60)
HEAVY_PLANNED_RANGE = (78, 92)
NORMAL_POSITIVE_RATE = 0.68
HEAVY_POSITIVE_RATE = 0.40
# 1-indexed scenario weeks. Scattered so workload is not a
# calendar-time trend. Counts are balanced across the two halves.
HEAVY_WEEKS = frozenset({5, 6, 12, 13, 18, 19, 23, 24})
HEAVY_WEEK_STYLES = {
    5: "many_small",
    6: "many_small",
    12: "few_fat",
    13: "few_fat",
    18: "many_small",
    19: "few_fat",
    23: "many_small",
    24: "few_fat",
}


@dataclass
class TaskPlan:
    task: Task
    category: str
    is_parent: bool
    estimated_sessions: int
    needed_sessions: int
    max_appearances: int
    sessions_done: int = 0
    appearances_used: int = 0
    ended: bool = False


@dataclass
class DaySlot:
    plan: TaskPlan
    planned_sessions: int
    actual_sessions: int
    fate: str


@dataclass
class SessionPlan:
    day_part: str
    started_at_local: datetime
    ended_at_local: datetime
    planned_duration_seconds: int
    actual_duration_seconds: int
    outcome: str
    interrupted: bool


def generate_planning_workload(
    db: Session,
    *,
    seed: int = DEFAULT_SEED,
    timezone_name: str,
) -> GenerationResult:
    rng = Random(seed)
    zone = ZoneInfo(timezone_name)
    days = working_days(START_DATE, WORKING_WEEKS)
    generator = _PlanningWorkloadGenerator(
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


class _PlanningWorkloadGenerator:
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
        self.stem_index = {
            category: 0 for category in DEPENDENCY_CATEGORIES
        }
        self.used_titles: set[str] = set()
        self.parent_ids: list[int] = []
        self.week_positive: dict[int, float] = {}

    def run(self) -> None:
        self._create_parents()
        for _ in range(22):
            self._spawn_leaf(created_on=START_DATE)

        weeks = [
            self.days[index:index + 5]
            for index in range(0, len(self.days), 5)
        ]
        for week_index, week_days in enumerate(weeks, start=1):
            self._generate_week(week_index, week_days)

        self.db.flush()

    def _create_parents(self) -> None:
        for _ in range(PARENT_COUNT):
            created_on = START_DATE - timedelta(
                days=self.rng.randint(1, 14)
            )
            task = self._make_task(
                category=self._next_category(),
                created_on=created_on,
                estimated_sessions=self.rng.choice([6, 8, 10]),
                parent_id=None,
            )
            self.parent_ids.append(task.id)
            self.plans[task.id] = TaskPlan(
                task=task,
                category=parse_task_category(task.title) or "Admin",
                is_parent=True,
                estimated_sessions=task.estimated_sessions or 8,
                needed_sessions=99,
                max_appearances=0,
                ended=True,
            )

    def _generate_week(
        self,
        week_index: int,
        week_days: list[date],
    ) -> None:
        is_heavy = week_index in HEAVY_WEEKS
        style = HEAVY_WEEK_STYLES.get(week_index, "normal")
        if is_heavy:
            planned_target = self.rng.randint(*HEAVY_PLANNED_RANGE)
        else:
            planned_target = self.rng.randint(*NORMAL_PLANNED_RANGE)
        self.week_positive[week_index] = self._week_positive_rate(
            is_heavy
        )
        self._refill_pool(week_days[0], style, week_index=week_index)
        planned_values = self._make_planned_values(
            planned_target,
            style,
        )
        day_buckets = self._distribute_to_days(
            planned_values,
            week_days,
        )
        for day, planned_list in zip(week_days, day_buckets):
            self._generate_day(
                day,
                planned_list,
                style=style,
                is_heavy=is_heavy,
                positive_rate=self.week_positive[week_index],
            )

    def _week_positive_rate(self, is_heavy: bool) -> float:
        noise = self.rng.uniform(-0.03, 0.03)
        if is_heavy:
            rate = HEAVY_POSITIVE_RATE + noise
            if self.rng.random() < 0.15:
                rate = 0.46 + self.rng.uniform(-0.02, 0.02)
            return min(0.48, max(0.38, rate))
        rate = NORMAL_POSITIVE_RATE + noise
        if self.rng.random() < 0.12:
            rate = 0.63 + self.rng.uniform(-0.02, 0.02)
        return min(0.74, max(0.62, rate))

    def _make_planned_values(
        self,
        target: int,
        style: str,
    ) -> list[int]:
        values: list[int] = []
        if style == "many_small":
            choices = (1, 1, 1, 1, 1, 2)
        elif style == "few_fat":
            choices = (3, 3, 4, 4, 5)
        else:
            choices = (1, 2, 2, 2, 3, 3)
        while sum(values) < target:
            values.append(self.rng.choice(choices))
        self._trim_planned_values(values, target)
        while sum(values) < target:
            increasable = [
                index
                for index, value in enumerate(values)
                if value < MAX_PLANNED_SESSIONS
            ]
            if style == "many_small":
                values.append(1)
            elif style == "few_fat":
                if increasable:
                    values[self.rng.choice(increasable)] += 1
                else:
                    values.append(self.rng.choice([3, 4, 5]))
            elif increasable:
                values[self.rng.choice(increasable)] += 1
            else:
                values.append(self.rng.choice([1, 2]))
            self._trim_planned_values(values, target)
        self.rng.shuffle(values)
        return values

    def _trim_planned_values(
        self,
        values: list[int],
        target: int,
    ) -> None:
        while sum(values) > target and values:
            richest = max(range(len(values)), key=values.__getitem__)
            if values[richest] > 1:
                values[richest] -= 1
            elif len(values) > 1:
                values.pop(richest)
            else:
                break

    def _distribute_to_days(
        self,
        values: list[int],
        week_days: list[date],
    ) -> list[list[int]]:
        buckets: list[list[int]] = [[] for _ in week_days]
        for index, value in enumerate(values):
            buckets[index % len(week_days)].append(value)
        for bucket in buckets:
            self.rng.shuffle(bucket)
        return buckets

    def _generate_day(
        self,
        day: date,
        planned_list: list[int],
        *,
        style: str,
        is_heavy: bool,
        positive_rate: float,
    ) -> None:
        if not planned_list:
            return
        used: set[int] = set()
        self._ensure_unused_leaves(day, len(planned_list))
        slots: list[DaySlot] = []
        for planned in planned_list:
            planned = min(MAX_PLANNED_SESSIONS, max(1, planned))
            plan = self._pick_leaf(day, used)
            used.add(plan.task.id)
            actual, fate = self._decide_execution(
                plan,
                planned,
                style=style,
                is_heavy=is_heavy,
            )
            plan.sessions_done += actual
            plan.appearances_used += 1
            if fate in {"complete", "abandon"}:
                plan.ended = True
            slots.append(
                DaySlot(
                    plan=plan,
                    planned_sessions=planned,
                    actual_sessions=actual,
                    fate=fate,
                )
            )

        daily_rows: list[DailyTask] = []
        for index, slot in enumerate(slots):
            created_at = local_to_utc(
                combine_local(
                    day,
                    time(5, 15 + index, self.rng.randint(0, 59)),
                    self.zone,
                )
            )
            daily = DailyTask(
                task_id=slot.plan.task.id,
                date=day,
                planned_sessions=slot.planned_sessions,
                state="planned",
                sort_order=index,
                created_at=created_at,
            )
            self.db.add(daily)
            daily_rows.append(daily)
        self.db.flush()

        n_sessions = sum(slot.actual_sessions for slot in slots)
        if n_sessions:
            session_plans = self._plan_session_times(day, n_sessions)
            holders = self._assign_counted(
                slots,
                daily_rows,
                session_plans,
            )
            for daily, session_plan, slot, is_last in holders:
                session_plan.interrupted = (
                    self.rng.random() < INTERRUPTION_RATE
                )
                session_plan.outcome = self._session_outcome(
                    slot=slot,
                    is_last=is_last,
                    positive_rate=positive_rate,
                )
                self._insert_session(daily, session_plan)
            self.db.flush()

        self._apply_terminals(slots, daily_rows)

    def _refill_pool(
        self,
        day: date,
        style: str,
        *,
        week_index: int,
    ) -> None:
        if style == "many_small":
            target = 28
        elif style == "few_fat":
            target = 14
        else:
            target = 18
        spawned = 0
        while (
            self._active_leaf_count() < target
            and self._leaf_count() < MAX_LEAVES
            and spawned < 16
        ):
            self._spawn_leaf(created_on=day)
            spawned += 1

    def _active_leaf_count(self) -> int:
        return sum(
            1
            for plan in self.plans.values()
            if not plan.is_parent and not plan.ended
        )

    def _ensure_unused_leaves(self, day: date, needed: int) -> None:
        unused = self._unused_leaves(set())
        spawned = 0
        while len(unused) < needed and spawned < needed + 4:
            self._spawn_leaf(created_on=day)
            unused = self._unused_leaves(set())
            spawned += 1

    def _unused_leaves(self, used: set[int]) -> list[TaskPlan]:
        return [
            plan
            for plan in self.plans.values()
            if not plan.is_parent
            and not plan.ended
            and plan.task.id not in used
        ]

    def _pick_leaf(self, day: date, used: set[int]) -> TaskPlan:
        candidates = self._unused_leaves(used)
        if not candidates:
            self._spawn_leaf(created_on=day)
            candidates = self._unused_leaves(used)
        return self.rng.choice(candidates)

    def _leaf_count(self) -> int:
        return sum(
            1 for plan in self.plans.values() if not plan.is_parent
        )

    def _decide_execution(
        self,
        plan: TaskPlan,
        planned: int,
        *,
        style: str,
        is_heavy: bool,
    ) -> tuple[int, str]:
        needed_left = max(1, plan.needed_sessions - plan.sessions_done)
        last_appearance = (
            plan.appearances_used + 1 >= plan.max_appearances
        )
        if last_appearance:
            return max(1, needed_left), "complete"

        if plan.appearances_used >= 1 and self.rng.random() < 0.025:
            actual = max(1, min(needed_left, max(1, planned - 1)))
            return actual, "abandon"

        a_cut, b_cut, c_cut = self._case_cuts(style, is_heavy)
        roll = self.rng.random()

        if roll < a_cut and needed_left > 1:
            if planned >= 3:
                actual = self.rng.choice([0, 1, 1, 2])
            elif planned == 2:
                actual = self.rng.choice([0, 1, 1])
            else:
                actual = 0
            actual = min(actual, needed_left - 1)
            return actual, "continue"

        if roll < b_cut and needed_left < planned:
            return needed_left, "complete"

        if roll < c_cut:
            extra = self.rng.randint(1, 2)
            actual = planned + extra
            if actual >= needed_left:
                return needed_left, "complete"
            return actual, "continue"

        actual = min(planned, needed_left)
        if actual >= needed_left:
            return actual, "complete"
        return actual, "continue"

    def _case_cuts(
        self,
        style: str,
        is_heavy: bool,
    ) -> tuple[float, float, float]:
        if is_heavy and style == "many_small":
            return 0.55, 0.72, 0.80
        if is_heavy and style == "few_fat":
            return 0.30, 0.46, 0.58
        return 0.38, 0.62, 0.74

    def _session_outcome(
        self,
        *,
        slot: DaySlot,
        is_last: bool,
        positive_rate: float,
    ) -> str:
        if is_last and slot.fate == "complete":
            return "complete"
        if is_last and slot.fate == "abandon":
            return "abandoned"
        if self.rng.random() < positive_rate:
            return "progress"
        if self.rng.random() < 0.55:
            return "stuck"
        return "paused"

    def _assign_counted(
        self,
        slots: list[DaySlot],
        daily_rows: list[DailyTask],
        session_plans: list[SessionPlan],
    ) -> list[tuple[DailyTask, SessionPlan, DaySlot, bool]]:
        holders: list[
            tuple[DailyTask, SessionPlan, DaySlot, bool]
        ] = []
        index = 0
        order = list(range(len(slots)))
        self.rng.shuffle(order)
        for slot_index in order:
            slot = slots[slot_index]
            daily = daily_rows[slot_index]
            n = slot.actual_sessions
            for offset in range(n):
                holders.append(
                    (
                        daily,
                        session_plans[index],
                        slot,
                        offset == n - 1,
                    )
                )
                index += 1
        holders.sort(key=lambda item: item[1].started_at_local)
        return holders

    def _apply_terminals(
        self,
        slots: list[DaySlot],
        daily_rows: list[DailyTask],
    ) -> None:
        for slot, daily in zip(slots, daily_rows):
            if slot.fate not in {"complete", "abandon"}:
                continue
            sessions = (
                self.db.query(WorkSession)
                .filter(WorkSession.daily_task_id == daily.id)
                .order_by(WorkSession.started_at)
                .all()
            )
            last = sessions[-1] if sessions else None
            if slot.fate == "complete":
                slot.plan.task.status = "completed"
                slot.plan.task.completed_at = (
                    last.ended_at if last is not None else None
                )
                daily.state = "completed"
            else:
                if last is not None:
                    last.outcome = "abandoned"
                slot.plan.task.status = "cancelled"
                slot.plan.task.completed_at = None
                daily.state = "abandoned"

    def _spawn_leaf(self, created_on: date) -> TaskPlan:
        estimated = self.rng.choice([2, 3, 3, 4, 4, 5, 6, 8])
        roll = self.rng.random()
        if roll < 0.18:
            multiplier = self.rng.uniform(0.55, 0.90)
        elif roll < 0.40:
            multiplier = self.rng.uniform(0.95, 1.08)
        else:
            multiplier = self.rng.uniform(1.18, 1.55)
        needed = max(1, round(estimated * multiplier))
        appearances = max(
            7,
            needed // 2 + self.rng.randint(3, 5),
        )
        parent_id = None
        if self.parent_ids and self.rng.random() < 0.16:
            parent_id = self.rng.choice(self.parent_ids)
        backlog = self.rng.choice(
            [0, 0, 1, 2, 3, 5, 8, 12]
        )
        task = self._make_task(
            category=self._next_category(),
            created_on=created_on,
            estimated_sessions=estimated,
            parent_id=parent_id,
            backlog_days=backlog,
        )
        plan = TaskPlan(
            task=task,
            category=parse_task_category(task.title) or "Admin",
            is_parent=False,
            estimated_sessions=estimated,
            needed_sessions=needed,
            max_appearances=appearances,
        )
        self.plans[task.id] = plan
        return plan

    def _make_task(
        self,
        *,
        category: str,
        created_on: date,
        estimated_sessions: int,
        parent_id: int | None,
        backlog_days: int = 0,
    ) -> Task:
        created_date = created_on - timedelta(days=backlog_days)
        created_at = local_to_utc(
            combine_local(
                created_date,
                time(
                    self.rng.randint(7, 18),
                    self.rng.randint(0, 59),
                    self.rng.randint(0, 59),
                ),
                self.zone,
            )
        )
        due_date = None
        if self.rng.random() < 0.22:
            due_date = created_on + timedelta(
                days=self.rng.randint(4, 21)
            )
        task = Task(
            title=self._next_title(category),
            description=None,
            status="active",
            priority=self.rng.randint(1, 5),
            estimated_sessions=estimated_sessions,
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
                if n_sessions >= 16:
                    minutes = self.rng.choice([12, 15, 15, 18])
                elif n_sessions >= 12:
                    minutes = self.rng.choice([15, 18, 20, 20])
                else:
                    minutes = self.rng.choice([20, 25, 25, 30])
                planned = minutes * 60
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

        planned_sessions = [
            slot for slot in slots if slot is not None
        ]
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
                    minutes=self.rng.randint(2, 6)
                )
            if session.started_at_local > latest_start:
                session.started_at_local = latest_start
            session.ended_at_local = (
                session.started_at_local
                + timedelta(seconds=session.actual_duration_seconds)
            )
            previous_end = session.ended_at_local

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

    def _weighted_choice(self, weights: dict[str, float]) -> str:
        draw = self.rng.random()
        total = 0.0
        items = list(weights.items())
        for name, weight in items[:-1]:
            total += weight
            if draw < total:
                return name
        return items[-1][0]
