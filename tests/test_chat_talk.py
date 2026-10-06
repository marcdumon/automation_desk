"""Answers in a chat, written from a stand-in OpenRouter stream: the pieces, the cost on the job, second opinions, Try
again, Stop, errors, and one answer at a time."""

import asyncio
import json
from datetime import date

import httpx
import pytest
import respx

from automation_desk import ledger, llm
from automation_desk.chat import store, talk

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'
GENERATION = 'https://openrouter.ai/api/v1/generation'
CITE = {'type': 'url_citation', 'url_citation': {'url': 'https://nih.gov/d', 'title': 'Vitamin D'}}


def chunk(model: str = 'openai/gpt-6-luna', **fields: object) -> str:
    """One event line of the stream."""
    return f'data: {json.dumps({"id": "gen-1", "model": model, "provider": "OpenAI"} | fields)}'


def stream(*texts: str, finish: str = 'stop', cost: float | None = 0.0001, sources: bool = False, tail: list | None = None,
           model: str = 'openai/gpt-6-luna') -> httpx.Response:
    """A streamed answer of these pieces, then its end and its usage (none when `cost` is None)."""
    lines = [': OPENROUTER PROCESSING']
    lines += [chunk(model, choices=[{'delta': {'content': t, **({'annotations': [CITE]} if sources else {})}}]) for t in texts]
    lines += tail or []
    if finish:
        lines.append(chunk(model, choices=[{'delta': {'content': ''}, 'finish_reason': finish}]))
    if cost is not None:
        lines.append(chunk(model, choices=[{'delta': {}, 'finish_reason': finish}],
                           usage={'prompt_tokens': 9, 'completion_tokens': 3, 'cost': cost}))
    return httpx.Response(200, text='\n'.join([*lines, 'data: [DONE]', '']))


@pytest.fixture(autouse=True)
def openrouter(monkeypatch) -> None:
    """A key for the stand-in, no real waits, and no answer left from another test."""
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(llm.time, 'sleep', lambda s: None)
    monkeypatch.setattr(talk, '_live', {})
    monkeypatch.setattr(talk, '_today', lambda: date(2026, 10, 5))


def sent(route: respx.Route, n: int = -1) -> dict:
    """The body of a request the stand-in got."""
    return json.loads(route.calls[n].request.content)


def booked() -> list[dict]:
    """The jobs in the ledger."""
    return ledger.all_jobs()


@respx.mock
def test_an_answer_is_written_piece_by_piece_and_booked() -> None:
    route = respx.post(COMPLETIONS).mock(return_value=stream('Take ', 'vitamin D.'))
    chat_id = store.create('Supplements for a 65 year old man?', 'openai/gpt-6-luna', web=False)
    live = talk.start(chat_id, background=False)
    assert [e['type'] for e in live.events] == ['start', 'piece', 'piece', 'end'] and live.done
    answer = live.events[-1]['message']
    assert (answer['content'], answer['state'], answer['cost_usd'], answer['chosen'], answer['model']) == (
        'Take vitamin D.', 'done', 0.0001, True, 'openai/gpt-6-luna')
    body = sent(route)
    assert body['stream'] is True and body['usage'] == {'include': True} and 'plugins' not in body
    assert body['messages'] == [{'role': 'system', 'content': 'You are a helpful assistant. Today is 5 October 2026.'},
                                {'role': 'user', 'content': 'Supplements for a 65 year old man?'}]
    [job] = booked()
    assert (job['group'], job['task_name'], job['sentence'], job['cost_usd'], job['status']) == (
        'chat', 'Chat answer', 'Supplements for a 65 year old man?', 0.0001, 'ok')
    assert answer['job_id'] == job['id'] and job['llm_calls'][0]['reply'] == 'Take vitamin D.'


@respx.mock
def test_a_question_with_web_search_sends_the_search_and_keeps_the_sources() -> None:
    route = respx.post(COMPLETIONS).mock(return_value=stream('See NIH.', sources=True))
    chat_id = store.create('Vitamin D dose?', 'anthropic/claude-sonnet-5.5', web=True)
    answer = talk.start(chat_id, background=False).events[-1]['message']
    assert sent(route)['plugins'] == [{'id': 'web', 'engine': 'exa', 'max_results': 5}]
    assert answer['sources'] == [{'url': 'https://nih.gov/d', 'title': 'Vitamin D'}]


@respx.mock
def test_a_second_opinion_gets_the_same_history_and_does_not_replace_the_chosen_answer() -> None:
    route = respx.post(COMPLETIONS).mock(side_effect=[stream('First.'), stream('Second.'), stream('Other view.', model='m2')])
    chat_id = store.create('Q1', 'm1', web=False)
    talk.start(chat_id, background=False)
    store.add_question(chat_id, 'Q2', web=False)
    talk.start(chat_id, background=False)
    other = talk.start(chat_id, model='m2', background=False).events[-1]['message']
    assert (other['turn'], other['model'], other['chosen'], other['content']) == (2, 'm2', False, 'Other view.')
    assert sent(route, 2)['messages'][1:] == sent(route, 1)['messages'][1:] == [
        {'role': 'user', 'content': 'Q1'}, {'role': 'assistant', 'content': 'First.'}, {'role': 'user', 'content': 'Q2'}]
    assert sent(route, 2)['model'] == 'm2' and store.get(chat_id)['model'] == 'm1', 'the chat goes on with its own model'
    assert {j['id']: j['task_name'] for j in booked()}[other['job_id']] == 'Second opinion' and len(booked()) == 3


@respx.mock
def test_try_again_replaces_a_failed_answer_with_the_same_model_and_keeps_it_chosen() -> None:
    respx.post(COMPLETIONS).mock(side_effect=[httpx.Response(402, text='no credit'), stream('Now it works.')])
    chat_id = store.create('Q', 'm1', web=False)
    failed = talk.start(chat_id, background=False).events[-1]['message']
    assert (failed['state'], failed['error']) == ('failed', llm.NO_CREDIT)
    assert booked()[0]['status'] == 'error'
    store.update(chat_id, model='m9')
    again = talk.start(chat_id, replace=failed['id'], background=False).events[-1]['message']
    assert (again['content'], again['model'], again['chosen']) == ('Now it works.', 'm1', True)
    assert store.message(failed['id']) is None and len(store.get(chat_id)['messages']) == 2


@respx.mock
def test_stop_keeps_the_text_and_asks_openrouter_what_it_cost(monkeypatch) -> None:
    respx.post(COMPLETIONS).mock(return_value=stream('Part one. ', 'Part two.', cost=None, finish=''))
    respx.get(GENERATION).mock(side_effect=[httpx.Response(404), httpx.Response(200, json={'data': {'total_cost': 0.0042}})])
    chat_id = store.create('Q', 'm1', web=False)
    events = llm.sse_events

    def stop_after_first_piece(lines):
        """The user presses Stop when the first piece is on the page."""
        for event in events(lines):
            yield event
            if event.get('choices'):
                talk.stop(chat_id)

    monkeypatch.setattr(llm, 'sse_events', stop_after_first_piece)
    answer = talk.start(chat_id, background=False).events[-1]['message']
    assert (answer['state'], answer['content']) == ('stopped', 'Part one. ')
    assert store.message(answer['id'])['cost_usd'] == 0.0042 and booked()[0]['cost_usd'] == 0.0042


@respx.mock
def test_an_error_in_the_middle_keeps_the_text_with_the_reason() -> None:
    error = 'data: ' + json.dumps({'error': {'message': 'Provider went away'}, 'choices': [{'delta': {}, 'finish_reason': 'error'}]})
    respx.post(COMPLETIONS).mock(return_value=stream('Half', finish='', cost=None, tail=[error]))
    respx.get(GENERATION).mock(return_value=httpx.Response(200, json={'data': {'total_cost': 0.0003}}))
    chat_id = store.create('Q', 'm1', web=False)
    answer = talk.start(chat_id, background=False).events[-1]['message']
    assert (answer['state'], answer['content'], answer['error']) == ('failed', 'Half', 'Provider went away')
    assert store.message(answer['id'])['cost_usd'] == 0.0003, 'what was written is billed, so it is booked'


@respx.mock
def test_an_answer_cut_off_at_the_limit_says_so() -> None:
    respx.post(COMPLETIONS).mock(return_value=stream('Long', finish='length'))
    chat_id = store.create('Q', 'm1', web=False)
    answer = talk.start(chat_id, background=False).events[-1]['message']
    assert (answer['state'], answer['error']) == ('stopped', talk.CUT_OFF)


def test_only_one_answer_at_a_time_in_a_chat() -> None:
    chat_id = store.create('Q', 'm1', web=False)
    store.add_answer(chat_id, 1, 'm1', chosen=True)
    with pytest.raises(talk.Busy):
        talk.start(chat_id, background=False)


@respx.mock
def test_follow_gives_every_event_of_the_answer_being_written() -> None:
    respx.post(COMPLETIONS).mock(return_value=stream('A', 'B'))
    chat_id = store.create('Q', 'm1', web=False)

    async def collect() -> list[dict]:
        """All events a page that attaches gets."""
        return [event async for event in talk.follow(chat_id)]

    assert asyncio.run(collect()) == [], 'no answer is being written'
    live = talk.start(chat_id, background=False)
    live.done = False
    live.events = live.events[:-1]
    task_events = asyncio.run(_finish_later(live, collect))
    assert [e['type'] for e in task_events] == ['start', 'piece', 'piece', 'end']


async def _finish_later(live: talk.Live, collect) -> list[dict]:
    """Let the answer end a moment after the page attached."""
    async def finish() -> None:
        await asyncio.sleep(0.1)
        live.events.append({'type': 'end', 'message': {}})
        live.done = True

    _, got = await asyncio.gather(finish(), collect())
    return got
