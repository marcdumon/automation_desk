"""The ledger records Google calls too, including nested collections and failures."""

import pytest

from llm_automation import jobs


class _Req:
    """A googleapiclient-like request."""

    def __init__(self, result: object) -> None:
        """Hold the result or exception to produce."""
        self.result = result

    def execute(self) -> object:
        """Return or raise the held result."""
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Messages:
    """gmail users().messages()."""

    def list(self, **params: object) -> _Req:
        """A successful list."""
        return _Req({'messages': []})


class _Users:
    """gmail users()."""

    def messages(self) -> _Messages:
        """Nested collection."""
        return _Messages()

    def getProfile(self, **params: object) -> _Req:
        """A failing call."""
        return _Req(RuntimeError('403'))


class _Gmail:
    """A minimal gmail client."""

    def users(self) -> _Users:
        """Top collection."""
        return _Users()


def test_google_calls_are_recorded_with_params_and_errors() -> None:
    svc = jobs.RecordedService(_Gmail(), 'gmail')
    with jobs.run(jobs.Job(group='gmail', sentence='s')) as job:
        svc.users().messages().list(userId='me', q='is:unread').execute()
        with pytest.raises(RuntimeError):
            svc.users().getProfile(userId='me').execute()
    ok, failed = job.google_calls
    assert (ok.method, ok.params, ok.error) == ('gmail.users.messages.list', {'userId': 'me', 'q': 'is:unread'}, '')
    assert (failed.method, failed.error) == ('gmail.users.getProfile', '403')
    assert jobs.load()[0]['google_calls'][0]['method'] == 'gmail.users.messages.list'


def test_record_fetch_only_inside_a_job() -> None:
    jobs.record_fetch('https://x.be', 200, 10, 5)
    with jobs.run(jobs.Job(group='calendar', sentence='s')) as job:
        jobs.record_fetch('https://x.be', 200, 1234, 80)
    assert [f.bytes for f in job.fetches] == [1234]


def test_calls_are_labelled_with_their_step_and_the_ledger_keeps_the_latest_state() -> None:
    svc = jobs.RecordedService(_Gmail(), 'gmail')
    job = jobs.Job(group='gmail', sentence='s')
    with jobs.run(job):
        svc.users().messages().list(userId='me').execute()
    with jobs.run(job, 'apply'):
        svc.users().messages().list(userId='me').execute()
        jobs.record_fetch('https://x.org', 200, 1, 1)
    assert [c.stage for c in job.google_calls] == ['preview', 'apply'] and job.fetches[0].stage == 'apply'
    stored = jobs.load()
    assert len(stored) == 1 and stored[0]['apply_status'] == 'ok' and len(stored[0]['google_calls']) == 2
