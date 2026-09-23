"""The model call: strict schemas, forgiving parsing, one retry with the error shown."""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from automation_desk import jobs, llm
from automation_desk.groups.calendar.web_page import FoundEvent
from automation_desk.groups.tasks.tasks.change_dates import ChangeDatesArgs

COMPLETIONS = 'https://openrouter.ai/api/v1/chat/completions'


class Pair(BaseModel):
    a: int
    b: str = 'x'


def reply(content: str) -> httpx.Response:
    """An OpenRouter completion response carrying `content`."""
    return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})


def test_strict_schema_keeps_fields_named_title_and_requires_all() -> None:
    schema = llm.strict_schema(FoundEvent)
    assert 'title' in schema['properties'] and 'title' in schema['required']
    assert schema['additionalProperties'] is False
    assert '$ref' not in json.dumps(llm.strict_schema(ChangeDatesArgs))


@pytest.mark.parametrize('text', ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1, } thanks'])
def test_coerce_ladder(text: str) -> None:
    assert llm.coerce(text, Pair) == Pair(a=1)


@respx.mock
def test_ask_retries_once_with_the_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    route = respx.post(COMPLETIONS).mock(side_effect=[reply('not json'), reply('{"a": 2, "b": "y"}')])
    assert llm.ask('sys', 'user', Pair) == Pair(a=2, b='y')
    second = json.loads(route.calls[1].request.content)
    assert 'invalid' in second['messages'][-1]['content']
    assert second['response_format']['json_schema']['strict'] is True


@respx.mock
def test_ask_does_not_retry_a_rejected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'bad')
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(401, text='no'))
    with pytest.raises(llm.LLMError, match='401'):
        llm.ask('sys', 'user', Pair)
    assert route.call_count == 1


@respx.mock
def test_every_call_is_recorded_with_model_provider_tokens_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    answered = {'id': 'gen-1', 'model': 'google/gemini-3.1-flash-lite', 'provider': 'Google',
                'usage': {'prompt_tokens': 120, 'completion_tokens': 30, 'cost': 0.000075},
                'choices': [{'message': {'content': '{"a": 5}'}, 'finish_reason': 'stop'}]}
    route = respx.post(COMPLETIONS).mock(side_effect=[reply('garbage'), httpx.Response(200, json=answered)])
    with jobs.run(jobs.Job(group='tasks', sentence='s')) as job:
        llm.ask('system text', 'user text', Pair, purpose='test purpose')
    assert json.loads(route.calls[0].request.content)['usage'] == {'include': True}
    failed, ok = job.llm_calls
    assert failed.error.startswith('unparseable') and failed.purpose == 'test purpose'
    assert (ok.model_used, ok.provider, ok.prompt_tokens, ok.completion_tokens, ok.cost_usd, ok.generation_id) == (
        'google/gemini-3.1-flash-lite', 'Google', 120, 30, 0.000075, 'gen-1')
    assert (ok.system, ok.reply) == ('system text', '{"a": 5}') and 'user text' in ok.user
    assert jobs.load()[0]['llm_calls'][1]['cost_usd'] == 0.000075
    sent = json.loads(route.calls[1].request.content)
    assert ok.request == sent, 'the exact body that went to OpenRouter, retry messages included'
    assert ok.response == answered and 'Authorization' not in json.dumps(ok.request)
    assert failed.request['messages'] != ok.request['messages'], 'each attempt keeps its own messages'
    assert job.summary()['cost_usd'] == 0.000075


@respx.mock
def test_a_call_outside_any_job_is_still_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    answered = {'id': 'gen-9', 'model': 'm', 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'cost': 0.00001},
                'choices': [{'message': {'content': '{"a": 1}'}, 'finish_reason': 'stop'}]}
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=answered))
    llm.ask('s', 'u', Pair, purpose='a check')
    stored = jobs.load()
    assert [(j['group'], j['sentence'], j['llm_calls'][0]['generation_id']) for j in stored] == [
        (jobs.OUTSIDE_APP, 'a check', 'gen-9')]


def test_the_suite_can_never_reach_openrouter_for_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    with pytest.raises(AssertionError, match='call OpenRouter for real'):
        llm.ask('s', 'u', Pair)
