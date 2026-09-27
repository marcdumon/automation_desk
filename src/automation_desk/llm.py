"""The one place a model is called: sentence in, validated pydantic object out.

The model only ever sees the user's sentence (or, for the web import, public page text) and a JSON schema. It never
sees mailbox, calendar or task data. Parsing descends a ladder (strict JSON, fenced block, first balanced object)
because cheap models do not always honour strict mode; copied from fin_research/llm/parsing.py.
"""

import copy
import json
import logging
import re
import threading
import time
from collections import deque

import httpx
from pydantic import BaseModel, ValidationError

from automation_desk import jobs
from automation_desk.config import config

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
RATE_LIMIT_WAITS = 5
RATE_LIMIT_MAX_WAIT_S = 65.0
# CLAUDE> when the recent requests went out, shared by the threads that call the model at once
_SENT: deque[float] = deque()
_PACE = threading.Lock()
# CLAUDE> when a pause for the rate limit ends (time.monotonic), so the page can say why nothing moves
_paused_until = 0.0


class LLMError(RuntimeError):
    """The model could not be reached or gave no usable answer."""


class CutOff(LLMError):
    """The answer was longer than the output limit: asking about less text at once can work."""


def strict_schema(schema: type[BaseModel]) -> dict:
    """A JSON schema strict mode will honour: refs inlined and every property required."""
    raw = schema.model_json_schema()
    defs = raw.pop('$defs', {})
    return _require_all(_inline(raw, defs))


def _inline(node: object, defs: dict) -> object:
    """Replace every `$ref` with the definition it points to; some providers reject refs."""
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if '$ref' in node:
        return _inline(defs[node['$ref'].rsplit('/', 1)[-1]], defs)
    return {key: _inline(value, defs) for key, value in node.items()}


def _require_all(node: object) -> object:
    """Recursively mark every declared property as required and forbid extras."""
    if isinstance(node, list):
        return [_require_all(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _require_all(value) for key, value in node.items() if key not in ('default', 'title', 'properties')}
    if isinstance(node.get('properties'), dict) and node['properties']:
        # CLAUDE> property names are field names, so they are mapped, never filtered like schema keywords
        out['properties'] = {name: _require_all(value) for name, value in node['properties'].items()}
        out['required'] = list(out['properties'])
        out['additionalProperties'] = False
    return out


_FENCED = re.compile(r'```(?:json)?\s*(.+?)```', re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r',\s*([}\]])')


def _balanced_object(text: str) -> str | None:
    """First balanced `{...}` in `text`, ignoring braces inside strings."""
    start = text.find('{')
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for position in range(start, len(text)):
        character = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return text[start:position + 1]
    return None


def coerce[T: BaseModel](text: str, schema: type[T]) -> T:
    """Parse a reply into `schema`, trying progressively more forgiving extractions."""
    candidates = [text.strip()]
    if fenced := _FENCED.search(text):
        candidates.append(fenced.group(1).strip())
    if embedded := _balanced_object(text):
        candidates.append(embedded)

    last_error: Exception | None = None
    for candidate in candidates:
        for attempt in (candidate, _TRAILING_COMMA.sub(r'\1', candidate)):
            try:
                return schema.model_validate(json.loads(attempt))
            except (json.JSONDecodeError, ValidationError) as error:
                last_error = error
    raise ValueError(f'could not parse a {schema.__name__} from the reply: {last_error}')


def pause_left() -> int:
    """Seconds left of a pause for the model's rate limit, 0 when none."""
    return max(round(_paused_until - time.monotonic()), 0)


def _pause(seconds: float) -> None:
    """Sleep for the rate limit, telling pause_left how long."""
    global _paused_until
    _paused_until = time.monotonic() + seconds
    try:
        time.sleep(seconds)
    finally:
        _paused_until = 0.0


def _wait_for_turn(per_minute: int) -> None:
    """Wait until a request fits in the model's requests-per-minute limit, and count it."""
    with _PACE:
        now = time.monotonic()
        while _SENT and now - _SENT[0] >= 60:
            _SENT.popleft()
        if len(_SENT) >= per_minute:
            _pause(60 - (now - _SENT[0]))
            _SENT.popleft()
            now = time.monotonic()
        _SENT.append(now)


def _rate_limit_wait(response: httpx.Response) -> float:
    """Seconds until a rate limit resets: from Retry-After, else from the reset time OpenRouter puts in the error."""
    if seconds := response.headers.get('retry-after', ''):
        return min(max(float(seconds), 1.0), RATE_LIMIT_MAX_WAIT_S)
    try:
        reset_ms = float(response.json()['error']['metadata']['headers']['X-RateLimit-Reset'])
    except (ValueError, KeyError, TypeError):
        return 10.0
    return min(max(reset_ms / 1000 - time.time(), 1.0), RATE_LIMIT_MAX_WAIT_S)


def _post(body: dict, http: httpx.Client) -> dict:
    """Send one completion request, spaced to the rate limit, retrying transient failures with backoff."""
    cfg = config()
    if not cfg.openrouter_key:
        raise LLMError('OPENROUTER_API_KEY is not set; put it in .env.')
    headers = {'Authorization': f'Bearer {cfg.openrouter_key}'}
    attempt = limited = 0
    while True:
        _wait_for_turn(cfg.llm_requests_per_minute)
        try:
            response = http.post(f'{cfg.llm_base_url}/chat/completions', json=body, headers=headers)
        except httpx.TransportError as error:
            if attempt == MAX_ATTEMPTS - 1:
                raise LLMError(f'OpenRouter unreachable: {error}') from error
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429 and limited < RATE_LIMIT_WAITS:
                limited += 1
                _pause(_rate_limit_wait(response))
                continue
            if response.status_code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise LLMError(f'OpenRouter returned {response.status_code}: {response.text[:300]}')
        time.sleep(2.0 * 2 ** attempt)
        attempt += 1


def _record(purpose: str, body: dict, reply: dict | None, started: float, error: str = '') -> None:
    """Put one completion call, with its exact cost as reported by OpenRouter, on the current job."""
    job = jobs.current()
    # CLAUDE> a call outside any job (a script, a check) still costs money: it becomes a job of its own
    standalone = job is None
    if standalone:
        job = jobs.Job(group=jobs.OUTSIDE_APP, sentence=purpose, task_name=purpose)
    reply = reply or {}
    usage = reply.get('usage') or {}
    choice = (reply.get('choices') or [{}])[0]
    messages = body['messages']
    job.llm_calls.append(jobs.LLMCall(
        purpose=purpose, model_requested=body['model'], model_used=reply.get('model', ''),
        provider=reply.get('provider', ''), prompt_tokens=int(usage.get('prompt_tokens') or 0),
        completion_tokens=int(usage.get('completion_tokens') or 0), cost_usd=float(usage.get('cost') or 0.0),
        latency_ms=int((time.monotonic() - started) * 1000), finish_reason=choice.get('finish_reason') or '',
        generation_id=reply.get('id', ''), system=messages[0]['content'],
        user='\n\n'.join(m['content'] for m in messages[1:] if m['role'] == 'user'),
        reply=(choice.get('message') or {}).get('content') or '', error=error,
        request=copy.deepcopy(body), response=reply, stage=jobs.stage()))
    if standalone:
        job.status, job.message = ('error', error) if error else ('ok', '')
        job.preview_ms = job.llm_calls[-1].latency_ms
        jobs.save(job)


def ask[T: BaseModel](system: str, user: str, schema: type[T], http: httpx.Client | None = None, purpose: str = '') -> T:
    """Ask the model for one `schema` object. A reply that doesn't validate is retried once with the error shown.

    Every request is recorded on the current job, including the retry and failed ones.
    """
    purpose = purpose or schema.__name__
    body = {
        'model': config().llm_model,
        'temperature': 0,
        'max_tokens': 8000,
        'usage': {'include': True},
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'response_format': {
            'type': 'json_schema',
            'json_schema': {'name': schema.__name__, 'strict': True, 'schema': strict_schema(schema)},
        },
    }
    client = http or httpx.Client(timeout=120.0)
    try:
        for attempt in range(2):
            started = time.monotonic()
            try:
                reply = _post(body, client)
            except LLMError as error:
                _record(purpose, body, None, started, str(error))
                raise
            choice = reply['choices'][0]
            text = choice['message'].get('content') or ''
            if choice.get('finish_reason') == 'length':
                _record(purpose, body, reply, started, 'cut off at the output limit')
                raise CutOff('The answer was longer than the output limit and got cut off.')
            try:
                parsed = coerce(text, schema)
            except ValueError as error:
                _record(purpose, body, reply, started, f'unparseable: {error}')
                log.warning('unparseable reply (attempt %d): %s', attempt + 1, error)
                body['messages'] = [*body['messages'], {'role': 'assistant', 'content': text},
                                    {'role': 'user', 'content': f'That reply was invalid: {error}. Answer again, JSON only.'}]
                continue
            _record(purpose, body, reply, started)
            return parsed
        raise LLMError('The model gave no valid answer twice.')
    finally:
        if http is None:
            client.close()
