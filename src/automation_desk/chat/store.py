"""Chats and their messages in the ledger. A chat is a list of turns: one question of the user, and one or more answers
to it (the first, a second opinion …), of which one is chosen: the next question builds on that one."""

import json
from datetime import datetime

from automation_desk.ledger import connect

TITLE_CHARS = 60
RESTARTED = 'The app stopped during this answer.'
JSON_COLUMNS = ('target', 'note')


def _now() -> str:
    """The time now, as the ledger keeps it."""
    # CLAUDE> microseconds: two changes in the same second still sort in the order they happened
    return datetime.now().astimezone().isoformat(timespec='microseconds')


def title_of(question: str) -> str:
    """A chat's first title: its first question on one line, cut at a word to at most 60 characters."""
    text = ' '.join(question.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rsplit(' ', 1)[0].rstrip(',;:') + '…'


def _message(row: dict) -> dict:
    """A message row as the page and the code use it."""
    return row | {'web': bool(row['web']), 'chosen': bool(row['chosen']), 'sources': json.loads(row['sources'] or '[]')}


def create(question: str, model: str, web: bool) -> int:
    """A new chat with its first question."""
    now = _now()
    with connect(write=True) as db:
        chat_id = int(db.execute('INSERT INTO chats (title, model, created, updated) VALUES (?, ?, ?, ?)',
                                 (title_of(question), model, now, now)).lastrowid)
        db.execute("INSERT INTO chat_messages (chat_id, turn, role, content, web, created) VALUES (?, 1, 'user', ?, ?, ?)",
                   (chat_id, question.strip(), int(web), now))
    return chat_id


def add_question(chat_id: int, question: str, web: bool) -> int:
    """The next question of a chat, as a new turn."""
    now = _now()
    with connect(write=True) as db:
        turn = db.execute('SELECT COALESCE(MAX(turn), 0) + 1 FROM chat_messages WHERE chat_id = ?', (chat_id,)).fetchone()[0]
        db.execute('UPDATE chats SET updated = ? WHERE id = ?', (now, chat_id))
        return int(db.execute("INSERT INTO chat_messages (chat_id, turn, role, content, web, created) VALUES (?, ?, 'user', ?, ?, ?)",
                              (chat_id, turn, question.strip(), int(web), now)).lastrowid)


def add_answer(chat_id: int, turn: int, model: str, chosen: bool) -> int:
    """An answer of a turn that is being written."""
    now = _now()
    with connect(write=True) as db:
        db.execute('UPDATE chats SET updated = ? WHERE id = ?', (now, chat_id))
        return int(db.execute("INSERT INTO chat_messages (chat_id, turn, role, model, chosen, state, created) "
                              "VALUES (?, ?, 'assistant', ?, ?, 'writing', ?)", (chat_id, turn, model, int(chosen), now)).lastrowid)


def update_message(message_id: int, **fields: object) -> None:
    """Change columns of a message; `sources` as a list."""
    if 'sources' in fields:
        fields['sources'] = json.dumps(fields['sources'], ensure_ascii=False)
    names = ', '.join(f'{name} = ?' for name in fields)
    with connect(write=True) as db:
        db.execute(f'UPDATE chat_messages SET {names} WHERE id = ?', (*fields.values(), message_id))


def message(message_id: int) -> dict | None:
    """One message."""
    with connect() as db:
        row = db.execute('SELECT * FROM chat_messages WHERE id = ?', (message_id,)).fetchone()
    return _message(dict(row)) if row else None


def delete_message(message_id: int) -> None:
    """Remove one message (an answer that is asked again)."""
    with connect(write=True) as db:
        db.execute('DELETE FROM chat_messages WHERE id = ?', (message_id,))


def get(chat_id: int) -> dict | None:
    """A chat with its messages in order (per turn: the question, then its answers as they came) and its total cost."""
    with connect() as db:
        row = db.execute('SELECT * FROM chats WHERE id = ?', (chat_id,)).fetchone()
        if row is None:
            return None
        messages = [_message(dict(m)) for m in db.execute(
            "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY turn, role = 'assistant', id", (chat_id,))]
    chat = {k: json.loads(v) if k in JSON_COLUMNS and v else v for k, v in dict(row).items()}
    return chat | {'messages': messages, 'cost_usd': sum(m['cost_usd'] or 0 for m in messages)}


def listing() -> list[dict]:
    """Every chat for the start view, the one changed last first: title, model, dates, cost and number of questions."""
    with connect() as db:
        rows = db.execute("SELECT c.id, c.title, c.model, c.created, c.updated, COALESCE(SUM(m.cost_usd), 0) AS cost_usd, "
                          "SUM(m.role = 'user') AS questions FROM chats c LEFT JOIN chat_messages m ON m.chat_id = c.id "
                          'GROUP BY c.id ORDER BY c.updated DESC, c.id DESC').fetchall()
    return [dict(row) for row in rows]


def update(chat_id: int, **fields: object) -> None:
    """Change columns of a chat (title, model, target, note); it counts as a change of the chat."""
    fields = {k: json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS and v is not None else v for k, v in fields.items()}
    fields['updated'] = _now()
    names = ', '.join(f'{name} = ?' for name in fields)
    with connect(write=True) as db:
        db.execute(f'UPDATE chats SET {names} WHERE id = ?', (*fields.values(), chat_id))


def delete(chat_id: int) -> bool:
    """Remove a chat and its messages."""
    with connect(write=True) as db:
        return db.execute('DELETE FROM chats WHERE id = ?', (chat_id,)).rowcount > 0


def _usable(answer: dict) -> bool:
    """An answer the next question can build on: one with text that did not fail."""
    return answer['state'] in ('done', 'stopped') and bool(answer['content'].strip())


def history(chat_id: int, turn: int) -> list[dict]:
    """What the model gets for an answer to `turn`: each earlier turn's question with its chosen answer (else its first
    usable one; a turn without one is left out), then the question of `turn`."""
    chat = get(chat_id) or {'messages': []}
    sent: list[dict] = []
    for number in range(1, turn + 1):
        question = next((m for m in chat['messages'] if m['turn'] == number and m['role'] == 'user'), None)
        if question is None:
            continue
        if number == turn:
            sent.append({'role': 'user', 'content': question['content']})
            break
        answers = [m for m in chat['messages'] if m['turn'] == number and m['role'] == 'assistant' and _usable(m)]
        answer = next((m for m in answers if m['chosen']), answers[0] if answers else None)
        if answer:
            sent += [{'role': 'user', 'content': question['content']}, {'role': 'assistant', 'content': answer['content']}]
    return sent


def choose(message_id: int) -> int:
    """Make an answer the one its turn builds on; the chat goes on with its model. The chat's id."""
    answer = message(message_id)
    with connect(write=True) as db:
        db.execute('UPDATE chat_messages SET chosen = (id = ?) WHERE chat_id = ? AND turn = ? AND role = ?',
                   (message_id, answer['chat_id'], answer['turn'], 'assistant'))
        db.execute('UPDATE chats SET model = ?, updated = ? WHERE id = ?', (answer['model'], _now(), answer['chat_id']))
    return answer['chat_id']


def writing(chat_id: int) -> dict | None:
    """The answer of the chat that is being written now."""
    with connect() as db:
        row = db.execute("SELECT * FROM chat_messages WHERE chat_id = ? AND state = 'writing'", (chat_id,)).fetchone()
    return _message(dict(row)) if row else None


def fail_writing() -> int:
    """Answers that were being written when the app stopped: failed, their text kept. How many."""
    with connect(write=True) as db:
        return db.execute("UPDATE chat_messages SET state = 'failed', error = ? WHERE state = 'writing'", (RESTARTED,)).rowcount


def last_model() -> str | None:
    """The model of the chat changed last."""
    with connect() as db:
        row = db.execute('SELECT model FROM chats ORDER BY updated DESC, id DESC LIMIT 1').fetchone()
    return row[0] if row else None


def model_costs() -> dict[str, float]:
    """The average real cost of an answer per model, from the user's own answers without web search."""
    with connect() as db:
        rows = db.execute("SELECT a.model, AVG(a.cost_usd) FROM chat_messages a JOIN chat_messages q ON q.chat_id = a.chat_id "
                          "AND q.turn = a.turn AND q.role = 'user' WHERE a.role = 'assistant' AND a.state = 'done' "
                          'AND a.cost_usd IS NOT NULL AND q.web = 0 GROUP BY a.model').fetchall()
    return {model: round(cost, 6) for model, cost in rows}
