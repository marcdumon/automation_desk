"""The generic interpret -> preview -> execute flow over HTTP."""

import pytest
from fastapi.testclient import TestClient

from llm_automation import api
from llm_automation.dates import DateExprError
from llm_automation.groups import GROUPS
from llm_automation.groups.base import Preview, Row
from llm_automation.groups.tasks.tasks.change_dates import ChangeDatesArgs
from llm_automation.interpret import Route

from .conftest import TZ


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An API client with the timezone fixed, so no Google call is made."""
    monkeypatch.setattr(api, 'user_timezone', lambda: TZ)
    return TestClient(api.app)


def test_groups_listing(client: TestClient) -> None:
    groups = client.get('/api/groups').json()
    assert [g['id'] for g in groups] == ['calendar', 'tasks', 'gmail']
    assert [t['id'] for t in groups[1]['tasks']] == ['change_dates', 'move_tasks', 'complete_tasks', 'delete_tasks', 'add_task']


def test_sentence_for_another_group_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, 'route', lambda group, text: Route(task='none', message='That is a calendar job.'))
    body = client.post('/api/groups/tasks/interpret', json={'text': 'add events from https://x.be'}).json()
    assert (body['status'], body['message'], body['preview']) == ('unsupported', 'That is a calendar job.', None)
    assert body['job']['status'] == 'unsupported'


def test_preview_then_execute_only_selectable_rows_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    task = GROUPS['tasks'].task('change_dates')
    monkeypatch.setattr(api, 'fill_args', lambda group, t, text: ChangeDatesArgs(
        status='ok', message='', list_name='Today', which='open', title_contains=[], due_period='', new_due='tomorrow'))
    seen: dict = {}

    def resolve(args: ChangeDatesArgs, ctx: object) -> tuple:
        """Stand-in resolve."""
        rows = [Row(id='a', cells={}), Row(id='b', cells={}, selectable=False)]
        return Preview(summary='s', columns=[], rows=rows), {'frozen': True}

    def execute(payload: dict, selected: set, ctx: object) -> list[str]:
        """Stand-in execute that records what it was given."""
        seen['execute'] = (payload, selected)
        return ['done']

    monkeypatch.setattr(task, 'resolve', resolve)
    monkeypatch.setattr(task, 'execute', execute)

    body = client.post('/api/groups/tasks/interpret', json={'text': 'x', 'task_id': 'change_dates'}).json()
    assert body['status'] == 'preview' and body['arguments']['new_due'] == 'tomorrow'

    done = client.post('/api/groups/tasks/execute', json={'plan_id': body['plan_id'], 'selected': ['a', 'b', 'zzz']}).json()
    assert done['results'] == ['done']
    assert done['job']['id'] == body['job']['id'], 'apply continues the job of the preview'
    assert (done['job']['apply_status'], done['job']['results']) == ('ok', 1)

    listed = client.get('/api/jobs', params={'group': 'tasks'}).json()['jobs']
    assert [(j['id'], j['status'], j['apply_status']) for j in listed] == [(body['job']['id'], 'ok', 'ok')], 'one job'
    detail = client.get(f"/api/jobs/{body['job']['id']}").json()
    assert detail['results'] == ['done'] and detail['applied_at']
    assert detail['preview']['summary'] == 's' and detail['applied_rows'] == ['a'], 'the preview and what was applied are kept'
    assert client.get(f"/api/jobs/{body['job']['id']}").json()['task_id'] == 'change_dates'
    assert seen['execute'] == ({'frozen': True}, {'a'})
    again = client.post('/api/groups/tasks/execute', json={'plan_id': body['plan_id'], 'selected': ['a']})
    assert again.status_code == 410


def test_user_errors_are_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, 'fill_args', lambda group, t, text: ChangeDatesArgs(
        status='ok', message='', list_name='Today', which='open', title_contains=[], due_period='', new_due='2026-09-30'))

    def resolve(args: ChangeDatesArgs, ctx: object) -> tuple:
        """Stand-in resolve."""
        raise DateExprError('numeric date')

    monkeypatch.setattr(GROUPS['tasks'].task('change_dates'), 'resolve', resolve)
    response = client.post('/api/groups/tasks/interpret', json={'text': 'x', 'task_id': 'change_dates'})
    assert response.status_code == 422 and 'numeric' in response.json()['detail']
    failed = client.get('/api/jobs').json()['jobs'][0]
    assert (failed['status'], failed['message']) == ('error', 'numeric date'), 'failed jobs are recorded too'


def test_unknown_group_is_404(client: TestClient) -> None:
    assert client.post('/api/groups/nope/interpret', json={'text': 'x'}).status_code == 404
