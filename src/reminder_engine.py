from __future__ import annotations

from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from src.assistant_models import RecurrenceFrequency, Reminder, ReminderStatus


def find_due_reminders(reminders: list[Reminder], now: datetime | None = None) -> list[Reminder]:
    current_time = now or datetime.utcnow()
    return [
        reminder
        for reminder in reminders
        if reminder.status == ReminderStatus.pending and reminder.remind_at <= current_time
    ]


def advance_datetime(
    value: datetime,
    frequency: RecurrenceFrequency | None,
    interval: int = 1,
) -> datetime | None:
    if frequency is None:
        return None
    safe_interval = max(interval, 1)
    if frequency == RecurrenceFrequency.daily:
        return value + timedelta(days=safe_interval)
    if frequency == RecurrenceFrequency.weekly:
        return value + timedelta(weeks=safe_interval)
    if frequency == RecurrenceFrequency.monthly:
        return value + relativedelta(months=safe_interval)
    if frequency == RecurrenceFrequency.yearly:
        return value + relativedelta(years=safe_interval)
    return None
