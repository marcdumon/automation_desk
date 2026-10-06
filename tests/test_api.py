"""The generic interpret -> preview -> execute flow over HTTP."""

import time

import pytest
from fastapi.testclient import TestClient

from automation_desk import api
from automation_desk.dates import DateExprError
from automation_desk.groups import GROUPS
from automation_desk.groups.base import Preview, Row
from automation_desk.groups.calendar.tasks.delete_events import DeleteEventsArgs
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
    assert groups[3]['tasks'] == [], 'News is run from its page, not from sentences'
    assert [t['id'] for t in groups[1]['tasks']] == ['promote_planned', 'demote_unplanned', 'verb_titles', 'label_deadline']


def test_sentence_for_another_group_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, 'route', lambda group, text: Route(task='none', message='That is a calendar job.'))
    body = client.post('/api/groups/tasks/interpret', json={'text': 'add events from https://x.be'}).json()
    assert (body['status'], body['message'], body['preview']) == ('unsupported', 'That is a calendar job.', None)
    assert body['job']['status'] == 'unsupported'


def test_preview_then_execute_only_selectable_rows_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    task = GROUPS['calendar'].task('delete_events')
    monkeypatch.setattr(api, 'fill_args', lambda group, t, text: DeleteEventsArgs(
        status='ok', message='', calendar_name='Test', date_range='tomorrow', title_contains=[], source_contains=''))
    seen: dict = {}

    def resolve(args: DeleteEventsArgs, ctx: object) -> tuple:
        """Stand-in resolve."""
        rows = [Row(id='a', cells={}), Row(id='b', cells={}, selectable=False)]
        return Preview(summary='s', columns=[], rows=rows), {'frozen': True}

    def execute(payload: dict, selected: set, ctx: object) -> list[str]:
        """Stand-in execute that records what it was given."""
        seen['execute'] = (payload, selected)
        return ['done']

    monkeypatch.setattr(task, 'resolve', resolve)
    monkeypatch.setattr(task, 'execute', execute)

    body = client.post('/api/groups/calendar/interpret', json={'text': 'x', 'task_id': 'delete_events'}).json()
    assert body['status'] == 'preview' and body['arguments']['date_range'] == 'tomorrow'

    done = client.post('/api/groups/calendar/execute', json={'plan_id': body['plan_id'], 'selected': ['a', 'b', 'zzz']}).json()
    assert done['results'] == ['done']
    assert done['job']['id'] == body['job']['id'], 'apply continues the job of the preview'
    assert (done['job']['apply_status'], done['job']['results']) == ('ok', 1)

    listed = client.get('/api/jobs', params={'group': 'calendar'}).json()['jobs']
    assert [(j['id'], j['status'], j['apply_status']) for j in listed] == [(body['job']['id'], 'ok', 'ok')], 'one job'
    detail = client.get(f"/api/jobs/{body['job']['id']}").json()
    assert detail['results'] == ['done'] and detail['applied_at']
    assert detail['preview']['summary'] == 's' and detail['applied_rows'] == ['a'], 'the preview and what was applied are kept'
    assert client.get(f"/api/jobs/{body['job']['id']}").json()['task_id'] == 'delete_events'
    assert seen['execute'] == ({'frozen': True}, {'a'})
    again = client.post('/api/groups/calendar/execute', json={'plan_id': body['plan_id'], 'selected': ['a']})
    assert again.status_code == 410


def test_user_errors_are_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, 'fill_args', lambda group, t, text: DeleteEventsArgs(
        status='ok', message='', calendar_name='Test', date_range='2026-09-30', title_contains=[], source_contains=''))

    def resolve(args: DeleteEventsArgs, ctx: object) -> tuple:
        """Stand-in resolve."""
        raise DateExprError('numeric date')

    monkeypatch.setattr(GROUPS['calendar'].task('delete_events'), 'resolve', resolve)
    response = client.post('/api/groups/calendar/interpret', json={'text': 'x', 'task_id': 'delete_events'})
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
    assert overview['progress'] == {}
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
    from automation_desk.groups.news import sites

    monkeypatch.setattr(sites, 'find_feed', lambda site, http: ('HLN', 'https://www.hln.be/rss.xml', 'feed'))
    answer = client.post('/api/news/sources', json={'sites': ['hln.be']}).json()
    assert answer['problems'] == [] and [s['name'] for s in answer['sources']] == ['HLN']


def test_a_server_older_than_its_code_asks_for_a_restart(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get('/api/version').json()['restart_needed'] is False
    monkeypatch.setattr(api, 'code_stamp', lambda: api.STARTED_STAMP + 1)
    assert client.get('/api/version').json()['restart_needed'] is True, 'code changed on disk after the server started'


def test_continuing_a_digest_starts_in_the_background(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk.groups.news import digest as news_digest

    started = []
    monkeypatch.setattr(news_digest, 'continue_digest', lambda digest_id, raise_cap: started.append((digest_id, raise_cap)))
    assert client.post('/api/news/digests/3/continue', json={'raise_cap': True}).json() == {'started': True}
    for _ in range(100):
        if started:
            break
        time.sleep(0.01)
    assert started == [(3, True)]


def test_watched_agenda_sites(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk.groups.calendar.tasks.check_watched import CheckWatchedSites

    lines = ['kmska.be/nl/agenda → Exhibitions', 'mas.be']
    saved = client.post('/api/calendar/watch', json={'lines': lines, 'default_calendar': 'events'})
    assert saved.json()['lines'] == ['kmska.be/nl/agenda → Exhibitions', 'mas.be']
    got = client.get('/api/calendar/watch').json()
    assert (got['default_calendar'], got['needs_browser']) == ('events', [])
    # CLAUDE> each site with its id, so the page can tick the ones to check
    assert [(s['label'], s['url'], s['calendar'], s['last_result']) for s in got['sites']] == [
        ('kmska.be/nl/agenda', 'https://kmska.be/nl/agenda', 'Exhibitions', ''), ('mas.be', 'https://mas.be', '', '')]
    assert all(isinstance(s['id'], int) for s in got['sites'])
    asked = {}

    def check(self: object, ctx: object, via_browser: bool = False, only: set | None = None) -> tuple:
        """Stand-in: one new event."""
        asked.update(via_browser=via_browser, only=only)
        return Preview(summary='1 new event(s)', columns=['Title'], rows=[Row(id='k1', cells={'Title': 'Expo'})]), {'offered': ['k1']}

    monkeypatch.setattr(CheckWatchedSites, 'check', check)
    answer = client.post('/api/calendar/watch/check', json={'via_browser': True, 'only': [1]}).json()
    assert answer['status'] == 'preview' and answer['plan_id'] and answer['task_id'] == 'check_watched_sites'
    assert asked == {'via_browser': True, 'only': {1}}


def test_watched_sites_from_imported_events(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk.groups.calendar.tasks import check_watched

    monkeypatch.setattr(api, 'context', lambda sentence, files=None: type('C', (), {'google': lambda self, n, v: None})())
    monkeypatch.setattr(check_watched, 'sites_from_events', lambda svc, http: ([('https://kmska.be/nl/agenda', 'Exhibitions')], 2))
    answer = client.post('/api/calendar/watch/from-events').json()
    assert (answer['added'], answer['unmatched']) == (1, 2) and answer['lines'] == ['kmska.be/nl/agenda → Exhibitions']


def test_one_section_is_applied_and_the_plan_stays_for_the_rest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk import jobs
    from automation_desk.groups.calendar.tasks.check_watched import CheckWatchedSites
    from automation_desk.plans import Plan

    applied = []
    monkeypatch.setattr(CheckWatchedSites, 'execute_group', lambda self, payload, selected, section, ctx: (
        applied.append((section, selected)) or ['Added'], {'b1', 'b2'}))
    monkeypatch.setattr(CheckWatchedSites, 'execute', lambda self, payload, selected, ctx: applied.append(('all', selected)) or ['ok'])
    plan_id = api.plans.put(Plan(group_id='calendar', task_id='check_watched_sites', payload={}, row_ids={'a1', 'b1', 'b2'},
                                   job=jobs.Job(group='calendar', sentence='Check watched agenda sites')))
    one = client.post('/api/groups/calendar/execute', json={'plan_id': plan_id, 'selected': ['b1', 'a1'], 'section': 'b.be'}).json()
    assert one['results'] == ['Added'] and applied == [('b.be', {'b1', 'a1'})]
    rest = client.post('/api/groups/calendar/execute', json={'plan_id': plan_id, 'selected': ['a1', 'b1']}).json()
    assert rest['results'] == ['ok'] and applied[-1] == ('all', {'a1'}), "b.be's rows are done"


def test_applying_a_check_with_nothing_ticked_still_declines(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk import jobs
    from automation_desk.groups.calendar.tasks.check_watched import CheckWatchedSites
    from automation_desk.plans import Plan

    seen = []
    monkeypatch.setattr(CheckWatchedSites, 'execute', lambda self, payload, selected, ctx: seen.append(selected) or ['Nothing added.'])
    plan_id = api.plans.put(Plan(group_id='calendar', task_id='check_watched_sites', payload={}, row_ids={'a1'},
                                 job=jobs.Job(group='calendar', sentence='Check watched agenda sites')))
    client.post('/api/groups/calendar/execute', json={'plan_id': plan_id, 'selected': []})
    assert seen == [set()], 'the unticked events are remembered as declined'


def test_stop_asks_a_running_action_to_stop(client: TestClient) -> None:
    from automation_desk import stop

    stop.begin('watch-check')
    assert client.post('/api/stop/watch-check').json() == {'stopping': 'watch-check'} and stop.requested('watch-check')
    assert client.post('/api/stop/anything-else').status_code == 404


def test_the_pdf_white_space_tool_returns_the_widened_pdf_under_the_chosen_name(client: TestClient) -> None:
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(600, 800)
    original = io.BytesIO()
    writer.write(original)
    answer = client.post('/api/tools/pdf-margin?side=right&percent=33&name=Notes%20lecture%201', content=original.getvalue())
    assert answer.status_code == 200 and answer.headers['content-type'] == 'application/pdf'
    assert answer.headers['content-disposition'] == "attachment; filename*=UTF-8''Notes%20lecture%201.pdf"
    assert round(float(PdfReader(io.BytesIO(answer.content)).pages[0].cropbox.right)) == 798
    refused = client.post('/api/tools/pdf-margin?side=right&percent=33&name=x', content=b'hello')
    assert refused.status_code == 422 and 'not a PDF' in refused.json()['detail']


def test_task_stats_are_refreshed_when_the_page_asks(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime

    from automation_desk.groups.base import Context

    from .conftest import FakeTodoist

    fake = FakeTodoist([{'id': 'P1', 'name': '🏠 Home'}], [
        {'id': 'a', 'content': 'a', 'project_id': 'P1', 'section_id': None, 'labels': ['this_week'], 'due': None,
         'added_at': '2026-09-30T06:00:00Z'}],
        [{'id': 'x', 'content': 'x', 'project_id': 'P1', 'labels': ['frog'], 'completed_at': '2026-09-30T06:30:00Z',
          'added_at': '2026-09-29T06:00:00Z'}])
    now = datetime(2026, 9, 30, 8, 0, tzinfo=TZ)
    monkeypatch.setattr(api, 'context', lambda sentence, files=None: Context(sentence=sentence, now=now, tz=TZ,
                                                                          service=lambda *a: None, todoist_factory=lambda: fake))
    view = client.get('/api/tasks/stats', params={'period': 30}).json()
    assert view['kpis']['open']['now'] == 1 and view['series'][-1]['completed'] == 1 and view['series'][-1]['frog'] is True


def test_a_sentence_the_chosen_task_cannot_do_goes_to_the_task_that_can(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The page kept 'Move unplanned tasks into Someday' chosen; 'move my planned tasks out of someday' is the other one."""
    from automation_desk.groups.tasks.tasks.someday import PromoteArgs

    asked = []

    def fill(group: object, task: object, text: str) -> PromoteArgs:
        """The chosen task says it cannot; the routed one can."""
        asked.append(task.id)
        if task.id == 'demote_unplanned':
            return PromoteArgs(status='unsupported', message='This task moves unplanned tasks into Someday.')
        return PromoteArgs(status='ok', message='')

    promote = GROUPS['tasks'].task('promote_planned')
    monkeypatch.setattr(api, 'fill_args', fill)
    monkeypatch.setattr(api, 'route', lambda group, text: Route(task='promote_planned', message=''))
    monkeypatch.setattr(promote, 'resolve', lambda args, ctx: (Preview(summary='Move 2', columns=[], rows=[]), {}))
    body = client.post('/api/groups/tasks/interpret', json={'text': 'move my planned tasks out of someday',
                                                            'task_id': 'demote_unplanned'}).json()
    assert (body['status'], body['task_id'], asked) == ('preview', 'promote_planned', ['demote_unplanned', 'promote_planned'])


def test_habits_page_round_trip(client: TestClient) -> None:
    habit = client.post('/api/habits', json={'name': 'Measure: Weight', 'schedule': 'daily'}).json()
    day = habit['day']
    assert [h['name'] for h in habit['today']] == ['Measure: Weight']
    hid = habit['today'][0]['id']
    ticked = client.post(f'/api/habits/{hid}/check', json={'day': day, 'done': True}).json()
    assert ticked['today'][0]['done'] is True
    renamed = client.patch(f'/api/habits/{hid}', json={'name': 'Weigh: Morning', 'schedule': 'weekdays'}).json()
    assert renamed['habits'][0]['name'] == 'Weigh: Morning'
    bad = client.post('/api/habits', json={'name': 'x', 'schedule': 'monthly'})
    assert bad.status_code == 422 and 'every day' in bad.json()['detail']
    assert client.post('/api/habits/reminder', json={'at': '21:00'}).json()['reminder'] == '21:00'
    assert client.delete(f'/api/habits/{hid}').json()['habits'] == []


def test_habits_can_be_reordered(client: TestClient) -> None:
    for name in ('A', 'B', 'C'):
        view = client.post('/api/habits', json={'name': name, 'schedule': 'daily'}).json()
    ids = [h['id'] for h in view['habits']]
    view = client.post('/api/habits/order', json={'ids': [ids[2], ids[0], ids[1]]}).json()
    assert [h['name'] for h in view['habits']] == ['C', 'A', 'B']


def test_the_pages_show_plain_task_descriptions(client: TestClient) -> None:
    """Gmail showed the model's hints to the user: 'Answers a QUESTION…', 'Only when the user asks to CHANGE mails…'."""
    tasks = {t['id']: t['description'] for g in client.get('/api/groups').json() for t in g['tasks']}
    assert not any(word in text for text in tasks.values() for word in ('QUESTION', 'CHANGE', 'the user'))
    from automation_desk.groups.gmail.tasks.label_mail import LabelMail
    assert 'Not for questions' in LabelMail.description, 'the model still gets its hints'
