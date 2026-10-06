"""The Chat routes over HTTP, with a stand-in OpenRouter: new chats, questions, second opinions, Try again, choosing,
following an answer, renaming, deleting and saving to Obsidian (a temporary vault)."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from automation_desk import api, llm
from automation_desk.chat import models, store, talk

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'
LUNA = {'id': 'openai/gpt-6-luna', 'name': 'OpenAI: GPT-6 Luna', 'in_per_m': 0.1, 'out_per_m': 0.5}


def stream(text: str, model: str = 'openai/gpt-6-luna') -> httpx.Response:
    """A streamed answer of one piece and its cost."""
    head = {'id': 'gen-1', 'model': model, 'provider': 'OpenAI'}
    lines = [f'data: {json.dumps(head | {"choices": [{"delta": {"content": text}}]})}',
             f'data: {json.dumps(head | {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"cost": 0.001}})}',
             'data: [DONE]', '']
    return httpx.Response(200, text='\n'.join(lines))


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """A client whose answers are written at once, in the request, from the stand-in."""
    real = talk.start
    monkeypatch.setattr(talk, 'start', lambda chat_id, model=None, replace=None, background=True: real(chat_id, model, replace, False))
    monkeypatch.setattr(talk, '_live', {})
    monkeypatch.setattr(models, '_cache', {'at': 9e18, 'models': [LUNA]})
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(llm.time, 'sleep', lambda s: None)
    return TestClient(api.app)


@respx.mock
def test_a_new_chat_is_answered_and_listed(client) -> None:
    route = respx.post(COMPLETIONS).mock(return_value=stream('Take vitamin D.'))
    chat = client.post('/api/chat', json={'text': ' Supplements at 65? ', 'model': 'openai/gpt-6-luna', 'web': True}).json()
    assert [(m['role'], m['content'], m['state']) for m in chat['messages']] == [
        ('user', 'Supplements at 65?', 'done'), ('assistant', 'Take vitamin D.', 'done')]
    assert chat['title'] == 'Supplements at 65?' and chat['cost_usd'] == 0.001 and chat['writing'] is False
    assert chat['model_info']['name'] == 'OpenAI: GPT-6 Luna', 'the page names the model, also one from the search'
    assert 'plugins' in json.loads(route.calls[0].request.content)
    listing = client.get('/api/chat').json()
    assert [c['id'] for c in listing['chats']] == [chat['id']] and listing['new_model'] == 'openai/gpt-6-luna'
    assert listing['new_model_info']['name'] == 'OpenAI: GPT-6 Luna'
    assert listing['models'][0]['id'] == 'openai/gpt-6-luna' and listing['web_extra'] == models.WEB_FEE_USD
    assert set(listing['settings']) == {'vault', 'subdir'}


def test_an_empty_question_is_refused(client) -> None:
    assert client.post('/api/chat', json={'text': '  '}).status_code == 400
    chat_id = store.create('Q', 'm', web=False)
    assert client.post(f'/api/chat/{chat_id}/questions', json={'text': ''}).status_code == 400


@respx.mock
def test_the_next_question_second_opinion_choice_and_try_again(client) -> None:
    route = respx.post(COMPLETIONS).mock(side_effect=[stream('A1'), stream('A2'), stream('Other', 'm2'), stream('A2 again')])
    chat_id = client.post('/api/chat', json={'text': 'Q1', 'model': 'm1'}).json()['id']
    chat = client.post(f'/api/chat/{chat_id}/questions', json={'text': 'Q2', 'web': False, 'model': 'm3'}).json()
    assert chat['model'] == 'm3' and json.loads(route.calls[1].request.content)['model'] == 'm3'
    chat = client.post(f'/api/chat/{chat_id}/answers', json={'model': 'm2'}).json()
    second = chat['messages'][-1]
    assert (second['turn'], second['model'], second['chosen']) == (2, 'm2', False)
    chat = client.post(f'/api/chat/messages/{second["id"]}/choose').json()
    answers = [m for m in chat['messages'] if m['turn'] == 2 and m['role'] == 'assistant']
    assert chat['model'] == 'm2' and [m['chosen'] for m in answers] == [False, True]
    first = chat['messages'][-2]
    chat = client.post(f'/api/chat/{chat_id}/answers', json={'replace': first['id']}).json()
    assert [m['content'] for m in chat['messages'] if m['turn'] == 2 and m['role'] == 'assistant'] == ['Other', 'A2 again']


def test_a_question_waits_for_the_answer_being_written(client) -> None:
    chat_id = store.create('Q', 'm', web=False)
    store.add_answer(chat_id, 1, 'm', chosen=True)
    answer = client.post(f'/api/chat/{chat_id}/questions', json={'text': 'Q2'})
    assert answer.status_code == 409 and 'Stop' in answer.json()['detail']
    assert len(store.get(chat_id)['messages']) == 2, 'the question is not added'
    assert client.delete(f'/api/chat/{chat_id}').status_code == 409


def test_another_chats_answer_cannot_be_asked_again_or_chosen(client) -> None:
    mine, other = store.create('Q', 'm', web=False), store.create('Q', 'm', web=False)
    question = store.get(other)['messages'][0]
    assert client.post(f'/api/chat/{mine}/answers', json={'replace': question['id']}).status_code == 400
    assert client.post(f'/api/chat/messages/{question["id"]}/choose').status_code == 400
    assert client.post('/api/chat/messages/999/choose').status_code == 404


def test_following_an_answer_streams_its_events(client) -> None:
    chat_id = store.create('Q', 'm', web=False)
    assert client.get(f'/api/chat/{chat_id}/follow').text == '', 'no answer is being written'
    events = [{'type': 'start', 'message': {}}, {'type': 'piece', 'text': 'Hi'}, {'type': 'end', 'message': {}}]
    talk._live[chat_id] = talk.Live(chat_id, 1, events, done=False)
    with client.stream('GET', f'/api/chat/{chat_id}/follow') as response:
        assert response.headers['content-type'].startswith('text/event-stream')
        got = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith('data: ')]
    assert got == events


def test_rename_change_model_stop_and_delete(client) -> None:
    chat_id = store.create('Q', 'm', web=False)
    assert client.patch(f'/api/chat/{chat_id}', json={'title': '  Pump  chat '}).json()['title'] == 'Pump chat'
    assert client.patch(f'/api/chat/{chat_id}', json={'title': ' '}).status_code == 400
    opus = 'anthropic/claude-opus-5.5'
    assert client.patch(f'/api/chat/{chat_id}', json={'model': opus}).json()['model'] == opus
    assert client.post(f'/api/chat/{chat_id}/stop').status_code == 200
    assert client.delete(f'/api/chat/{chat_id}').json() == {'deleted': True}
    assert client.get(f'/api/chat/{chat_id}').status_code == 404


def test_the_models_are_searched(client) -> None:
    assert [m['id'] for m in client.get('/api/chat/models', params={'q': 'luna'}).json()['models']] == ['openai/gpt-6-luna']


@respx.mock
def test_a_chat_is_saved_as_a_note_then_into_a_note_i_have(client, tmp_path) -> None:
    respx.post(COMPLETIONS).mock(return_value=stream('Take vitamin D.'))
    vault = tmp_path / 'vault'
    (vault / 'Health').mkdir(parents=True)
    (vault / 'Health' / 'me.md').write_text('\n'.join(['# Me', '## Doctor', 'Text.', '']))
    client.post('/api/research/settings', json={'vault': str(vault), 'subdir': 'Research'})
    chat_id = client.post('/api/chat', json={'text': 'Supplements?', 'model': 'm1'}).json()['id']
    note = client.post(f'/api/chat/{chat_id}/export', json={'target': {'title': 'Vitamins'}}).json()
    assert note['path'].endswith('_vitamins.md') and note['title'] == 'Vitamins' and note['error'] == ''
    assert client.get(f'/api/chat/{chat_id}').json()['note']['path'] == note['path']
    target = {'note': 'Health/me.md', 'place': 'after', 'heading': {'level': 2, 'text': 'Doctor'}, 'level': 2, 'title': 'Vitamins'}
    into = client.post(f'/api/chat/{chat_id}/export', json={'target': target}).json()
    text = (vault / 'Health' / 'me.md').read_text()
    assert into['path'] == str(vault / 'Health' / 'me.md') and '%% chat' in text and text.index('Text.') < text.index('## Vitamins')
    again = client.post(f'/api/chat/{chat_id}/export', json={}).json()
    assert again['path'] == into['path'] and (vault / 'Health' / 'me.md').read_text() == text, 'Save again: the same place'
    headings = client.get('/api/research/notes/headings', params={'path': 'Health/me.md', 'skip': f'chat {chat_id}'}).json()
    assert [h['text'] for h in headings['headings']] == ['Me', 'Doctor']
