"""Shared test helpers: a fake Google API client and a context factory."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from llm_automation import ledger as ledger_store
from llm_automation.groups.base import Context

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
def ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Keep every test's jobs out of the real ledger."""
    path = tmp_path / 'automation.db'
    monkeypatch.setattr(ledger_store, 'DB', path)
    return path


@pytest.fixture
def make_ctx() -> Callable[..., Context]:
    """Build a Context whose Google clients are the given fake."""
    def build(fake: FakeGoogle, sentence: str = '') -> Context:
        """A Context for `sentence` backed by `fake`."""
        return Context(sentence=sentence, now=NOW, tz=TZ, service=lambda name, version: fake)
    return build
