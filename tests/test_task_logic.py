from __future__ import annotations

import unittest
from datetime import datetime

from pydantic import ValidationError

from app.models import RecurrenceTypeEnum, Task
from app.routers.tasks import _next_cycle_boundary, _next_due
from app.schemas import TaskCreate


class TaskLogicTests(unittest.TestCase):
    def test_monthly_requires_due_at(self) -> None:
        with self.assertRaises(ValidationError):
            TaskCreate(
                title="Monatsaufgabe",
                assignee_id=1,
                recurrence_type=RecurrenceTypeEnum.monthly,
                due_at=None,
            )

    def test_weekly_flexible_forces_always_submittable_false(self) -> None:
        model = TaskCreate(
            title="Wochenaufgabe",
            assignee_id=1,
            recurrence_type=RecurrenceTypeEnum.weekly,
            due_at=None,
            always_submittable=True,
        )
        self.assertFalse(model.always_submittable)

    def test_daily_allows_only_short_reminders(self) -> None:
        with self.assertRaises(ValidationError):
            TaskCreate(
                title="Täglich",
                assignee_id=1,
                recurrence_type=RecurrenceTypeEnum.daily,
                due_at=datetime(2026, 3, 16, 18, 0, 0),
                active_weekdays=[0, 1, 2, 3, 4],
                reminder_offsets_minutes=[1440],
            )

    def test_next_due_for_weekly_flexible_stays_none(self) -> None:
        self.assertIsNone(_next_due(None, RecurrenceTypeEnum.weekly.value, None))

    def test_next_cycle_boundary_for_weekly_exact(self) -> None:
        task = Task(
            recurrence_type=RecurrenceTypeEnum.weekly.value,
            due_at=datetime(2026, 3, 16, 9, 0, 0),
            active_weekdays=[],
        )
        boundary = _next_cycle_boundary(task)
        self.assertEqual(boundary, datetime(2026, 3, 23, 9, 0, 0))


if __name__ == "__main__":
    unittest.main()
