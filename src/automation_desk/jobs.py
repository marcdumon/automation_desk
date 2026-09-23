"""The job ledger: every command run is a job, and everything a job does is recorded.

A job is one command: its preview and, when the user confirms, its apply. It records each model call (model asked for,
model and provider that answered, tokens, exact cost reported by OpenRouter, latency, exact request and response), each
Google API call and each web page read, every one labelled with the step ('preview' or 'apply') that made it.
Jobs are stored in the SQLite ledger (ledger.py, data/automation.db inside the project); saving a job again replaces it.
"""

import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from automation_desk import ledger

# CLAUDE> the group of model calls made outside the app (scripts, checks), so the ledger matches OpenRouter's bill
OUTSIDE_APP = 'outside the app'
_write_lock = threading.Lock()


def _now() -> str:
    """Local time with offset, to the second."""
    return datetime.now().astimezone().isoformat(timespec='seconds')


@dataclass
class LLMCall:
    """One request to OpenRouter."""

    purpose: str
    model_requested: str
    model_used: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    finish_reason: str
    generation_id: str
    system: str
    user: str
    reply: str
    error: str = ''
    # CLAUDE> the exact JSON body sent to OpenRouter (the API key travels in a header, never here) and the raw answer
    request: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)
    stage: str = 'preview'


@dataclass
class GoogleCall:
    """One Google API request."""

    method: str
    params: dict
    latency_ms: int
    error: str = ''
    stage: str = 'preview'


@dataclass
class Fetch:
    """One web page or file downloaded."""

    url: str
    status: int
    bytes: int
    latency_ms: int
    via: str = 'download'
    stage: str = 'preview'


@dataclass
class Job:
    """One command: its preview and, if the user confirmed it, its apply, with everything both steps did."""

    group: str
    sentence: str
    id: str = field(default_factory=lambda: secrets.token_hex(6))
    task_id: str = ''
    task_name: str = ''
    started: str = field(default_factory=_now)
    status: str = 'running'
    message: str = ''
    preview_ms: int = 0
    applied_at: str = ''
    apply_status: str = ''
    apply_message: str = ''
    apply_ms: int = 0
    results: list[str] = field(default_factory=list)
    # CLAUDE> what the preview showed (answer, rows, notes) and which rows were applied, to look back at later
    preview: dict = field(default_factory=dict)
    applied_rows: list[str] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    google_calls: list[GoogleCall] = field(default_factory=list)
    fetches: list[Fetch] = field(default_factory=list)

    def summary(self) -> dict:
        """The job without prompts, replies and call parameters: what lists and cost lines show."""
        running = self.status == 'running'
        preview_ms = self.preview_ms
        return {
            'id': self.id, 'group': self.group, 'sentence': self.sentence, 'task_id': self.task_id,
            'task_name': self.task_name, 'started': self.started, 'status': 'ok' if running else self.status,
            'message': self.message, 'applied_at': self.applied_at, 'apply_status': self.apply_status,
            'apply_message': self.apply_message, 'results': len(self.results),
            'preview_ms': preview_ms, 'apply_ms': self.apply_ms, 'duration_ms': preview_ms + self.apply_ms,
            'models': sorted({c.model_used or c.model_requested for c in self.llm_calls}),
            'llm_calls': len(self.llm_calls),
            'prompt_tokens': sum(c.prompt_tokens for c in self.llm_calls),
            'completion_tokens': sum(c.completion_tokens for c in self.llm_calls),
            'cost_usd': round(sum(c.cost_usd for c in self.llm_calls), 8),
            'google_calls': len(self.google_calls),
            'fetches': len(self.fetches),
        }


_current: ContextVar[Job | None] = ContextVar('current_job', default=None)
_stage: ContextVar[str] = ContextVar('current_stage', default='preview')


def current() -> Job | None:
    """The job being run in this request, if any."""
    return _current.get()


def stage() -> str:
    """The step of the current job: 'preview' or 'apply'."""
    return _stage.get()


@contextmanager
def run(job: Job, step: str = 'preview') -> Iterator[Job]:
    """Run one step of `job`: make it current, time the step, record its outcome and append the job to the ledger."""
    job_token, stage_token = _current.set(job), _stage.set(step)
    started = time.monotonic()
    try:
        yield job
    except Exception as error:
        if step == 'preview':
            job.status, job.message = 'error', str(error)
        else:
            job.apply_status, job.apply_message = 'error', str(error)
        raise
    finally:
        elapsed = int((time.monotonic() - started) * 1000)
        if step == 'preview':
            # CLAUDE> adjusting a preview is more preview work on the same job, so its time adds up
            job.preview_ms += elapsed
            job.status = 'ok' if job.status == 'running' else job.status
        else:
            job.apply_ms, job.applied_at = elapsed, _now()
            job.apply_status = job.apply_status or 'ok'
        _stage.reset(stage_token)
        _current.reset(job_token)
        save(job)


def save(job: Job) -> None:
    """Store a job in the ledger, replacing an earlier save of the same job."""
    ledger.store(job)


def load() -> list[dict]:
    """Every recorded job in full, newest first; for checks and tests (pages query the ledger directly)."""
    return ledger.all_jobs()


class _RecordedRequest:
    """Wraps a googleapiclient request so `.execute()` is timed and recorded on the current job."""

    def __init__(self, request: Any, method: str, params: dict) -> None:
        """Wrap one request."""
        self._request, self._method, self._params = request, method, params

    def execute(self) -> Any:
        """Run the request and record it."""
        started = time.monotonic()
        error = ''
        try:
            return self._request.execute()
        except Exception as failure:
            error = str(failure)
            raise
        finally:
            if job := current():
                job.google_calls.append(GoogleCall(method=self._method, params=self._params, error=error, stage=stage(),
                                                   latency_ms=int((time.monotonic() - started) * 1000)))


class _RecordedCollection:
    """Wraps `svc.tasks()` and friends."""

    def __init__(self, collection: Any, prefix: str) -> None:
        """Wrap one collection."""
        self._collection, self._prefix = collection, prefix

    def __getattr__(self, method: str) -> Any:
        """Any API method: returns a recorded request."""
        target = getattr(self._collection, method)

        def call(**params: Any) -> Any:
            result = target(**params)
            name = f'{self._prefix}.{method}'
            # CLAUDE> nested collections such as gmail users().messages() have no execute()
            return _RecordedRequest(result, name, params) if hasattr(result, 'execute') else _RecordedCollection(result, name)
        return call


class RecordedService:
    """A Google API client whose every request is recorded on the current job."""

    def __init__(self, service: Any, api: str) -> None:
        """Wrap one API client."""
        self._service, self._api = service, api

    def __getattr__(self, collection: str) -> Any:
        """Any collection, e.g. svc.events()."""
        target = getattr(self._service, collection)
        return lambda: _RecordedCollection(target(), f'{self._api}.{collection}')


def record_fetch(url: str, status: int, size: int, latency_ms: int, via: str = 'download') -> None:
    """Note a page read on the current job, and whether it was downloaded or loaded in the browser."""
    if job := current():
        job.fetches.append(Fetch(url=url, status=status, bytes=size, latency_ms=latency_ms, via=via, stage=stage()))
