"""Local-time daypart classification for analytics.

These boundaries match the evaluation scenario buckets,
with one production difference: sessions before 06:00
are kept in a documented ``overnight`` bucket instead of
being folded into evening or dropped.

Half-open local-time intervals:

    overnight       00:00–05:59
    early_morning   06:00–08:59
    morning         09:00–11:59
    early_afternoon 12:00–14:59
    late_afternoon  15:00–17:59
    evening         18:00 onward

The Python classifier and the SQL CASE expression are
both derived from ``DAYPARTS`` so they cannot drift.
Callers must pass a local wall-clock datetime; this
helper does not convert timezones.
"""

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import Time, case, cast
from sqlalchemy.sql.elements import ColumnElement


@dataclass(frozen=True)
class DaypartSpec:
    name: str
    start: time
    end: time | None


DAYPARTS: tuple[DaypartSpec, ...] = (
    DaypartSpec("overnight", time(0, 0), time(6, 0)),
    DaypartSpec("early_morning", time(6, 0), time(9, 0)),
    DaypartSpec("morning", time(9, 0), time(12, 0)),
    DaypartSpec("early_afternoon", time(12, 0), time(15, 0)),
    DaypartSpec("late_afternoon", time(15, 0), time(18, 0)),
    DaypartSpec("evening", time(18, 0), None),
)
DAYPART_NAMES = tuple(spec.name for spec in DAYPARTS)
DAYPART_RANK = {
    name: index for index, name in enumerate(DAYPART_NAMES)
}


def classify_daypart(local_dt: datetime) -> str:
    """Classify a local wall-clock datetime into a daypart."""
    local_time = local_dt.timetz().replace(tzinfo=None)
    for spec in DAYPARTS:
        if spec.end is not None and local_time < spec.end:
            return spec.name
    return DAYPARTS[-1].name


def daypart_sql(
    local_started: ColumnElement,
) -> ColumnElement:
    """SQL CASE classifying a local timestamp into a daypart."""
    local_time = cast(local_started, Time)
    clauses = [
        (local_time < spec.end, spec.name)
        for spec in DAYPARTS
        if spec.end is not None
    ]
    return case(*clauses, else_=DAYPARTS[-1].name)


def daypart_rank_sql(
    daypart: ColumnElement,
) -> ColumnElement:
    """Stable calendar order for daypart groups."""
    return case(
        DAYPART_RANK,
        value=daypart,
    )
