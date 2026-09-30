"""Shared test helpers: a fake Google API client and a context factory."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from automation_desk import ledger as ledger_store
from automation_desk.groups.base import Context

TZ = ZoneInfo('Europe/Brussels')
TODAY = date(2026, 9, 22)
NOW = datetime(2026, 9, 22, 21, 0, tzinfo=TZ)


class _Request:
    """What `svc.x().y(...)` returns: `.execute()` runs the handler."""

    def __init__(self, fake: 'FakeGoogle', name: str, kwargs: dict) -> None:
        """Remember the call."""
        self.fake, self.name, self.kwargs = fake, name, kwargs

    def execute(self) -> Any:
        """Record the call and answer it."""
        self.fake.calls.append((self.name, self.kwargs))
        return self.fake.handlers[self.name](**self.kwargs)


class _Collection:
    """What `svc.x()` returns."""

    def __init__(self, fake: 'FakeGoogle', collection: str) -> None:
        """Remember the collection."""
        self.fake, self.collection = fake, collection

    def __getattr__(self, method: str) -> Callable[..., object]:
        """A nested collection when handlers exist below it (gmail users().messages()), otherwise a request."""
        if method.startswith('_') or method == 'execute':
            # CLAUDE> like a real googleapiclient collection: it has no execute(), only its methods do
            raise AttributeError(method)
        name = f'{self.collection}.{method}'
        if any(key.startswith(f'{name}.') for key in self.fake.handlers):
            return lambda **kwargs: _Collection(self.fake, name)
        return lambda **kwargs: _Request(self.fake, name, kwargs)


class FakeGoogle:
    """Stands in for a googleapiclient Resource: handlers keyed 'collection.method', every call recorded."""

    def __init__(self, handlers: dict[str, Callable[..., Any]]) -> None:
        """Handlers keyed by collection.method."""
        self.handlers = handlers
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, collection: str) -> Callable[[], _Collection]:
        """Any collection name, called like svc.tasks()."""
        return lambda: _Collection(self, collection)

    def names(self) -> list[str]:
        """Just the method names called, in order."""
        return [name for name, _ in self.calls]


@pytest.fixture(autouse=True)
def no_real_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would reach OpenRouter for real; tests that mock it with respx are unaffected."""
    import httpx

    real_post = httpx.Client.post

    def guarded(self: httpx.Client, url: object, *args: object, **kwargs: object) -> httpx.Response:
        """Refuse real OpenRouter traffic."""
        if 'openrouter.ai' in str(url) and not isinstance(self._transport, httpx.MockTransport) and not _respx_active():
            raise AssertionError(f'test tried to call OpenRouter for real: {url}')
        return real_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, 'post', guarded)


def _respx_active() -> bool:
    """Whether a respx mock router is currently intercepting requests."""
    import respx

    return bool(respx.mock.routes) or any(router.routes for router in getattr(respx, '_routers', []))


@pytest.fixture(autouse=True)
def fresh_rate_pace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with no recent model requests, so the per-minute spacing never makes the suite wait."""
    from collections import deque

    from automation_desk import llm

    monkeypatch.setattr(llm, '_SENT', deque())


@pytest.fixture(autouse=True)
def captures_in_tmp(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pages read through a stand-in browser are kept in the test's own folder, not the project's data."""
    from automation_desk.groups.calendar import web_page

    monkeypatch.setattr(web_page, 'CAPTURES', tmp_path / 'captures')


@pytest.fixture(autouse=True)
def no_real_chrome_like_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused download never reaches the real site again in tests; tests of the retry put their own stand-in."""
    from automation_desk.groups.calendar import web_page

    monkeypatch.setattr(web_page, 'chrome_like', lambda url: None)


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Keep every test's jobs out of the real ledger."""
    path = tmp_path / 'automation.db'
    monkeypatch.setattr(ledger_store, 'DB', path)
    return path


class FakeTodoist:
    """Stands in for automation_desk.todoist.Todoist: projects and tasks in memory, every change recorded in `calls`."""

    def __init__(self, projects: list[dict], tasks: list[dict], completed: list[dict] | None = None) -> None:
        """Projects as {'id', 'name'}, open tasks and completed tasks as the API gives them."""
        self._projects, self.tasks, self.done = projects, tasks, completed or []
        self.section_names: dict[str, str] = {}
        self.section_projects: dict[str, str] = {}
        self.label_names: list[str] = []
        self.calls: list[tuple] = []

    def labels(self) -> list[dict]:
        """All personal labels, from `label_names`."""
        return [{'id': f'L{n}', 'name': name} for n, name in enumerate(self.label_names)]

    def sections(self) -> list[dict]:
        """All sections, from `section_names` (id → name) and `section_projects` (id → project id)."""
        return [{'id': section_id, 'name': name, 'project_id': self.section_projects.get(section_id, '')}
                for section_id, name in self.section_names.items()]

    def move_to_section(self, task_id: str, section_id: str) -> None:
        """Move a task into a section."""
        self.calls.append(('move_to_section', task_id, section_id))
        for task in self.tasks:
            if task['id'] == task_id:
                task['section_id'] = section_id

    def projects(self) -> list[dict]:
        """All projects."""
        return list(self._projects)

    def open_tasks(self) -> list[dict]:
        """Tasks not done."""
        return list(self.tasks)

    def completed(self, since: object, until: object) -> list[dict]:
        """Completed tasks, whatever the period."""
        self.calls.append(('completed', since, until))
        return list(self.done)

    def task(self, task_id: str) -> dict:
        """One task as it is now."""
        return next(t for t in self.tasks + self.done if t['id'] == task_id)

    def add(self, fields: dict) -> dict:
        """Create a task."""
        self.calls.append(('add', fields))
        return {'id': 'new', **fields}

    def update(self, task_id: str, fields: dict) -> dict:
        """Change a task."""
        self.calls.append(('update', task_id, fields))
        return {}

    def close(self, task_id: str) -> None:
        """Complete a task."""
        self.calls.append(('close', task_id))

    def delete(self, task_id: str) -> None:
        """Delete a task."""
        self.calls.append(('delete', task_id))

    def move(self, task_id: str, project_id: str) -> None:
        """Move a task to the top of a project, out of any section, as Todoist does."""
        self.calls.append(('move', task_id, project_id))
        for task in self.tasks:
            if task['id'] == task_id and not getattr(self, 'moves_nothing', False):
                task.update(project_id=project_id, section_id=None)


@pytest.fixture(autouse=True)
def no_real_todoist(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the user's real Todoist: without a stand-in, the Context finds no token."""
    from automation_desk.groups import base

    monkeypatch.setattr(base, 'todoist_token', lambda: '')


@pytest.fixture
def make_ctx() -> Callable[..., Context]:
    """Build a Context whose Google clients, or Todoist, are the given fake."""
    def build(fake: FakeGoogle | FakeTodoist, sentence: str = '') -> Context:
        """A Context for `sentence` backed by `fake`."""
        if isinstance(fake, FakeTodoist):
            return Context(sentence=sentence, now=NOW, tz=TZ, service=lambda name, version: FakeGoogle({}),
                           todoist_factory=lambda: fake)
        return Context(sentence=sentence, now=NOW, tz=TZ, service=lambda name, version: fake)
    return build
