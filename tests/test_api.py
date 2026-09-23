"""The generic interpret -> preview -> execute flow over HTTP."""

import pytest
from fastapi.testclient import TestClient

from automation_desk import api
from automation_desk.dates import DateExprError
from automation_desk.groups import GROUPS
from automation_desk.groups.base import Preview, Row
from automation_desk.groups.tasks.tasks.change_dates import ChangeDatesArgs
from automation_desk.interpret import Route

from .conftest import TZ


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An API client with the timezone fixed, so no Google call is made."""
    monkeypatch.setattr(api, 'user_timezone', lambda: TZ)
    return TestClient(api.app)


def test_groups_listing(client: TestClient) -> None:
    groups = client.get('/api/groups').json()
    assert [g['id'] for g in groups] == ['calendar', 'tasks', 'gmail', 'news']
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


def test_attached_files_reach_the_task(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(api, 'UPLOADS', tmp_path)
    up = client.post('/api/uploads', params={'name': '../programma herfst.pdf'}, content=b'%PDF-1.7 hello').json()
    assert up['name'] == 'programma herfst.pdf' and (tmp_path / up['id']).read_bytes() == b'%PDF-1.7 hello'
    task = GROUPS['calendar'].task('add_events_from_web')
    seen: dict = {}
    monkeypatch.setattr(api, 'fill_args', lambda group, t, text: task.Args(
        status='ok', message='', calendar_name='x', exclude_weekdays=[], date_range='', text_filter='', follow_pages=False,
        pdf_mail_from=[], pdf_mail_subject=[]))

    def resolve(args: object, ctx: object) -> tuple:
        """Record the files the task was given."""
        seen['files'] = ctx.files
        raise api.UserError('stop here')

    monkeypatch.setattr(task, 'resolve', resolve)
    client.post('/api/groups/calendar/interpret', json={'text': 'add these', 'task_id': task.id, 'upload_ids': [up['id']]})
    assert seen['files'] == [('programma herfst.pdf', b'%PDF-1.7 hello')]
    gone = client.post('/api/groups/calendar/interpret', json={'text': 'x', 'task_id': task.id, 'upload_ids': ['nope']})
    assert gone.status_code == 422 and 'Attach it again' in gone.json()['detail']


def test_news_endpoints(client: TestClient) -> None:
    from automation_desk.groups.news import store

    store.add_source('https://a.be', 'A', 'https://a.be/rss', 'feed')
    overview = client.get('/api/news/overview').json()
    assert [s['name'] for s in overview['sources']] == ['A']
    assert overview['running'] is False and overview['failure'] == '' and overview['nothing_new'] is None
    assert client.post('/api/news/cap', json={'usd': 11}).status_code == 422
    assert client.post('/api/news/cap', json={'usd': 0.5}).json() == {'cap_usd': 0.5}
    assert client.post('/api/news/subjects', json={'names': ['AI', 'Tech']}).json() == {'subjects': ['AI', 'Tech']}
    assert client.post('/api/news/blocked', json={'names': ['Sports']}).json() == {'blocked': ['Sports']}
    assert client.get('/api/news/overview').json()['blocked'] == ['Sports']
    assert client.get('/api/news/digests/99').status_code == 404
    assert client.delete('/api/news/digests/99').status_code == 404
    assert client.post('/api/news/digests/99/delete-subject', json={'subject': 'AI'}).json() == {'deleted': 0}
    assert client.post('/api/news/suggestions', json={'name': 'AI/ML', 'answer': 'block'}).json() == {'ok': True}
    assert 'AI/ML' in client.get('/api/news/overview').json()['blocked'], 'a name with / works in the body'
    assert client.post('/api/news/suggestions', json={'name': 'X', 'answer': 'maybe'}).status_code == 422
    assert client.delete('/api/news/stories/9-9').status_code == 404
    source_id = overview['sources'][0]['id']
    assert client.delete(f'/api/news/sources/{source_id}').json() == {'ok': True}
    assert client.get('/api/news/overview').json()['sources'] == []


def test_news_site_list_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk.groups.news.tasks import sites

    monkeypatch.setattr(sites, 'find_feed', lambda site, http: ('HLN', 'https://www.hln.be/rss.xml', 'feed'))
    answer = client.post('/api/news/sources', json={'sites': ['hln.be']}).json()
    assert answer['problems'] == [] and [s['name'] for s in answer['sources']] == ['HLN']
