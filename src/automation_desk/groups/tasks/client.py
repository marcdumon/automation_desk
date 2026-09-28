"""Google Tasks API helpers shared by the Tasks group's standard tasks."""

from collections import Counter
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


def task_state(task: dict) -> list:
    """What the user sees of a task, to tell whether they changed it since a preview. Not its etag: moving or deleting a task
    shifts the others in its list, and Google gives each of them a new etag without anything the user would call a change."""
    return [task.get('title', ''), task.get('notes', ''), task.get('due', ''), task.get('status', '')]


REPEATING_NOTE = 'looks like a repeating task: change it in Google Tasks'


def _title_key(task: dict) -> str:
    """A title for counting: case- and space-insensitive."""
    return ' '.join(task.get('title', '').split()).casefold()


def repeating_titles(svc: Resource) -> set[str]:
    """Titles the user completed twice or more, in any list: most likely repeating tasks. The Tasks API does not say which
    tasks repeat, and moving one to another list broke its series (Quick Clean), so previews leave these unticked."""
    done = Counter(_title_key(t) for tasklist in tasklists(svc) for t in tasks_in(svc, tasklist['id'], with_completed=True)
                   if t.get('status') == 'completed' and _title_key(t))
    return {title for title, count in done.items() if count >= 2}


def looks_repeating(task: dict, titles: set[str]) -> bool:
    """Whether an open task is probably an occurrence of a repeating task; its completed copies are harmless to handle."""
    return task.get('status') != 'completed' and _title_key(task) in titles
