"""Todoist, the task app behind the Tasks group: a small client for its API v1, every call recorded on the current job.

Docs: https://developer.todoist.com/api/v1/. Lists are Todoist projects; a task's `due` is its date (with an optional time
and repeat), separate from its `deadline`.
"""

import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from dotenv import dotenv_values

from automation_desk import jobs
from automation_desk.config import ROOT
from automation_desk.jobs import GoogleCall

BASE = 'https://api.todoist.com/api/v1'
# CLAUDE> the completed-tasks call accepts at most three months per request
COMPLETED_WINDOW = timedelta(days=89)
TOKEN_HELP = ('Copy it from Todoist → Settings → Integrations → Developer and put it in the .env file of automation_desk as '
              'TODOIST_API_TOKEN=...')


class TodoistError(RuntimeError):
    """Todoist refused a request or could not be reached; the message says what to do."""


def token() -> str:
    """The Todoist API token, read from .env at every call so a new token works without restarting the app."""
    return dotenv_values(ROOT / '.env').get('TODOIST_API_TOKEN') or os.environ.get('TODOIST_API_TOKEN', '')


class Todoist:
    """The calls the Tasks group needs."""

    def __init__(self, api_token: str, http: httpx.Client | None = None) -> None:
        """A client for one account."""
        if not api_token:
            raise TodoistError(f'No Todoist API token yet. {TOKEN_HELP}')
        self._headers = {'Authorization': f'Bearer {api_token}'}
        self._http = http or httpx.Client(timeout=30.0)

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        """One request, recorded on the current job; a refusal becomes a TodoistError in plain words."""
        started, error = time.monotonic(), ''
        try:
            try:
                response = self._http.request(method, f'{BASE}{path}', headers=self._headers, **kwargs)
            except httpx.HTTPError as failure:
                raise TodoistError(f'Todoist could not be reached ({failure}). Check the internet connection and try '
                                   'again.') from failure
            if response.status_code in (401, 403):
                raise TodoistError(f'Todoist did not accept the API token. {TOKEN_HELP}')
            if response.status_code == 404:
                raise TodoistError('Todoist no longer has that task or project; it was probably deleted in the meantime.')
            if response.status_code >= 400:
                detail = response.json().get('error', response.text[:200]) if response.content else response.reason_phrase
                raise TodoistError(f'Todoist refused the request ({response.status_code}): {detail}')
            return response.json() if response.content else None
        except TodoistError as failure:
            error = str(failure)
            raise
        finally:
            if job := jobs.current():
                job.google_calls.append(GoogleCall(method=f'todoist.{method} {path.split("?")[0]}', params=kwargs.get('params') or {},
                                                   latency_ms=int((time.monotonic() - started) * 1000), error=error,
                                                   stage=jobs.stage()))

    def _all(self, path: str, params: dict | None = None, key: str = 'results') -> list[dict]:
        """Every page of a list call."""
        items, cursor = [], None
        while True:
            page = self._call('GET', path, params={**(params or {}), 'limit': 200, **({'cursor': cursor} if cursor else {})})
            items += page.get(key, [])
            if not (cursor := page.get('next_cursor')):
                return items

    def projects(self) -> list[dict]:
        """The user's projects (the Tasks page calls them lists), Inbox first as Todoist orders them."""
        return self._all('/projects')

    def labels(self) -> list[dict]:
        """The user's personal labels."""
        return self._all('/labels')

    def sections(self) -> list[dict]:
        """All sections of all projects."""
        return self._all('/sections')

    def open_tasks(self) -> list[dict]:
        """Every task not yet done, in all projects."""
        return self._all('/tasks')

    def completed(self, since: datetime, until: datetime) -> list[dict]:
        """Tasks completed in a period, asked three months at a time as the API requires."""
        items, start = [], since.astimezone(UTC)
        end = until.astimezone(UTC)
        while start < end:
            stop = min(start + COMPLETED_WINDOW, end)
            items += self._all('/tasks/completed/by_completion_date', {'since': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                                       'until': stop.strftime('%Y-%m-%dT%H:%M:%SZ')}, key='items')
            start = stop
        return items

    def task(self, task_id: str) -> dict:
        """One task as it is now."""
        return self._call('GET', f'/tasks/{task_id}')

    def add(self, fields: dict) -> dict:
        """Create a task."""
        return self._call('POST', '/tasks', json=fields)

    def update(self, task_id: str, fields: dict) -> dict:
        """Change fields of a task."""
        return self._call('POST', f'/tasks/{task_id}', json=fields)

    def close(self, task_id: str) -> None:
        """Mark a task done; a repeating one moves on to its next date."""
        self._call('POST', f'/tasks/{task_id}/close')

    def delete(self, task_id: str) -> None:
        """Delete a task and its subtasks."""
        self._call('DELETE', f'/tasks/{task_id}')

    def move_to_section(self, task_id: str, section_id: str) -> None:
        """Move a task, with its subtasks, into a section."""
        self._call('POST', f'/tasks/{task_id}/move', json={'section_id': section_id})

    def move(self, task_id: str, project_id: str) -> None:
        """Move a task, with its subtasks, to another project."""
        self._call('POST', f'/tasks/{task_id}/move', json={'project_id': project_id})
