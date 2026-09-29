"""Todoist task helpers shared by the Tasks group's standard tasks: dates, repeats and what the user sees of a task."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from automation_desk.dates import label


def task_date(task: dict) -> date | None:
    """The day a task is planned for (Todoist's 'date', not its deadline); None when undated."""
    due = task.get('due')
    return date.fromisoformat(due['date'][:10]) if due else None


def task_time(task: dict) -> time | None:
    """The time of day a task is planned for, as the user sees it; None when it has only a date."""
    due = task.get('due')
    if not due or len(due['date']) <= 10:
        return None
    return datetime.fromisoformat(due['date'].removesuffix('Z')).time()


def when(task: dict) -> str:
    """A task's date and time as previews show them: 'Fri 25 Sep 2026 10:30', or '—'."""
    day, clock = task_date(task), task_time(task)
    return f'{label(day)} {clock.strftime("%H:%M")}' if day and clock else label(day) if day else '—'


def repeat_rule(task: dict) -> str:
    """How a task repeats, in Todoist's words ('every saturday'); empty when it does not."""
    due = task.get('due') or {}
    return due.get('string', '') if due.get('is_recurring') else ''


def utc_moment(day: date, clock: time, tz: ZoneInfo) -> str:
    """A local date and time as the UTC timestamp Todoist's due_datetime takes."""
    return datetime.combine(day, clock, tz).astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def task_state(task: dict) -> list:
    """What the user sees of a task, to tell whether they changed it since a preview."""
    return [task.get('content', ''), task.get('description', ''), (task.get('due') or {}).get('date'), task.get('checked', False)]
