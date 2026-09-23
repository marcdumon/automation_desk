"""All reads and writes of the news tables in the ledger database."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from automation_desk.config import config
from automation_desk.ledger import connect


@dataclass(frozen=True)
class Source:
    """A news site or blog the user follows."""

    id: int
    site: str
    name: str
    feed: str
    kind: str
    added: str
    last_checked: str
    last_result: str


@dataclass(frozen=True)
class ArticleRecord:
    """One article as stored: its summary lives on its story."""

    link: str
    source_id: int
    title: str
    published: datetime | None
    teaser: str
    from_teaser: bool
    reason: str


@dataclass(frozen=True)
class StoryRecord:
    """One headline of a digest: one or more articles telling the same story."""

    subject: str
    title: str
    summary: str
    articles: list[ArticleRecord]


@dataclass(frozen=True)
class LeftOut:
    """An article on a blocked topic: not in the digest, only listed as left out."""

    link: str
    source_id: int
    title: str
    topic: str


@dataclass(frozen=True)
class DigestRecord:
    """A finished digest, ready to store."""

    made_at: datetime
    covers_from: datetime
    trigger: str
    job_id: str
    problems: list[str]
    stories: list[StoryRecord]
    suggestions: dict[str, list[str]] = field(default_factory=dict)
    left_out: list[LeftOut] = field(default_factory=list)


def _now() -> str:
    """Local time with offset, to the second."""
    return datetime.now().astimezone().isoformat(timespec='seconds')


def add_source(site: str, name: str, feed: str, kind: str) -> int:
    """Add a site (or update it when the address is known) and return its id."""
    with connect(write=True) as db:
        db.execute('INSERT INTO news_sources (site, name, feed, kind, added) VALUES (?, ?, ?, ?, ?) '
                   'ON CONFLICT (site) DO UPDATE SET name = excluded.name, feed = excluded.feed, kind = excluded.kind',
                   (site, name, feed, kind, _now()))
        return db.execute('SELECT id FROM news_sources WHERE site = ?', (site,)).fetchone()[0]


def remove_source(source_id: int) -> None:
    """Stop following a site; its earlier digests stay."""
    with connect(write=True) as db:
        db.execute('DELETE FROM news_sources WHERE id = ?', (source_id,))


def sources() -> list[Source]:
    """All followed sites, in the order they were added."""
    with connect() as db:
        rows = db.execute('SELECT * FROM news_sources ORDER BY id').fetchall()
    return [Source(r['id'], r['site'], r['name'], r['feed'] or '', r['kind'], r['added'], r['last_checked'] or '',
                   r['last_result'] or '') for r in rows]


def set_source_result(source_id: int, result: str, ok: bool = True) -> None:
    """Remember what happened when a site was read; `last_checked` is the last good read, so a failed first read stays one."""
    with connect(write=True) as db:
        if ok:
            db.execute('UPDATE news_sources SET last_checked = ?, last_result = ? WHERE id = ?', (_now(), result, source_id))
        else:
            db.execute('UPDATE news_sources SET last_result = ? WHERE id = ?', (result, source_id))


def _names(table: str) -> list[str]:
    """The names in a name list table, in order."""
    with connect() as db:
        return [r[0] for r in db.execute(f'SELECT name FROM {table} ORDER BY position')]


def _set_names(table: str, names: list[str]) -> None:
    """Replace a name list with `names`, in that order; blanks and repeats (in any case) are dropped."""
    clean: dict[str, str] = {}
    for name in (' '.join(n.split()) for n in names):
        if name:
            clean.setdefault(name.casefold(), name)
    with connect(write=True) as db:
        db.execute(f'DELETE FROM {table}')
        db.executemany(f'INSERT INTO {table} (name, position) VALUES (?, ?)', [(n, i) for i, n in enumerate(clean.values())])


def subjects() -> list[str]:
    """The user's subjects, in order."""
    return _names('news_subjects')


def set_subjects(names: list[str]) -> None:
    """Replace the subject list with `names`, in that order."""
    _set_names('news_subjects', names)


def blocked() -> list[str]:
    """The topics the user never wants in a digest, in order."""
    return _names('news_blocked')


def set_blocked(names: list[str]) -> None:
    """Replace the blocked topics with `names`, in that order."""
    _set_names('news_blocked', names)


def known_links(links: list[str]) -> set[str]:
    """Which of these links were already in a digest, left out of one, or recorded as seen."""
    if not links:
        return set()
    marks = ', '.join('?' * len(links))
    with connect() as db:
        rows = db.execute(f'SELECT link FROM news_articles WHERE link IN ({marks}) '
                          f'UNION SELECT link FROM news_seen WHERE link IN ({marks}) '
                          f'UNION SELECT link FROM news_left_out WHERE link IN ({marks})', [*links, *links, *links])
        return {r[0] for r in rows}


def mark_seen(source_id: int, links: list[str]) -> None:
    """Record links as seen without putting them in a digest (a front page read for the first time)."""
    with connect(write=True) as db:
        db.executemany('INSERT OR IGNORE INTO news_seen (link, source_id, seen) VALUES (?, ?, ?)',
                       [(link, source_id, _now()) for link in links])


def save_digest(record: DigestRecord) -> int:
    """Store a digest with its stories, articles and subject suggestions; return its id."""
    articles = sum(len(s.articles) for s in record.stories)
    source_count = len({a.source_id for s in record.stories for a in s.articles})
    with connect(write=True) as db:
        cursor = db.execute(
            'INSERT INTO news_digests (made_at, covers_from, trigger, job_id, article_count, story_count, source_count, problems) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (record.made_at.isoformat(timespec='seconds'), record.covers_from.isoformat(timespec='seconds'), record.trigger,
             record.job_id, articles, len(record.stories), source_count, json.dumps(record.problems, ensure_ascii=False)))
        digest_id = cursor.lastrowid
        for position, story in enumerate(record.stories):
            story_id = f'{digest_id}-{position}'
            db.execute('INSERT INTO news_stories (id, digest_id, position, subject, title, summary) VALUES (?, ?, ?, ?, ?, ?)',
                       (story_id, digest_id, position, story.subject, story.title, story.summary))
            db.executemany(
                'INSERT OR REPLACE INTO news_articles (link, source_id, story_id, title, published, teaser, from_teaser, reason) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [(a.link, a.source_id, story_id, a.title, a.published.isoformat(timespec='seconds') if a.published else None,
                  a.teaser, int(a.from_teaser), a.reason) for a in story.articles])
        db.executemany('INSERT OR REPLACE INTO news_left_out (link, digest_id, source_id, title, topic) VALUES (?, ?, ?, ?, ?)',
                       [(a.link, digest_id, a.source_id, a.title, a.topic) for a in record.left_out])
        for name, examples in record.suggestions.items():
            db.execute("INSERT INTO news_suggestions (name, examples, digest_id, status) VALUES (?, ?, ?, 'open') "
                       "ON CONFLICT (name) DO UPDATE SET examples = excluded.examples, digest_id = excluded.digest_id "
                       "WHERE news_suggestions.status = 'open'", (name, json.dumps(examples, ensure_ascii=False), digest_id))
    return digest_id


def digests() -> list[dict]:
    """All digests, newest first, without their stories."""
    with connect() as db:
        rows = db.execute('SELECT * FROM news_digests ORDER BY made_at DESC').fetchall()
    return [{**dict(r), 'problems': json.loads(r['problems'] or '[]')} for r in rows]


def digest(digest_id: int) -> dict | None:
    """One digest with its stories grouped by subject in the user's order, 'Other' last."""
    with connect() as db:
        head = db.execute('SELECT * FROM news_digests WHERE id = ?', (digest_id,)).fetchone()
        if head is None:
            return None
        stories = db.execute('SELECT * FROM news_stories WHERE digest_id = ? ORDER BY position', (digest_id,)).fetchall()
        articles = db.execute(
            'SELECT a.*, s.name AS source FROM news_articles a JOIN news_stories t ON t.id = a.story_id '
            'LEFT JOIN news_sources s ON s.id = a.source_id WHERE t.digest_id = ? ORDER BY a.published', (digest_id,)).fetchall()
        left_out = db.execute('SELECT o.link, o.title, s.name AS source, o.topic FROM news_left_out o '
                              'LEFT JOIN news_sources s ON s.id = o.source_id WHERE o.digest_id = ? ORDER BY o.topic, o.title',
                              (digest_id,)).fetchall()
    order = subjects()
    grouped: dict[str, list[dict]] = {}
    for story in stories:
        grouped.setdefault(story['subject'] or 'Other', []).append(
            {'id': story['id'], 'title': story['title'], 'summary': story['summary'],
             'articles': [{'link': a['link'], 'title': a['title'], 'source': a['source'] or '', 'published': a['published'],
                           'from_teaser': bool(a['from_teaser']), 'reason': a['reason'] or ''}
                          for a in sorted((a for a in articles if a['story_id'] == story['id']),
                                          key=lambda a: a['title'] != story['title'])]})
    ranked = sorted(grouped, key=lambda s: (s == 'Other', order.index(s) if s in order else len(order), s))
    return {**dict(head), 'problems': json.loads(head['problems'] or '[]'), 'cost_usd': _job_cost(head['job_id']),
            'subjects': [{'subject': s, 'stories': grouped[s]} for s in ranked],
            'left_out': [{'link': o['link'], 'title': o['title'], 'source': o['source'] or '', 'topic': o['topic']} for o in left_out]}


def _delete_stories(db: sqlite3.Connection, story_ids: list[str], digest_id: int) -> None:
    """Remove stories from a digest and recount it; their articles stay seen so they never return."""
    for story_id in story_ids:
        db.execute('INSERT OR IGNORE INTO news_seen (link, source_id, seen) '
                   'SELECT link, source_id, ? FROM news_articles WHERE story_id = ?', (_now(), story_id))
        db.execute('DELETE FROM news_articles WHERE story_id = ?', (story_id,))
        db.execute('DELETE FROM news_stories WHERE id = ?', (story_id,))
    joined = 'FROM news_articles a JOIN news_stories t ON t.id = a.story_id WHERE t.digest_id = :d'
    db.execute(f'UPDATE news_digests SET story_count = (SELECT COUNT(*) FROM news_stories WHERE digest_id = :d), '
               f'article_count = (SELECT COUNT(*) {joined}), source_count = (SELECT COUNT(DISTINCT a.source_id) {joined}) '
               'WHERE id = :d', {'d': digest_id})


def delete_story(story_id: str) -> bool:
    """Remove a story from its digest; its articles never return. False when there is no such story."""
    with connect(write=True) as db:
        story = db.execute('SELECT digest_id FROM news_stories WHERE id = ?', (story_id,)).fetchone()
        if story is None:
            return False
        _delete_stories(db, [story_id], story['digest_id'])
    return True


def delete_subject(digest_id: int, subject: str) -> int:
    """Remove every story of one subject from a digest; returns how many stories went."""
    with connect(write=True) as db:
        ids = [r[0] for r in db.execute(
            "SELECT id FROM news_stories WHERE digest_id = ? AND COALESCE(NULLIF(subject, ''), 'Other') = ?", (digest_id, subject))]
        if ids:
            _delete_stories(db, ids, digest_id)
    return len(ids)


def delete_digest(digest_id: int) -> bool:
    """Remove a whole digest; its articles (left-out ones too) never return, and its time still counts for the schedule."""
    with connect(write=True) as db:
        head = db.execute('SELECT made_at FROM news_digests WHERE id = ?', (digest_id,)).fetchone()
        if head is None:
            return False
        _delete_stories(db, [r[0] for r in db.execute('SELECT id FROM news_stories WHERE digest_id = ?', (digest_id,))], digest_id)
        db.execute('INSERT OR IGNORE INTO news_seen (link, source_id, seen) '
                   'SELECT link, source_id, ? FROM news_left_out WHERE digest_id = ?', (_now(), digest_id))
        db.execute('DELETE FROM news_left_out WHERE digest_id = ?', (digest_id,))
        db.execute('DELETE FROM news_digests WHERE id = ?', (digest_id,))
        # CLAUDE> without this, no digest left means "none yet": the scheduler would make a new one within a minute
        _set_last_run(db, head['made_at'])
    return True


def _set_last_run(db: sqlite3.Connection, at_iso: str) -> None:
    """Remember the latest digest run for the schedule, also when its digest is deleted or was never saved."""
    db.execute("INSERT INTO news_settings (key, value) VALUES ('last_run_at', ?) ON CONFLICT (key) DO UPDATE SET "
               'value = MAX(value, excluded.value)', (at_iso,))


def record_nothing_new(at: datetime, since: datetime, problems: list[str]) -> None:
    """A run that found no new article: no digest is saved, the page says so, and the schedule counts the run."""
    note = {'at': at.isoformat(timespec='seconds'), 'since': since.isoformat(timespec='seconds'), 'problems': problems}
    with connect(write=True) as db:
        db.execute("INSERT INTO news_settings (key, value) VALUES ('nothing_new', ?) "
                   'ON CONFLICT (key) DO UPDATE SET value = excluded.value', (json.dumps(note, ensure_ascii=False),))
        _set_last_run(db, note['at'])


def nothing_new() -> dict | None:
    """The latest run that found nothing new, while no digest was made after it."""
    with connect() as db:
        row = db.execute("SELECT value FROM news_settings WHERE key = 'nothing_new'").fetchone()
        newest = db.execute('SELECT MAX(made_at) FROM news_digests').fetchone()[0]
    note = json.loads(row[0]) if row else None
    if note is None or (newest and datetime.fromisoformat(newest) >= datetime.fromisoformat(note['at'])):
        return None
    return note


def latest_made_at() -> datetime | None:
    """When the latest digest run was, deleted digests and runs with nothing new included."""
    with connect() as db:
        stored = db.execute('SELECT MAX(made_at) FROM news_digests').fetchone()[0]
        last_run = db.execute("SELECT value FROM news_settings WHERE key = 'last_run_at'").fetchone()
    times = [datetime.fromisoformat(t) for t in (stored, last_run[0] if last_run else None) if t]
    return max(times) if times else None


def _job_cost(job_id: str | None) -> float:
    """What the model calls of a digest's job cost."""
    with connect() as db:
        return float(db.execute('SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE job_id = ?', (job_id,)).fetchone()[0])


def open_suggestions() -> list[dict]:
    """Subjects the model proposed that the user has not accepted or rejected yet, never a blocked topic."""
    with connect() as db:
        rows = db.execute("SELECT * FROM news_suggestions WHERE status = 'open' ORDER BY name").fetchall()
    hidden = {name.casefold() for name in blocked()}
    return [{'name': r['name'], 'examples': json.loads(r['examples'] or '[]'), 'digest_id': r['digest_id']}
            for r in rows if r['name'].casefold() not in hidden]


def set_suggestion(name: str, status: str) -> None:
    """Accept (the subject joins the list, last), block (it joins the blocked topics) or reject a suggestion."""
    with connect(write=True) as db:
        db.execute('UPDATE news_suggestions SET status = ? WHERE name = ?', (status, name))
    if status == 'accepted':
        set_subjects([*subjects(), name])
    elif status == 'blocked':
        set_blocked([*blocked(), name])


def cap() -> float:
    """The daily cost cap in dollars."""
    with connect() as db:
        row = db.execute("SELECT value FROM news_settings WHERE key = 'cap_usd'").fetchone()
    return float(row[0]) if row else config().news.cap_usd


def set_cap(usd: float) -> None:
    """Change the daily cost cap."""
    with connect(write=True) as db:
        db.execute("INSERT INTO news_settings (key, value) VALUES ('cap_usd', ?) "
                   'ON CONFLICT (key) DO UPDATE SET value = excluded.value', (str(usd),))
