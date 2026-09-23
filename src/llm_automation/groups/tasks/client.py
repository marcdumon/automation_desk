"""Google Tasks API helpers shared by the Tasks group's standard tasks."""

from datetime import date

from googleapiclient.discovery import Resource


def tasklists(svc: Resource) -> list[dict]:
    """All task lists of the user."""
    items, token = [], None
    while True:
        page = svc.tasklists().list(maxResults=100, pageToken=token).execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items


def tasks_in(svc: Resource, list_id: str, with_completed: bool) -> list[dict]:
    """Tasks of one list; completed and hidden ones only when asked."""
    items, token = [], None
    while True:
        page = svc.tasks().list(tasklist=list_id, maxResults=100, pageToken=token, showCompleted=with_completed,
                                showHidden=with_completed).execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items


def due_date(task: dict) -> date | None:
    """The due date of a task. Google Tasks keeps only the date part."""
    return date.fromisoformat(task['due'][:10]) if task.get('due') else None


def due_value(day: date) -> str:
    """A date in the RFC 3339 form the Tasks API expects for `due`."""
    return f'{day.isoformat()}T00:00:00.000Z'
