"""The Research routes over HTTP, with the engine's steps run by stand-ins."""

import pytest
from fastapi.testclient import TestClient

from automation_desk import api, stop
from automation_desk.research import run, store


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """A client whose background runs happen at once, in the request."""
    monkeypatch.setattr(api, '_in_background', lambda work, *args: work(*args))
    return TestClient(api.app)


def test_start_answer_and_open_a_research(client, monkeypatch) -> None:
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    monkeypatch.setattr(run, 'advance', lambda rid: store.update(rid, state='done', result={'summary': 'ok'}))
    made = client.post('/api/research', json={'request': 'A pump', 'budget': 150, 'countries': ['BE']}).json()
    assert made['state'] == 'questions'
    after = client.post(f'/api/research/{made["id"]}/answers', json={'answers': {'q1': 'Sewer'}, 'kind': 'product'}).json()
    assert after['state'] == 'done'
    listing = client.get('/api/research').json()
    assert [r['id'] for r in listing['researches']] == [made['id']] and listing['estimate'] > 0


def test_an_empty_request_or_no_country_is_refused(client) -> None:
    assert client.post('/api/research', json={'request': ' ', 'budget': None, 'countries': ['BE']}).status_code == 400
    assert client.post('/api/research', json={'request': 'x', 'budget': None, 'countries': []}).status_code == 400


def test_delete_and_settings(client) -> None:
    rid = store.create('x', 10, ['BE'])
    assert client.delete(f'/api/research/{rid}').json() == {'deleted': True}
    assert client.get(f'/api/research/{rid}').status_code == 404
    saved = client.post('/api/research/settings', json={'municipality': 'Antwerpen'}).json()
    assert saved['municipality'] == 'Antwerpen'


def test_budget_route(client, monkeypatch) -> None:
    """Budget action requires state 'budget', transitions to 'running'."""
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    made = client.post('/api/research', json={'request': 'x', 'budget': None, 'countries': ['BE']}).json()
    # CLAUDE> State is 'questions', budget action requires 'budget' — should fail
    result = client.post(f'/api/research/{made["id"]}/budget', json={'amount': 50})
    assert result.status_code == 409


def test_reply_route(client, monkeypatch) -> None:
    """Reply action requires state 'waiting', transitions to 'running'."""
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    made = client.post('/api/research', json={'request': 'x', 'budget': None, 'countries': ['BE']}).json()
    # CLAUDE> State is 'questions', reply action requires 'waiting' — should fail
    result = client.post(f'/api/research/{made["id"]}/reply', json={'answers': {'q1': 'a'}})
    assert result.status_code == 409


def test_continue_route(client, monkeypatch) -> None:
    """Continue action requires state 'failed'."""
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    made = client.post('/api/research', json={'request': 'x', 'budget': None, 'countries': ['BE']}).json()
    # CLAUDE> State is 'questions', continue action requires 'failed' — should fail
    result = client.post(f'/api/research/{made["id"]}/continue')
    assert result.status_code == 409


def test_answers_wrong_state(client, monkeypatch) -> None:
    """Answers action requires state 'questions'."""
    rid = store.create('x', 10, ['BE'])
    store.update(rid, state='done')
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    # CLAUDE> State is 'done', answers action requires 'questions' — should fail
    result = client.post(f'/api/research/{rid}/answers', json={'answers': {'q1': 'a'}, 'kind': 'product'})
    assert result.status_code == 409


def test_delete_running_research(client, monkeypatch) -> None:
    """Cannot delete research while it runs."""
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    monkeypatch.setattr(run, 'progress', lambda: {'id': 123})
    made = client.post('/api/research', json={'request': 'x', 'budget': None, 'countries': ['BE']}).json()
    store.update(made['id'], state='running')
    result = client.delete(f'/api/research/{made["id"]}')
    assert result.status_code == 409


def test_answers_happy_path(client, monkeypatch) -> None:
    """Answers route returns 200, transitions state to running, second call returns 409."""
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    # CLAUDE> Track advance calls
    advance_calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: advance_calls.append(rid))
    # CLAUDE> Create research in state 'questions'
    made = client.post('/api/research', json={'request': 'x', 'budget': None, 'countries': ['BE']}).json()
    assert made['state'] == 'questions'
    # CLAUDE> First call succeeds and transitions to running
    result1 = client.post(f'/api/research/{made["id"]}/answers', json={'answers': {'q1': 'a'}, 'kind': 'product'})
    assert result1.status_code == 200
    assert result1.json()['state'] == 'running'
    # CLAUDE> Verify stored data: answers, kind, and the requirements step that comes next
    row = store.get(made['id'])
    assert row['answers'] == {'q1': 'a'}
    assert row['kind'] == 'product'
    assert row['step'] == 'requirements'
    # CLAUDE> Verify advance was called with this research id
    assert advance_calls == [made['id']]
    # CLAUDE> Second call fails because state is now 'running'
    result2 = client.post(f'/api/research/{made["id"]}/answers', json={'answers': {'q1': 'b'}, 'kind': 'product'})
    assert result2.status_code == 409


def test_budget_happy_path(client, monkeypatch) -> None:
    """Budget route returns 200, transitions state to running, second call returns 409."""
    monkeypatch.setattr(run, 'start', lambda request, budget, countries: store.create(request, budget, countries))
    # CLAUDE> Track advance calls
    advance_calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: advance_calls.append(rid))
    # CLAUDE> Create and manually set to state 'budget' (normal flow would do this in answers)
    made = store.create('x', None, ['BE'])
    store.update(made, state='budget')
    # CLAUDE> First call succeeds and transitions to running
    result1 = client.post(f'/api/research/{made}/budget', json={'amount': 50})
    assert result1.status_code == 200
    assert result1.json()['state'] == 'running'
    # CLAUDE> Verify stored data: budget amount and step 'plan'
    row = store.get(made)
    assert row['budget'] == 50
    assert row['step'] == 'plan'
    # CLAUDE> Verify advance was called with this research id
    assert advance_calls == [made]
    # CLAUDE> Second call fails because state is now 'running'
    result2 = client.post(f'/api/research/{made}/budget', json={'amount': 75})
    assert result2.status_code == 409


def test_reply_happy_path(client, monkeypatch) -> None:
    """Reply route returns 200, transitions state to running, second call returns 409."""
    # CLAUDE> Track advance calls
    advance_calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: advance_calls.append(rid))
    # CLAUDE> Create and set to state 'waiting'
    made = store.create('x', 10, ['BE'])
    store.update(made, state='waiting')
    # CLAUDE> First call succeeds and transitions to running
    result1 = client.post(f'/api/research/{made}/reply', json={'answers': {'q1': 'a'}})
    assert result1.status_code == 200
    assert result1.json()['state'] == 'running'
    # CLAUDE> Verify stored data: followup_answers and step 'score'
    row = store.get(made)
    assert row['followup_answers'] == {'q1': 'a'}
    assert row['step'] == 'score'
    # CLAUDE> Verify advance was called with this research id
    assert advance_calls == [made]
    # CLAUDE> Second call fails because state is now 'running'
    result2 = client.post(f'/api/research/{made}/reply', json={'answers': {'q1': 'b'}})
    assert result2.status_code == 409


def test_continue_happy_path(client, monkeypatch) -> None:
    """Continue route returns 200, transitions state to running, second call returns 409."""
    # CLAUDE> Track advance calls
    advance_calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: advance_calls.append(rid))
    # CLAUDE> Create and set to state 'failed'
    made = store.create('x', 10, ['BE'])
    store.update(made, state='failed', step='search', note='Previous error message')
    # CLAUDE> First call succeeds and transitions to running
    result1 = client.post(f'/api/research/{made}/continue')
    assert result1.status_code == 200
    assert result1.json()['state'] == 'running'
    # CLAUDE> Verify stored data: state 'running', note cleared, step unchanged
    row = store.get(made)
    assert row['state'] == 'running'
    assert row['note'] == ''
    assert row['step'] == 'search'
    # CLAUDE> Verify advance was called with this research id
    assert advance_calls == [made]
    # CLAUDE> Second call fails because state is now 'running'
    result2 = client.post(f'/api/research/{made}/continue')
    assert result2.status_code == 409


def test_stop_is_per_research(client) -> None:
    """The page stops its own research; other names are refused."""
    assert client.post('/api/stop/research-5').json() == {'stopping': 'research-5'}
    assert stop.requested('research-5') and not stop.requested('research-6')
    assert client.post('/api/stop/research-x').status_code == 404


@pytest.mark.parametrize('limits', [{'searches': 2.5, 'pages': 30, 'cost': 1.0}, {'searches': 20, 'pages': 0, 'cost': 1.0},
                                    {'searches': 20, 'pages': 30, 'cost': 0}, {'searches': 20, 'pages': 30}])
def test_fractional_or_partial_limits_are_refused(client, limits: dict) -> None:
    """Limits are whole searches and pages and a cost above zero, all three given."""
    assert client.post('/api/research/settings', json={'limits': limits}).status_code == 422
    assert store.settings()['limits'] == {'searches': 20, 'pages': 30, 'cost': 1.0}


def test_whole_limits_are_saved(client) -> None:
    saved = client.post('/api/research/settings', json={'limits': {'searches': 5, 'pages': 8, 'cost': 0.5}}).json()
    assert saved['limits'] == {'searches': 5, 'pages': 8, 'cost': 0.5}


def test_a_failed_question_form_reaches_the_user(client, monkeypatch) -> None:
    """The model error is shown and no research stays behind."""
    from automation_desk.llm import LLMError
    from automation_desk.research import questions

    monkeypatch.setattr(questions, 'ask', lambda *a, **k: (_ for _ in ()).throw(LLMError('OpenRouter returned 502')))
    answer = client.post('/api/research', json={'request': 'A pump', 'budget': 150, 'countries': ['BE']})
    assert answer.status_code == 502 and '502' in answer.json()['detail'] and store.listing() == []


def test_the_requirements_route_runs_the_research_on(client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: calls.append(rid))
    rid = store.create('A pump', 150, ['BE'])
    store.update(rid, state='requirements', requirements=[{'id': 'r1', 'text': 'Old', 'weight': 'must'}])
    given = {'requirements': [{'text': 'Pumps dirt', 'weight': 'must'}, {'text': ' ', 'weight': 'nice'}]}
    after = client.post(f'/api/research/{rid}/requirements', json=given)
    assert after.status_code == 200 and calls == [rid]
    row = store.get(rid)
    assert (row['state'], row['step']) == ('running', 'plan')
    assert row['requirements'] == [{'id': 'r1', 'text': 'Pumps dirt', 'weight': 'must'}]
    assert client.post(f'/api/research/{rid}/requirements', json=given).status_code == 409
    store.update(rid, state='requirements')
    assert client.post(f'/api/research/{rid}/requirements', json={'requirements': []}).status_code == 400


def test_score_again_only_for_a_finished_research(client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: calls.append(rid))
    rid = store.create('A pump', 150, ['BE'])
    store.add_page(rid, 'https://a.be/p', 'q', 'A', '', 'BE')
    store.update_page(rid, 'https://a.be/p', status='read', facts={'products': []})
    store.update(rid, state='running')
    assert client.post(f'/api/research/{rid}/rescore').status_code == 409
    for state in ('done', 'stopped'):
        store.update(rid, state=state)
        assert client.post(f'/api/research/{rid}/rescore').status_code == 200
        assert store.get(rid)['step'] == 'requirements'
    assert calls == [rid, rid]


def test_vault_settings_and_export(client, tmp_path) -> None:
    """The vault and subfolder are kept; the export writes the note there; a wrong subfolder is a plain 400."""
    vault = tmp_path / 'vault'
    vault.mkdir()
    saved = client.post('/api/research/settings', json={'vault': str(vault), 'subdir': 'Research'}).json()
    assert (saved['vault'], saved['subdir']) == (str(vault), 'Research')
    assert 'vaults' in client.get('/api/research').json()
    rid = store.create('A pump for the pit', 150, ['BE'])
    store.update(rid, state='done', result={'summary': 'Fits.', 'ranking': []})
    written = client.post(f'/api/research/{rid}/export').json()
    assert written['path'].startswith(str(vault / 'Research')) and (vault / 'Research').iterdir()
    client.post('/api/research/settings', json={'subdir': '../out'})
    answer = client.post(f'/api/research/{rid}/export')
    assert answer.status_code == 400 and 'inside the vault' in answer.json()['detail']


def test_score_again_without_pages_and_cancel_are_plain_409s(client, monkeypatch) -> None:
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    rid = store.create('Nothing read', 150, ['BE'])
    store.update(rid, state='done', result={'summary': 'x'})
    answer = client.post(f'/api/research/{rid}/rescore')
    assert answer.status_code == 409 and 'no shop page' in answer.json()['detail'] and store.get(rid)['state'] == 'done'
    assert client.post(f'/api/research/{rid}/requirements/cancel').status_code == 409
    store.update(rid, state='requirements')
    assert client.post(f'/api/research/{rid}/requirements/cancel').json()['state'] == 'done'


def test_score_again_with_the_edited_table(client, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(run, 'advance', lambda rid: calls.append(rid))
    rid = store.create('A pump', 150, ['BE'])
    store.add_page(rid, 'https://a.be/p', 'q', 'A', '', 'BE')
    store.update_page(rid, 'https://a.be/p', status='read', facts={'products': []})
    store.update(rid, state='done', limits={'searches': 20, 'pages': 30, 'cost': 1.0}, result={'stopped_by': 'the page limit'})
    assert 'after reading 30 shop pages' in client.get(f'/api/research/{rid}').json()['stopped_text']
    given = {'requirements': [{'text': 'Pumps dirt', 'weight': 'must'}]}
    answer = client.post(f'/api/research/{rid}/rescore', json=given)
    assert answer.status_code == 200 and calls == [rid] and store.get(rid)['requirements'][0]['text'] == 'Pumps dirt'
    store.update(rid, state='done')
    assert client.post(f'/api/research/{rid}/rescore', json={'requirements': []}).status_code == 400


def test_a_title_can_be_changed(client) -> None:
    rid = store.create('A long request', 10, ['BE'])
    assert client.patch(f'/api/research/{rid}', json={'title': '  Lift pit pump '}).json()['title'] == 'Lift pit pump'
    assert client.patch(f'/api/research/{rid}', json={'title': ' '}).status_code == 400


def test_a_finished_research_is_saved_to_obsidian_by_itself(client, monkeypatch, tmp_path) -> None:
    """The user's notes were never saved: the only save was a button. A finished research now goes to the vault at once."""
    monkeypatch.setattr(run, 'advance', lambda rid: store.update(rid, state='done', result={'summary': 'Fits.', 'ranking': []}))
    client.post('/api/research/settings', json={'vault': str(tmp_path), 'subdir': 'Research'})
    rid = store.create('A pump', 150, ['BE'])
    store.update(rid, state='questions', title='Lift pit pump')
    client.post(f'/api/research/{rid}/answers', json={'answers': {'q1': 'a'}, 'kind': 'product'})
    note = store.get(rid)['result']['note']
    assert note['path'].endswith('_lift_pit_pump.md') and (tmp_path / 'Research').iterdir()


def test_without_a_vault_a_finished_research_is_not_saved(client, monkeypatch) -> None:
    monkeypatch.setattr(run, 'advance', lambda rid: store.update(rid, state='done', result={'summary': 'Fits.'}))
    client.post('/api/research/settings', json={'vault': ''})
    rid = store.create('A pump', 150, ['BE'])
    store.update(rid, state='questions')
    client.post(f'/api/research/{rid}/answers', json={'answers': {'q1': 'a'}, 'kind': 'product'})
    assert 'note' not in store.get(rid)['result']
