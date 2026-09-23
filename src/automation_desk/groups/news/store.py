"""All reads and writes of the news tables in the ledger database."""

import json
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
class DigestRecord:
    """A finished digest, ready to store."""

    made_at: datetime
    covers_from: datetime
    trigger: str
    job_id: str
    problems: list[str]
    stories: list[StoryRecord]
    suggestions: dict[str, list[str]] = field(default_factory=dict)


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


def set_source_result(source_id: int, result: str) -> None:
    """Remember when a site was last read and what happened."""
    with connect(write=True) as db:
        db.execute('UPDATE news_sources SET last_checked = ?, last_result = ? WHERE id = ?', (_now(), result, source_id))


def subjects() -> list[str]:
    """The user's subjects, in order."""
    with connect() as db:
        return [r[0] for r in db.execute('SELECT name FROM news_subjects ORDER BY position')]


def set_subjects(names: list[str]) -> None:
    """Replace the subject list with `names`, in that order; blanks and repeats are dropped."""
    clean = list(dict.fromkeys(' '.join(n.split()) for n in names if n.strip()))
    with connect(write=True) as db:
        db.execute('DELETE FROM news_subjects')
        db.executemany('INSERT INTO news_subjects (name, position) VALUES (?, ?)', [(n, i) for i, n in enumerate(clean)])


def known_links(links: list[str]) -> set[str]:
    """Which of these links were already in a digest or recorded as seen."""
    if not links:
        return set()
    marks = ', '.join('?' * len(links))
    with connect() as db:
        rows = db.execute(f'SELECT link FROM news_articles WHERE link IN ({marks}) '
                          f'UNION SELECT link FROM news_seen WHERE link IN ({marks})', [*links, *links])
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
        for name, examples in record.suggestions.items():
            db.execute("INSERT INTO news_suggestions (name, examples, digest_id, status) VALUES (?, ?, ?, 'open') "
                       "ON CONFLICT (name) DO UPDATE SET examples = excluded.examples, digest_id = excluded.digest_id "
                       "WHERE news_suggestions.status = 'open'", (name, json.dumps(examples[:5], ensure_ascii=False), digest_id))
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
    order = subjects()
    grouped: dict[str, list[dict]] = {}
    for story in stories:
        grouped.setdefault(story['subject'] or 'Other', []).append(
            {'id': story['id'], 'title': story['title'], 'summary': story['summary'],
             'articles': [{'link': a['link'], 'title': a['title'], 'source': a['source'] or '', 'published': a['published'],
                           'from_teaser': bool(a['from_teaser']), 'reason': a['reason'] or ''}
                          for a in articles if a['story_id'] == story['id']]})
    ranked = sorted(grouped, key=lambda s: (s == 'Other', order.index(s) if s in order else len(order), s))
    return {**dict(head), 'problems': json.loads(head['problems'] or '[]'),
            'subjects': [{'subject': s, 'stories': grouped[s]} for s in ranked]}


def latest_made_at() -> datetime | None:
    """When the newest digest was made."""
    with connect() as db:
        row = db.execute('SELECT MAX(made_at) FROM news_digests').fetchone()
    return datetime.fromisoformat(row[0]) if row[0] else None


def open_suggestions() -> list[dict]:
    """Subjects the model proposed that the user has not accepted or rejected yet."""
    with connect() as db:
        rows = db.execute("SELECT * FROM news_suggestions WHERE status = 'open' ORDER BY name").fetchall()
    return [{'name': r['name'], 'examples': json.loads(r['examples'] or '[]'), 'digest_id': r['digest_id']} for r in rows]


def set_suggestion(name: str, status: str) -> None:
    """Accept (the subject joins the list, last) or reject a suggestion."""
    with connect(write=True) as db:
        db.execute('UPDATE news_suggestions SET status = ? WHERE name = ?', (status, name))
    if status == 'accepted' and name not in subjects():
        set_subjects([*subjects(), name])


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
