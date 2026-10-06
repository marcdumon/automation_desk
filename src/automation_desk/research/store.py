"""Research rows, the pages each research found, and the Research page settings, in the ledger."""

import json
from datetime import datetime

from automation_desk.ledger import connect

JSON_COLUMNS = ('countries', 'questions', 'answers', 'classes', 'plan', 'followups', 'followup_answers', 'result',
                'limits', 'requirements', 'target')
PAGE_JSON = ('facts',)
DEFAULTS = {'countries': ['BE', 'NL', 'DE', 'FR'], 'municipality': '', 'limits': {'searches': 20, 'pages': 30, 'cost': 1.0},
            'vault': '', 'subdir': 'Research'}
RESTARTED = 'The app stopped while this research ran. Press Continue to go on from the step it was on.'


def _encode(value: object) -> object:
    """Dicts and lists as JSON text, the rest as is."""
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


def _decode(row: dict, columns: tuple[str, ...]) -> dict:
    """JSON columns back to Python values."""
    return {k: json.loads(v) if k in columns and v is not None else v for k, v in row.items()}


def create(request: str, budget: float | None, countries: list[str]) -> int:
    """A new research in state 'questions', with the current limits."""
    with connect(write=True) as db:
        cursor = db.execute(
            'INSERT INTO research (request, budget, countries, state, limits, created) VALUES (?, ?, ?, ?, ?, ?)',
            (request.strip(), budget, _encode(countries), 'questions', _encode(settings()['limits']),
             datetime.now().astimezone().isoformat(timespec='seconds')))
        return int(cursor.lastrowid)


def get(research_id: int) -> dict | None:
    """One research, JSON columns decoded."""
    with connect() as db:
        row = db.execute('SELECT * FROM research WHERE id = ?', (research_id,)).fetchone()
    return _decode(dict(row), JSON_COLUMNS) if row else None


def update(research_id: int, **fields: object) -> None:
    """Change columns of a research."""
    if not fields:
        return
    names = ', '.join(f'{name} = ?' for name in fields)
    with connect(write=True) as db:
        db.execute(f'UPDATE research SET {names} WHERE id = ?', (*map(_encode, fields.values()), research_id))


def listing() -> list[dict]:
    """Every research for the overview, newest first: its title, state and cost, and its recommendation in short (a
    summary: its counts of key points, sources and sites)."""
    with connect() as db:
        rows = db.execute('SELECT id, request, title, kind, state, created, cost_usd, result FROM research '
                          'ORDER BY id DESC').fetchall()
    out = []
    for row in rows:
        item = dict(row)
        result = json.loads(item.pop('result')) if item.get('result') else {}
        best = result.get('best') or None
        item['best'] = {k: best.get(k) for k in ('title', 'score', 'total')} if best else None
        # CLAUDE> a summary's card: its key points, sources and the sites they come from
        item['summary'] = {'points': sum(len(g['items']) for g in result.get('groups') or []),
                           'sources': len(result.get('sources') or []),
                           'sites': len({s['site'] for s in result.get('sources') or []})} if item['kind'] == 'summary' else None
        out.append(item)
    return out


def delete(research_id: int) -> bool:
    """Remove a research and its pages."""
    with connect(write=True) as db:
        db.execute('DELETE FROM research_pages WHERE research_id = ?', (research_id,))
        return db.execute('DELETE FROM research WHERE id = ?', (research_id,)).rowcount > 0


def add_page(research_id: int, url: str, query: str, title: str, snippet: str, country: str) -> bool:
    """A page a search found; False when the research already has it."""
    with connect(write=True) as db:
        cursor = db.execute(
            'INSERT OR IGNORE INTO research_pages (research_id, url, query, title, snippet, country) VALUES (?, ?, ?, ?, ?, ?)',
            (research_id, url, query, title, snippet, country))
        return cursor.rowcount > 0


def pages(research_id: int, status: str | None = None) -> list[dict]:
    """The pages of a research in the order found, optionally only one status."""
    query, params = 'SELECT * FROM research_pages WHERE research_id = ?', [research_id]
    if status:
        query, params = f'{query} AND status = ?', [*params, status]
    with connect() as db:
        rows = db.execute(f'{query} ORDER BY rowid', params).fetchall()
    return [_decode(dict(r), PAGE_JSON) for r in rows]


def update_page(research_id: int, url: str, **fields: object) -> None:
    """Change columns of one page."""
    names = ', '.join(f'{name} = ?' for name in fields)
    with connect(write=True) as db:
        db.execute(f'UPDATE research_pages SET {names} WHERE research_id = ? AND url = ?',
                   (*map(_encode, fields.values()), research_id, url))


def settings() -> dict:
    """The Research page settings, defaults filled in (other values kept in the same table, like the rates, are no
    settings)."""
    with connect() as db:
        stored = {k: json.loads(v) for k, v in db.execute('SELECT key, value FROM research_settings')}
    return DEFAULTS | {k: v for k, v in stored.items() if k in DEFAULTS}


def kept(key: str) -> object:
    """A value the research keeps between runs (the rates of the day), None when there is none."""
    with connect() as db:
        row = db.execute('SELECT value FROM research_settings WHERE key = ?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def keep(key: str, value: object) -> None:
    """Keep a value between runs."""
    with connect(write=True) as db:
        db.execute('INSERT OR REPLACE INTO research_settings (key, value) VALUES (?, ?)', (key, json.dumps(value)))


def save_settings(countries: list[str] | None = None, municipality: str | None = None, limits: dict | None = None,
                  vault: str | None = None, subdir: str | None = None) -> dict:
    """Keep the settings that were given; returns all settings."""
    given = {'countries': countries, 'municipality': municipality, 'limits': limits,
             'vault': vault.strip() if vault is not None else None, 'subdir': subdir.strip() if subdir is not None else None}
    with connect(write=True) as db:
        for key, value in given.items():
            if value is not None:
                db.execute('INSERT OR REPLACE INTO research_settings (key, value) VALUES (?, ?)', (key,
                                                                                                     json.dumps(value)))
    return settings()


def fail_running() -> int:
    """At app start: a research left running stopped with the app; it can continue from its step."""
    with connect(write=True) as db:
        return db.execute("UPDATE research SET state = 'failed', note = ? WHERE state = 'running'", (RESTARTED,
                                                                                                       )).rowcount


def transition_state(research_id: int, from_state: str, to_state: str) -> bool:
    """Atomically change state if it matches from_state; returns True if successful."""
    with connect(write=True) as db:
        return db.execute('UPDATE research SET state = ? WHERE id = ? AND state = ?', (to_state, research_id,
                                                                                         from_state)).rowcount > 0
