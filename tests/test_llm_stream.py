"""An answer streamed from OpenRouter: the event lines, the text, the cost, the sources and the errors (never the real site)."""

import json

import httpx
import pytest
import respx

from automation_desk import jobs, llm

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'
GENERATION = 'https://openrouter.ai/api/v1/generation'
BODY = {'model': 'openai/gpt-6-luna', 'stream': True,
        'messages': [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'Q'}]}


def chunk(**fields: object) -> str:
    """One event line of the stream."""
    return f'data: {json.dumps({"id": "gen-1", "model": "openai/gpt-6-luna", "provider": "OpenAI"} | fields)}'


def stream(*lines: str) -> httpx.Response:
    """A streamed answer of these lines."""
    return httpx.Response(200, text='\n'.join([*lines, '']), headers={'content-type': 'text/event-stream'})


CITE = {'type': 'url_citation', 'url_citation': {'url': 'https://nih.gov/d', 'title': 'Vitamin D', 'content': 'c'}}
ANSWER = [': OPENROUTER PROCESSING', '', chunk(choices=[{'index': 0, 'delta': {'role': 'assistant', 'content': 'Hel'}}]), '',
          chunk(choices=[{'index': 0, 'delta': {'content': 'lo', 'annotations': [CITE, CITE]}}]),
          chunk(choices=[{'index': 0, 'delta': {'content': ''}, 'finish_reason': 'stop'}]),
          chunk(choices=[{'index': 0, 'delta': {'content': ''}, 'finish_reason': 'stop'}],
                usage={'prompt_tokens': 5, 'completion_tokens': 2, 'cost': 0.0001}), '', 'data: [DONE]']


def read_all(response: httpx.Response) -> tuple[list[str], dict, int]:
    """The pieces of text, the answer folded together, and the number of keep-alive lines."""
    answer: dict = {}
    pieces, alive = [], 0
    for event in llm.sse_events(response.iter_lines()):
        if not event:
            alive += 1
        elif piece := llm.read_chunk(event, answer):
            pieces.append(piece)
    return pieces, answer, alive


@respx.mock
def test_the_answer_comes_in_pieces_with_its_cost_and_sources(monkeypatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    respx.post(COMPLETIONS).mock(return_value=stream(*ANSWER))
    with httpx.Client() as http:
        response = llm.open_stream(BODY, http)
        try:
            pieces, answer, alive = read_all(response)
        finally:
            response.close()
    assert pieces == ['Hel', 'lo'] and alive == 1
    assert answer == {'id': 'gen-1', 'model': 'openai/gpt-6-luna', 'provider': 'OpenAI', 'text': 'Hello', 'finish_reason': 'stop',
                      'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'cost': 0.0001},
                      'sources': [{'url': 'https://nih.gov/d', 'title': 'Vitamin D'}]}


def test_an_error_in_the_stream_is_kept_with_the_text() -> None:
    answer: dict = {}
    lines = [chunk(choices=[{'delta': {'content': 'Part'}}]),
             'data: ' + json.dumps({'error': {'code': 'server_error', 'message': 'Provider went away'},
                                    'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': 'error'}]})]
    for event in llm.sse_events(lines):
        llm.read_chunk(event, answer)
    assert (answer['text'], answer['error'], answer['finish_reason']) == ('Part', 'Provider went away', 'error')


@respx.mock
def test_the_rate_limit_is_waited_for_and_said(monkeypatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(llm.time, 'sleep', lambda s: None)
    respx.post(COMPLETIONS).mock(side_effect=[httpx.Response(429, headers={'retry-after': '3'}, text='slow down'), stream(*ANSWER)])
    waits = []
    with httpx.Client() as http:
        llm.open_stream(BODY, http, on_wait=waits.append).close()
    assert waits == [3]


@respx.mock
def test_a_server_error_before_the_answer_is_tried_again(monkeypatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(llm.time, 'sleep', lambda s: None)
    route = respx.post(COMPLETIONS).mock(side_effect=[httpx.Response(502), httpx.ConnectError('down'), stream(*ANSWER)])
    with httpx.Client() as http:
        llm.open_stream(BODY, http).close()
    assert route.call_count == 3


@respx.mock
@pytest.mark.parametrize('status, says', [(401, 'OPENROUTER_API_KEY'), (402, 'openrouter.ai/settings/credits'),
                                          (400, 'OpenRouter returned 400: bad model')])
def test_a_refusal_says_what_to_do(monkeypatch, status: int, says: str) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(status, text='bad model'))
    with httpx.Client() as http, pytest.raises(llm.LLMError, match=says):
        llm.open_stream(BODY, http)
    assert route.call_count == 1


@respx.mock
def test_the_cost_of_a_stopped_answer_is_asked_until_openrouter_has_it(monkeypatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(llm.time, 'sleep', lambda s: None)
    route = respx.get(GENERATION).mock(side_effect=[httpx.Response(404), httpx.Response(200, json={'data': {'total_cost': 0.0042}})])
    with httpx.Client() as http:
        assert llm.generation_cost('gen-1', http) == 0.0042
    assert route.calls[0].request.url.params['id'] == 'gen-1'
    respx.get(GENERATION).mock(return_value=httpx.Response(404))
    with httpx.Client() as http:
        assert llm.generation_cost('gen-2', http, waits=(1,)) is None


def test_a_streamed_call_is_booked_on_the_job_with_its_cost() -> None:
    job = jobs.Job(group='chat', sentence='Q')
    answer = {'id': 'gen-1', 'model': 'openai/gpt-6-luna', 'provider': 'OpenAI', 'text': 'Hello', 'finish_reason': 'stop',
              'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'cost': 0.0001}}
    with jobs.run(job):
        llm.record_stream('chat answer', BODY, answer, started=0.0)
    [call] = job.llm_calls
    assert (call.purpose, call.model_used, call.provider, call.cost_usd, call.reply, call.user, call.generation_id) == (
        'chat answer', 'openai/gpt-6-luna', 'OpenAI', 0.0001, 'Hello', 'Q', 'gen-1')
    assert call.prompt_tokens == 5 and call.completion_tokens == 2 and call.finish_reason == 'stop'
