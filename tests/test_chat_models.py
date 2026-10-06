"""The model list of the Chat page: the short list with what an answer costs, the search over every OpenRouter model, and
the model of a new chat (OpenRouter's list is a stand-in)."""

import httpx
import pytest
import respx

from automation_desk.chat import models, store

CATALOGUE = {'data': [
    {'id': 'openai/gpt-6-luna', 'name': 'OpenAI: GPT-6 Luna', 'context_length': 1050000,
     'pricing': {'prompt': '0.0000001', 'completion': '0.0000005'}},
    {'id': 'anthropic/claude-opus-5.5', 'name': 'Anthropic: Claude Opus 5.5', 'context_length': 1000000,
     'pricing': {'prompt': '0.000004', 'completion': '0.00002'}},
    {'id': 'mistralai/mistral-large-3', 'name': 'Mistral: Large 3', 'context_length': 128000,
     'pricing': {'prompt': '0.000002', 'completion': '0.000006'}},
    {'id': 'openrouter/auto', 'name': 'Auto Router', 'context_length': 2000000, 'pricing': {'prompt': '-1', 'completion': '-1'}}]}


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch) -> None:
    """Every test reads the list afresh."""
    monkeypatch.setattr(models, '_cache', {'at': 0.0, 'models': []})


@respx.mock
def test_the_short_list_comes_first_with_the_cost_of_an_answer() -> None:
    respx.get(models.CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    listed = models.short_list()
    assert [m['id'] for m in listed] == models.SHORT_LIST
    luna = listed[0]
    assert luna == {'id': 'openai/gpt-6-luna', 'name': 'OpenAI: GPT-6 Luna', 'in_per_m': 0.1, 'out_per_m': 0.5,
                    'per_answer': 0.0011, 'measured': False, 'web_extra': 0.0075}
    opus = next(m for m in listed if m['id'] == 'anthropic/claude-opus-5.5')
    assert (opus['per_answer'], opus['web_extra']) == (0.044, 0.027), 'web search: the fee and 5,000 tokens of page text'
    sol = next(m for m in listed if m['id'] == 'openai/gpt-6.1-sol')
    assert sol == {'id': 'openai/gpt-6.1-sol', 'name': 'openai/gpt-6.1-sol', 'in_per_m': None, 'out_per_m': None,
                   'per_answer': None, 'measured': False, 'web_extra': None}, 'a model not on the list shows, without a price'


@respx.mock
def test_the_cost_of_your_own_answers_replaces_the_estimate() -> None:
    respx.get(models.CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    chat_id = store.create('Q', 'openai/gpt-6-luna', web=False)
    mid = store.add_answer(chat_id, 1, 'openai/gpt-6-luna', chosen=True)
    store.update_message(mid, state='done', cost_usd=0.0004)
    luna = models.short_list()[0]
    assert (luna['per_answer'], luna['measured']) == (0.0004, True)


@respx.mock
def test_every_model_can_be_found_by_words_of_its_name_or_id() -> None:
    respx.get(models.CATALOGUE_URL).mock(return_value=httpx.Response(200, json=CATALOGUE))
    assert [m['id'] for m in models.search('mistral LARGE')] == ['mistralai/mistral-large-3']
    assert [m['id'] for m in models.search('')] == [m['id'] for m in CATALOGUE['data']]
    assert models.search('auto')[0]['per_answer'] is None, 'a model without a fixed price has no estimate'


@respx.mock
def test_the_list_is_read_once_in_six_hours_and_kept_when_openrouter_fails() -> None:
    route = respx.get(models.CATALOGUE_URL).mock(side_effect=[httpx.Response(200, json=CATALOGUE), httpx.Response(503)])
    assert len(models.catalogue(now=1000.0)) == 4
    assert len(models.catalogue(now=1000.0 + 3600)) == 4 and route.call_count == 1
    assert len(models.catalogue(now=1000.0 + 7 * 3600)) == 4 and route.call_count == 2, 'the old list stays'


def test_a_new_chat_takes_the_model_of_the_last_chat() -> None:
    assert models.new_model() == models.FIRST_MODEL
    store.create('Q', 'anthropic/claude-opus-5.5', web=False)
    assert models.new_model() == 'anthropic/claude-opus-5.5'
