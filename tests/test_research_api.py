"""The Research routes over HTTP, with the engine's steps run by stand-ins."""

from pathlib import Path

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


SUMMED = {'title': 'Flat roofs', 'paragraphs': ['S.'], 'stopped_by': '', 'unread': [], 'off_subject': [], 'pages_read': 2,
          'groups': [{'heading': 'Rules', 'items': [{'text': 'A permit.', 'sources': [1]}, {'text': 'Costs.', 'sources': [1, 2]}]}],
          'sources': [{'n': 1, 'url': 'https://vrt.be/a', 'title': '', 'site': 'vrt.be'},
                      {'n': 2, 'url': 'https://hln.be/b', 'title': '', 'site': 'hln.be'}]}


def test_start_a_summary_and_see_it_in_the_list(client, monkeypatch) -> None:
    ran = []
    monkeypatch.setattr(run, 'advance', lambda rid: (ran.append(rid), store.update(rid, state='done', result=SUMMED)))
    made = client.post('/api/research/summary', json={'subject': 'Flat roofs', 'sites': ['vrt.be', '', 'https://hln.be/b']}).json()
    assert (made['kind'], made['target'], ran) == ('summary', {}, [made['id']])
    assert made['plan'] == [{'kind': 'site', 'site': 'vrt.be'}, {'kind': 'page', 'url': 'https://hln.be/b'}]
    card = client.get('/api/research').json()['researches'][0]
    assert card['summary'] == {'points': 2, 'sources': 2, 'sites': 2}


@pytest.mark.parametrize('body, says', [
    ({'subject': ' ', 'sites': ['vrt.be']}, 'Write the subject'),
    ({'subject': 'x', 'sites': ['vrt.be', 'solar panels']}, 'Line 2, "solar panels", is not a website'),
    ({'subject': 'x', 'sites': ['vrt.be'], 'target': {'note': 'a.md'}}, 'No Obsidian vault is set')])
def test_a_summary_needs_a_subject_websites_and_a_note_that_exists(client, body: dict, says: str) -> None:
    answer = client.post('/api/research/summary', json=body)
    assert answer.status_code == 400 and says in answer.json()['detail']


def test_the_notes_and_their_headings_come_from_the_vault_in_settings(client, monkeypatch, tmp_path) -> None:
    vault = tmp_path / 'work_vault'
    (vault / 'Projects').mkdir(parents=True)
    (vault / 'Projects' / 'roof.md').write_text('\n'.join(['# Roof', 'Intro.', '## Costs', 'Text.', '']))
    store.save_settings(vault=str(vault))
    # CLAUDE> /api/research/{id} matches any word: the notes route must come first
    assert client.get('/api/research/notes', params={'q': 'roof'}).json() == {'notes': [{'path': 'Projects/roof.md', 'name': 'roof'}]}
    assert client.get('/api/research/notes/headings', params={'path': 'Projects/roof.md'}).json() == {
        'headings': [{'level': 1, 'text': 'Roof'}, {'level': 2, 'text': 'Costs'}]}
    assert client.get('/api/research/notes/headings', params={'path': '../x.md'}).status_code == 400
    monkeypatch.setattr(run, 'advance', lambda rid: store.update(rid, state='done', result=SUMMED))
    target = {'note': 'Projects/roof.md', 'place': 'after', 'heading': {'level': 1, 'text': 'Roof'}, 'level': 2}
    made = client.post('/api/research/summary', json={'subject': 'Flat roofs', 'sites': ['vrt.be'], 'target': target}).json()
    assert made['target'] == target | {'vault': str(vault), 'title': ''}
    text = (vault / 'Projects' / 'roof.md').read_text()
    assert text.index('## Costs') < text.index(f'%% summary {made["id"]} %%'), 'saved at once, after all text under Roof'
    assert made['result']['note']['path'] == str(vault / 'Projects' / 'roof.md') and made['result']['note']['error'] == ''


def test_a_summary_is_not_scored_again(client) -> None:
    rid = store.create('Flat roofs', None, [])
    store.update(rid, kind='summary', state='done', result=SUMMED)
    store.add_page(rid, 'https://vrt.be/a', 'vrt.be', '', '', '')
    store.update_page(rid, 'https://vrt.be/a', status='read')
    answer = client.post(f'/api/research/{rid}/rescore')
    assert answer.status_code == 409 and 'summary' in answer.json()['detail'] and store.get(rid)['state'] == 'done'


def test_a_summary_on_top_and_a_place_after_without_a_heading(client, monkeypatch, tmp_path) -> None:
    vault = tmp_path / 'work_vault'
    vault.mkdir()
    (vault / 'roof.md').write_text('\n'.join(['# Roof', 'Intro.', '## Costs', 'Text.', '']))
    store.save_settings(vault=str(vault))
    monkeypatch.setattr(run, 'advance', lambda rid: store.update(rid, state='done', result=SUMMED))
    body = {'subject': 'Flat roofs', 'sites': ['vrt.be']}
    refused = client.post('/api/research/summary', json=body | {'target': {'note': 'roof.md', 'place': 'after'}})
    assert refused.status_code == 400 and 'heading' in refused.json()['detail']
    made = client.post('/api/research/summary', json=body | {'target': {'note': 'roof.md', 'place': 'top',
                                                                        'heading': {'level': 2, 'text': 'Costs'}}}).json()
    assert made['target'] == {'note': 'roof.md', 'place': 'top', 'heading': None, 'level': 2, 'title': '', 'vault': str(vault)}
    text = (vault / 'roof.md').read_text()
    assert text.index('Intro.') < text.index(f'%% summary {made["id"]} %%') < text.index('\n## Costs\n')


def finished_summary(target: dict | None = None) -> int:
    """A finished summary with one page read, saved to the target given (default: a new note)."""
    rid = run.start_summary('Flat roofs', [{'kind': 'site', 'site': 'vrt.be'}], target or {})
    store.update(rid, state='done', result=SUMMED, title='Flat roofs', searches_done=1)
    store.add_page(rid, 'https://vrt.be/a', 'vrt.be', '', '', '')
    store.update_page(rid, 'https://vrt.be/a', status='read')
    return rid


def test_go_on_runs_what_is_left_and_refuses_when_nothing_is(client, monkeypatch) -> None:
    ran = []
    monkeypatch.setattr(run, 'advance', lambda rid: (ran.append(rid), store.update(rid, state='done')))
    rid = finished_summary()
    opened = client.get(f'/api/research/{rid}').json()
    assert opened['left'] == {'pages': 0, 'searches': 0} and 'go_on_estimate' in opened
    nothing = client.post(f'/api/research/{rid}/more')
    assert nothing.status_code == 409 and 'Nothing is left' in nothing.json()['detail']
    store.add_page(rid, 'https://vrt.be/b', 'vrt.be', '', '', '')
    assert client.post(f'/api/research/{rid}/more').json()['state'] == 'done' and ran == [rid]
    store.update(rid, state='running')
    assert client.post(f'/api/research/{rid}/more').status_code == 409
    assert client.get('/api/research').json()['unit_costs'].keys() == {'search', 'page', 'steps', 'summary_steps'}


def test_websites_are_added_to_a_summary_only(client, monkeypatch) -> None:
    monkeypatch.setattr(run, 'advance', lambda rid: store.update(rid, state='done'))
    rid = finished_summary()
    bad = client.post(f'/api/research/{rid}/sites', json={'sites': ['solar panels']})
    assert bad.status_code == 400 and 'is not a website' in bad.json()['detail']
    same = client.post(f'/api/research/{rid}/sites', json={'sites': ['vrt.be']})
    assert same.status_code == 409 and 'already' in same.json()['detail']
    added = client.post(f'/api/research/{rid}/sites', json={'sites': ['hln.be']}).json()
    assert added['plan'][-1] == {'kind': 'site', 'site': 'hln.be'}
    product = store.create('A pump', 100, ['BE'])
    store.update(product, state='done')
    assert client.post(f'/api/research/{product}/sites', json={'sites': ['hln.be']}).status_code == 409


def two_notes(tmp_path) -> object:
    """A vault in Settings with note a.md (a title and text) and note b.md (a title, an intro and one part)."""
    vault = tmp_path / 'work_vault'
    vault.mkdir()
    (vault / 'a.md').write_text('\n'.join(['# A', 'Text A.', '']))
    (vault / 'b.md').write_text('\n'.join(['# B', 'Intro B.', '## Part', 'Text.', '']))
    store.save_settings(vault=str(vault))
    return vault


def test_a_summary_saved_somewhere_else_keeps_its_earlier_copy(client, tmp_path) -> None:
    """One place to save, chosen at each save: a save never deletes what was saved before somewhere else."""
    vault = two_notes(tmp_path)
    rid = finished_summary({'vault': str(vault), 'note': 'a.md', 'place': 'end', 'heading': None, 'level': 2, 'title': ''})
    client.post(f'/api/research/{rid}/export')
    saved = client.post(f'/api/research/{rid}/export', json={'target': {
        'note': 'b.md', 'place': 'top', 'level': 2, 'title': 'Pump guide'}}).json()
    assert f'%% summary {rid} %%' in (vault / 'a.md').read_text(), 'the copy saved before stays'
    b = (vault / 'b.md').read_text()
    assert b.index('Intro B.') < b.index(f'%% summary {rid} %%') < b.index('\n## Part\n') and '\n## Pump guide\n' in b
    assert (saved['path'], saved['title']) == (str(vault / 'b.md'), 'Pump guide')
    assert store.get(rid)['target'] == {'note': 'b.md', 'place': 'top', 'heading': None, 'level': 2, 'title': 'Pump guide',
                                        'vault': str(vault)}, 'the place saved last is the one "Save again" uses'
    new = client.post(f'/api/research/{rid}/export', json={'target': {'note': '', 'title': 'Pump note'}}).json()
    assert new['path'].endswith('_pump_note.md') and '\n# Pump note\n' in Path(new['path']).read_text()
    assert f'%% summary {rid} %%' in (vault / 'b.md').read_text() and store.get(rid)['target'] == {'title': 'Pump note'}


def test_a_save_into_the_note_that_has_the_summary_puts_it_at_the_place_chosen(client, tmp_path) -> None:
    vault = two_notes(tmp_path)
    rid = finished_summary()
    target = {'note': 'b.md', 'level': 2, 'title': 'Pump guide'}
    client.post(f'/api/research/{rid}/export', json={'target': target | {'place': 'end'}})
    client.post(f'/api/research/{rid}/export', json={'target': target | {'place': 'top'}})
    b = (vault / 'b.md').read_text()
    assert b.count(f'%% summary {rid} %%') == 1 and b.index(f'%% end of summary {rid} %%') < b.index('\n## Part\n')
    client.post(f'/api/research/{rid}/export')
    assert (vault / 'b.md').read_text() == b, '"Save again" writes the same place'


def test_a_save_after_a_heading_needs_the_heading(client, tmp_path) -> None:
    two_notes(tmp_path)
    rid = finished_summary()
    answer = client.post(f'/api/research/{rid}/export', json={'target': {'note': 'b.md', 'place': 'after', 'level': 2}})
    assert answer.status_code == 400 and 'heading' in answer.json()['detail']


def test_each_save_takes_the_title_the_user_chooses(client, monkeypatch, tmp_path) -> None:
    """The note's name came from the research's title, and every save wrote over that one note: the user chooses the title
    at each save. The same title writes that note again; a new title makes a new note and the earlier one stays."""
    vault = tmp_path / 'vault'
    vault.mkdir()
    store.save_settings(vault=str(vault), subdir='Research')
    rid = store.create('A pump for the pit', 150, ['BE'])
    store.update(rid, state='done', title='Pit pump', result={'summary': 'Fits.', 'ranking': []})
    one = client.post(f'/api/research/{rid}/export', json={'title': 'Pump one'}).json()
    first = vault / 'Research' / one['path'].rsplit('/', 1)[1]
    assert first.name.endswith('_pump_one.md') and '\n# Pump one\n' in first.read_text() and one['title'] == 'Pump one'
    first.write_text(first.read_text() + 'Mine.\n')
    again = client.post(f'/api/research/{rid}/export', json={'title': 'Pump one'}).json()
    assert again['path'] == one['path'] and first.read_text().rstrip().endswith('Mine.'), 'the same title: the same note'
    two = client.post(f'/api/research/{rid}/export', json={'title': 'Pump two'}).json()
    assert two['path'].endswith('_pump_two.md') and first.exists(), 'a new title: a new note; the earlier one stays'
    assert store.get(rid)['title'] == 'Pit pump', "the research's own title does not change"
    monkeypatch.setattr(run, 'advance', lambda rid: None)
    api._save_note(rid)
    assert store.get(rid)['result']['note']['path'] == two['path'], 'a save after a run uses the title saved last'


def test_a_title_another_note_has_is_refused(client, tmp_path) -> None:
    vault = tmp_path / 'vault'
    (vault / 'Research').mkdir(parents=True)
    store.save_settings(vault=str(vault), subdir='Research')
    rid = store.create('A pump', 150, ['BE'])
    store.update(rid, state='done', result={'summary': 'Fits.', 'ranking': []})
    taken = vault / 'Research' / f'{store.get(rid)["created"][:10]}_my_pump.md'
    taken.write_text('My own note.\n')
    answer = client.post(f'/api/research/{rid}/export', json={'title': 'My pump'})
    assert answer.status_code == 400 and 'Choose another title' in answer.json()['detail']
    assert taken.read_text() == 'My own note.\n'
    blank = client.post(f'/api/research/{rid}/export', json={'title': '  '})
    assert blank.status_code == 400 and 'title' in blank.json()['detail']


def test_a_summary_starts_without_websites(client, monkeypatch) -> None:
    """Without websites the summary searches the web for the subject."""
    ran = []
    monkeypatch.setattr(run, 'advance', lambda rid: ran.append(rid))
    made = client.post('/api/research/summary', json={'subject': 'Flat roofs', 'sites': ['', ' ']}).json()
    assert (made['plan'], made['state'], ran) == ([], 'running', [made['id']])
