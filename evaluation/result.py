"""Shared generation summary for evaluation scenarios."""

from dataclasses import dataclass
from datetime import date


@dataclass
class GenerationResult:
    scenario: str
    seed: int
    start_date: date
    end_date: date
    task_count: int
    daily_task_count: int
    work_session_count: int
