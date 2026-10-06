"""Answers in a chat. A worker thread reads the answer from OpenRouter's stream and keeps each piece as an event; the page
reads the events as they come (and again from the start after a reload). The text is saved about every second, the cost
goes on a job of the Chat group, and a stopped answer's cost is asked from OpenRouter afterwards."""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date

import httpx

from automation_desk import jobs, llm
from automation_desk.chat import store

log = logging.getLogger(__name__)

GROUP = 'chat'
MAX_TOKENS = 16000
WEB_PLUGIN = {'id': 'web', 'engine': 'exa', 'max_results': 5}
SAVE_EVERY_S = 1.0
CUT_OFF = 'The answer was longer than the limit, so it is cut off.'
BROKEN_OFF = 'The answer stopped before its end. Press Try again.'


class Busy(RuntimeError):
    """An answer of this chat is being written."""


@dataclass
class Live:
    """An answer being written: its events (start, wait, piece, end) and the user's Stop."""

    chat_id: int
    message_id: int
    events: list[dict] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)
    done: bool = False


# CLAUDE> the answer of each chat being written now, or written last (a page that attaches late finds its end)
_live: dict[int, Live] = {}
_lock = threading.Lock()


def _today() -> date:
    """Today, for the model's instruction."""
    return date.today()


def system_text() -> str:
    """What the model is told before the conversation."""
    today = _today()
    return f'You are a helpful assistant. Today is {today.day} {today:%B %Y}.'


def start(chat_id: int, model: str | None = None, replace: int | None = None, background: bool = True) -> Live:
    """Begin an answer to the chat's last question: with the chat's model, or `model` (a second opinion when the question
    has an answer already). `replace` asks again for that answer (Try again): same turn, same model, same choice."""
    with _lock:
        if busy(chat_id):
            raise Busy('An answer of this chat is being written. Wait for it, or press Stop.')
        chat = store.get(chat_id)
        questions = [m for m in chat['messages'] if m['role'] == 'user']
        if replace is not None:
            old = store.message(replace)
            turn, chosen, model = old['turn'], old['chosen'], model or old['model']
            store.delete_message(replace)
        else:
            turn = questions[-1]['turn']
            answers = [m for m in chat['messages'] if m['turn'] == turn and m['role'] == 'assistant']
            chosen, model = not any(a['chosen'] for a in answers), model or chat['model']
        others = [m for m in chat['messages'] if m['turn'] == turn and m['role'] == 'assistant' and m['id'] != replace]
        question = next(m for m in questions if m['turn'] == turn)
        message_id = store.add_answer(chat_id, turn, model, chosen)
        live = Live(chat_id, message_id, [{'type': 'start', 'message': store.message(message_id)}])
        _live[chat_id] = live
    purpose = 'Second opinion' if others and not chosen else 'Chat answer'
    if background:
        threading.Thread(target=_write, args=(live, model, question, purpose), daemon=True).start()
    else:
        _write(live, model, question, purpose)
    return live


def busy(chat_id: int) -> bool:
    """Whether an answer of the chat is being written."""
    running = _live.get(chat_id)
    return bool((running and not running.done) or store.writing(chat_id))


def _write(live: Live, model: str, question: dict, purpose: str) -> None:
    """Write the answer; whatever goes wrong, the answer ends (failed) and a page that follows it is told."""
    try:
        _answer(live, model, question, purpose)
    except Exception as error:
        log.exception('chat answer %s', live.message_id)
        store.update_message(live.message_id, state='failed', error=f'Something went wrong in the app: {error}. Press Try again.')
    finally:
        if not live.done:
            live.events.append({'type': 'end', 'message': store.message(live.message_id)})
            live.done = True


def _answer(live: Live, model: str, question: dict, purpose: str) -> None:
    """Read the answer from OpenRouter into the live events and the message, and book it."""
    body = {'model': model, 'stream': True, 'max_tokens': MAX_TOKENS, 'usage': {'include': True},
            'messages': [{'role': 'system', 'content': system_text()}, *store.history(live.chat_id, question['turn'])]}
    if question['web']:
        body['plugins'] = [WEB_PLUGIN]
    job = jobs.Job(group=GROUP, sentence=question['content'][:300], task_id='chat', task_name=purpose)
    answer: dict = {}
    state, error = 'done', ''
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0)) as http:
        with jobs.run(job):
            state, error = _read(live, body, http, answer)
            llm.record_stream(purpose.lower(), body, answer, started, error if state == 'failed' else '')
            if state == 'failed':
                job.status, job.message = 'error', error
        cost = (answer.get('usage') or {}).get('cost')
        store.update_message(live.message_id, content=answer.get('text', ''), state=state, error=error, cost_usd=cost,
                             seconds=round(time.monotonic() - started, 1), sources=answer.get('sources', []), job_id=job.id,
                             generation_id=answer.get('id', ''))
        live.events.append({'type': 'end', 'message': store.message(live.message_id)})
        live.done = True
        # CLAUDE> a stopped or broken answer has no usage at its end: OpenRouter still bills what was written
        if cost is None and answer.get('id') and (cost := llm.generation_cost(answer['id'], http)) is not None:
            job.llm_calls[-1].cost_usd = cost
            jobs.save(job)
            store.update_message(live.message_id, cost_usd=cost)


def _read(live: Live, body: dict, http: httpx.Client, answer: dict) -> tuple[str, str]:
    """Stream the answer into `answer` and the live events; its state and the reason when it is not done."""
    try:
        response = llm.open_stream(body, http, on_wait=lambda seconds: live.events.append({'type': 'wait', 'seconds': seconds}))
    except llm.LLMError as error:
        return 'failed', str(error)
    state = 'done'
    saved = time.monotonic()
    try:
        for event in llm.sse_events(response.iter_lines()):
            # CLAUDE> checked before each event, keep-alive lines too: what comes after Stop is not shown or kept
            if live.stop.is_set():
                state = 'stopped'
                break
            if piece := llm.read_chunk(event, answer):
                live.events.append({'type': 'piece', 'text': piece})
                if time.monotonic() - saved > SAVE_EVERY_S:
                    store.update_message(live.message_id, content=answer['text'])
                    saved = time.monotonic()
    except httpx.HTTPError as error:
        return 'failed', f'The connection to OpenRouter broke: {error}. Press Try again.'
    finally:
        response.close()
    if answer.get('error'):
        return 'failed', answer['error']
    if state == 'stopped':
        return 'stopped', ''
    if answer.get('finish_reason') == 'length':
        return 'stopped', CUT_OFF
    if not answer.get('finish_reason'):
        return 'failed', BROKEN_OFF
    return 'done', ''


def stop(chat_id: int) -> bool:
    """Stop the answer being written in a chat; False when none is."""
    live = _live.get(chat_id)
    if not live or live.done:
        return False
    live.stop.set()
    return True


async def follow(chat_id: int) -> AsyncIterator[dict]:
    """The events of the answer being written in a chat, from its start, as they come; nothing when none is."""
    live = _live.get(chat_id)
    if not live or live.done:
        return
    seen = 0
    while True:
        while seen < len(live.events):
            event = live.events[seen]
            yield event
            seen += 1
            if event['type'] == 'end':
                return
        if live.done:
            return
        await asyncio.sleep(0.05)
