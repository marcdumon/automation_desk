"""The models of the Chat page: a short list of good models first, a search over every OpenRouter model, and what an
answer costs: the average of the user's own answers with that model, else an estimate from the list price."""

import logging
import time

import httpx

from automation_desk.chat import store

log = logging.getLogger(__name__)

CATALOGUE_URL = 'https://openrouter.ai/api/v1/models'
SHORT_LIST = ['openai/gpt-6-luna', 'openai/gpt-6.1-sol', 'google/gemini-3.1-flash-lite', 'google/gemini-3.1-pro-preview',
              'anthropic/claude-sonnet-5.5', 'anthropic/claude-opus-5.5']
# CLAUDE> the spike of 2026-10-05: good answers on a health question for about $0.007
FIRST_MODEL = 'openai/gpt-6.1-sol'
KEEP_S = 6 * 3600
# CLAUDE> an answer of about 1,000 words, with the hidden thinking of most models, against a short question
TOKENS_IN, TOKENS_OUT = 1000, 2000
# CLAUDE> measured 2026-10-05: OpenRouter's Exa fee was $0.007 for 5 pages, and the model read about 5,000 tokens of page text
WEB_FEE_USD = 0.007
WEB_TOKENS = 5000
SEARCH_LIMIT = 40
_cache: dict = {'at': 0.0, 'models': []}


def _per_m(price: object) -> float | None:
    """A price per token as dollars per million tokens; None for a model without a fixed price."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    return round(value * 1_000_000, 4) if value >= 0 else None


def catalogue(http: httpx.Client | None = None, now: float | None = None) -> list[dict]:
    """Every OpenRouter model (id, name, prices per million tokens), read at most once in six hours; the list read before
    when OpenRouter cannot be reached."""
    now = time.time() if now is None else now
    if _cache['models'] and now - _cache['at'] < KEEP_S:
        return _cache['models']
    client = http or httpx.Client(timeout=20.0)
    try:
        data = client.get(CATALOGUE_URL).raise_for_status().json()['data']
        _cache['models'] = [{'id': m['id'], 'name': m.get('name') or m['id'],
                             'in_per_m': _per_m((m.get('pricing') or {}).get('prompt')),
                             'out_per_m': _per_m((m.get('pricing') or {}).get('completion'))} for m in data]
        _cache['at'] = now
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        log.warning('OpenRouter model list: %s', error)
    finally:
        if http is None:
            client.close()
    return _cache['models']


def _with_cost(model: dict, costs: dict[str, float]) -> dict:
    """A model with what an answer costs (measured when the user has answers of it, else estimated) and what web search
    adds to it: the search fee and the page text the model reads."""
    web = None if model['in_per_m'] is None else round(WEB_FEE_USD + WEB_TOKENS * model['in_per_m'] / 1_000_000, 4)
    if model['id'] in costs:
        return model | {'per_answer': costs[model['id']], 'measured': True, 'web_extra': web}
    if model['in_per_m'] is None or model['out_per_m'] is None:
        return model | {'per_answer': None, 'measured': False, 'web_extra': web}
    estimate = (TOKENS_IN * model['in_per_m'] + TOKENS_OUT * model['out_per_m']) / 1_000_000
    return model | {'per_answer': round(estimate, 4), 'measured': False, 'web_extra': web}


def describe(model_id: str, http: httpx.Client | None = None) -> dict:
    """One model with its prices and cost per answer; without prices when OpenRouter's list does not hold it."""
    found = next((m for m in catalogue(http) if m['id'] == model_id), None)
    return _with_cost(found or {'id': model_id, 'name': model_id, 'in_per_m': None, 'out_per_m': None}, store.model_costs())


def short_list(http: httpx.Client | None = None) -> list[dict]:
    """The short list, in its order."""
    return [describe(model_id, http) for model_id in SHORT_LIST]


def search(query: str, http: httpx.Client | None = None) -> list[dict]:
    """The OpenRouter models whose id or name holds every word of the query, in OpenRouter's order (newest first)."""
    words = query.casefold().split()
    costs = store.model_costs()
    found = [m for m in catalogue(http) if all(w in f'{m["id"]} {m["name"]}'.casefold() for w in words)]
    return [_with_cost(m, costs) for m in found[:SEARCH_LIMIT]]


def new_model() -> str:
    """The model of a new chat: that of the chat changed last, else the first choice."""
    return store.last_model() or FIRST_MODEL
