"""The job ledger in SQLite: data/automation.db inside the project.

One row per job in `jobs`, with its preview and results as JSON and its counts and cost kept up to date on every save,
so lists and totals are plain queries. Every model call, Google call and page read is a row of its own, linked to
its job. Saving a job again (after its apply) replaces its rows in one transaction.

A connection per operation, WAL journaling and a busy timeout let the app and other scripts write at the same time.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from llm_automation.config import ROOT

if TYPE_CHECKING:
    from llm_automation.jobs import Job

DB = ROOT / 'data' / 'automation.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, grp TEXT NOT NULL, sentence TEXT NOT NULL, task_id TEXT, task_name TEXT,
    started TEXT NOT NULL, status TEXT, message TEXT, preview_ms INTEGER, applied_at TEXT, apply_status TEXT,
    apply_message TEXT, apply_ms INTEGER, results TEXT, preview TEXT, applied_rows TEXT,
    llm_call_count INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL,
    google_call_count INTEGER, page_read_count INTEGER, models TEXT
);
CREATE INDEX IF NOT EXISTS jobs_started ON jobs (started);
CREATE INDEX IF NOT EXISTS jobs_group_started ON jobs (grp, started);

CREATE TABLE IF NOT EXISTS llm_calls (
    job_id TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE, seq INTEGER NOT NULL, stage TEXT, purpose TEXT,
    model_requested TEXT, model_used TEXT, provider TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
    cost_usd REAL, latency_ms INTEGER, finish_reason TEXT, generation_id TEXT, system TEXT, user TEXT, reply TEXT,
    error TEXT, request TEXT, response TEXT, PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS llm_calls_model ON llm_calls (model_used);
CREATE INDEX IF NOT EXISTS llm_calls_generation ON llm_calls (generation_id);

CREATE TABLE IF NOT EXISTS google_calls (
    job_id TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE, seq INTEGER NOT NULL, stage TEXT, method TEXT,
    params TEXT, latency_ms INTEGER, error TEXT, PRIMARY KEY (job_id, seq)
);

CREATE TABLE IF NOT EXISTS page_reads (
    job_id TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE, seq INTEGER NOT NULL, stage TEXT, url TEXT,
    status INTEGER, bytes INTEGER, latency_ms INTEGER, via TEXT, PRIMARY KEY (job_id, seq)
);
'''

LLM_COLUMNS = ['stage', 'purpose', 'model_requested', 'model_used', 'provider', 'prompt_tokens', 'completion_tokens',
               'cost_usd', 'latency_ms', 'finish_reason', 'generation_id', 'system', 'user', 'reply', 'error', 'request',
               'response']
GOOGLE_COLUMNS = ['stage', 'method', 'params', 'latency_ms', 'error']
PAGE_COLUMNS = ['stage', 'url', 'status', 'bytes', 'latency_ms', 'via']
JSON_FIELDS = {'request', 'response', 'params'}


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """A connection with the schema in place; commits on success, rolls back on error."""
    target = path or DB
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript(SCHEMA)
        with connection:
            yield connection
    finally:
        connection.close()


def _dump(value: object) -> str:
    """JSON text for a column."""
    return json.dumps(value, ensure_ascii=False)


def store(job: 'Job') -> None:
    """Save a job and all its calls, replacing an earlier save of the same job."""
    summary = job.summary()
    with connect() as db:
        db.execute('DELETE FROM jobs WHERE id = ?', (job.id,))
        db.execute(
            '''INSERT INTO jobs (id, grp, sentence, task_id, task_name, started, status, message, preview_ms, applied_at,
                                 apply_status, apply_message, apply_ms, results, preview, applied_rows, llm_call_count,
                                 prompt_tokens, completion_tokens, cost_usd, google_call_count, page_read_count, models)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (job.id, job.group, job.sentence, job.task_id, job.task_name, job.started, job.status, job.message,
             job.preview_ms, job.applied_at, job.apply_status, job.apply_message, job.apply_ms, _dump(job.results),
             _dump(job.preview), _dump(job.applied_rows), summary['llm_calls'], summary['prompt_tokens'],
             summary['completion_tokens'], summary['cost_usd'], summary['google_calls'], summary['fetches'],
             _dump(summary['models'])))
        for table, columns, calls in (('llm_calls', LLM_COLUMNS, job.llm_calls),
                                      ('google_calls', GOOGLE_COLUMNS, job.google_calls),
                                      ('page_reads', PAGE_COLUMNS, job.fetches)):
            rows = []
            for seq, call in enumerate(calls):
                values = asdict(call)
                rows.append((job.id, seq, *(_dump(values[c]) if c in JSON_FIELDS else values[c] for c in columns)))
            if rows:
                marks = ', '.join('?' * (len(columns) + 2))
                db.executemany(f"INSERT INTO {table} (job_id, seq, {', '.join(columns)}) VALUES ({marks})", rows)


def _summary(row: sqlite3.Row) -> dict:
    """A job row as the lists and cost lines show it."""
    return {
        'id': row['id'], 'group': row['grp'], 'sentence': row['sentence'], 'task_id': row['task_id'],
        'task_name': row['task_name'], 'started': row['started'], 'status': row['status'], 'message': row['message'],
        'applied_at': row['applied_at'], 'apply_status': row['apply_status'], 'apply_message': row['apply_message'],
        'results': len(json.loads(row['results'] or '[]')), 'preview_ms': row['preview_ms'], 'apply_ms': row['apply_ms'],
        'duration_ms': (row['preview_ms'] or 0) + (row['apply_ms'] or 0), 'models': json.loads(row['models'] or '[]'),
        'llm_calls': row['llm_call_count'], 'prompt_tokens': row['prompt_tokens'],
        'completion_tokens': row['completion_tokens'], 'cost_usd': row['cost_usd'],
        'google_calls': row['google_call_count'], 'fetches': row['page_read_count'],
    }


def summaries(group: str | None = None) -> list[dict]:
    """Job summaries, newest first, optionally of one group."""
    with connect() as db:
        rows = db.execute('SELECT * FROM jobs WHERE (? IS NULL OR grp = ?) ORDER BY started DESC', (group, group))
        return [_summary(row) for row in rows]


def totals() -> dict:
    """Cost, calls and tokens overall, per group and per model."""
    def usage(rows: sqlite3.Cursor) -> dict[str, dict]:
        return {row['name']: {'calls': row['calls'], 'prompt_tokens': row['tin'], 'completion_tokens': row['tout'],
                              'cost_usd': row['cost']} for row in rows}

    aggregate = 'COUNT(*) AS calls, SUM(c.prompt_tokens) AS tin, SUM(c.completion_tokens) AS tout, SUM(c.cost_usd) AS cost'
    with connect() as db:
        by_group = usage(db.execute(f'SELECT j.grp AS name, {aggregate} FROM llm_calls c JOIN jobs j ON j.id = c.job_id '
                                    'GROUP BY j.grp ORDER BY cost DESC'))
        by_model = usage(db.execute(f"SELECT COALESCE(NULLIF(c.model_used, ''), c.model_requested) AS name, {aggregate} "
                                    'FROM llm_calls c GROUP BY name ORDER BY cost DESC'))
        total = db.execute('SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls').fetchone()[0]
    return {'total_cost_usd': total, 'by_group': by_group, 'by_model': by_model}


def _calls(db: sqlite3.Connection, table: str, columns: list[str], job_id: str) -> list[dict]:
    """A job's calls of one kind, in the order they happened."""
    rows = db.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE job_id = ? ORDER BY seq", (job_id,))
    return [{c: json.loads(row[c]) if c in JSON_FIELDS and row[c] is not None else row[c] for c in columns} for row in rows]


def detail(job_id: str) -> dict | None:
    """One job in full: every field, every call, its preview and results; None when unknown."""
    with connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        if row is None:
            return None
        return {**_summary(row), 'group': row['grp'], 'summary': _summary(row),
                'results': json.loads(row['results'] or '[]'), 'preview': json.loads(row['preview'] or '{}'),
                'applied_rows': json.loads(row['applied_rows'] or '[]'),
                'llm_calls': _calls(db, 'llm_calls', LLM_COLUMNS, job_id),
                'google_calls': _calls(db, 'google_calls', GOOGLE_COLUMNS, job_id),
                'fetches': _calls(db, 'page_reads', PAGE_COLUMNS, job_id)}


def all_jobs() -> list[dict]:
    """Every job in full, newest first; for checks and tests, not for pages."""
    return [full for s in summaries() if (full := detail(s['id']))]
