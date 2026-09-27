"""Watched agenda sites: one per line with the calendar their events go to, and the events the user skipped."""

import re
import threading
from dataclasses import dataclass
from datetime import datetime

from automation_desk import jobs
from automation_desk.ledger import connect

DEFAULT_CALENDAR = 'events'
NEEDS_BROWSER = 'needs your browser'
# CLAUDE> what the running check is doing, for the page to poll: each site's state, and the job whose reads it counts
_progress: dict = {}
_progress_lock = threading.Lock()
# CLAUDE> 'kmska.be/nl/agenda → Exhibitions' or '... -> Exhibitions'
ARROW = re.compile(r'\s*(?:→|->)\s*')


@dataclass(frozen=True)
class WatchedSite:
    """An agenda page the user watches, the calendar its events go to ('' = the default), and its last check."""

    id: int
    site: str
    calendar: str
    last_result: str


def _address(site: str) -> str:
    """https://, no trailing slash."""
    site = site.strip().rstrip('/')
    return site if site.startswith('http') else f'https://{site}'


def _label(site: str) -> str:
    """A site as people write it: kmska.be/nl/agenda."""
    return site.removeprefix('https://').removeprefix('http://').removeprefix('www.').rstrip('/')


def sites() -> list[WatchedSite]:
    """The watched sites, in the order of the list."""
    with connect() as db:
        rows = db.execute('SELECT * FROM watched_sites ORDER BY id').fetchall()
    return [WatchedSite(r['id'], r['site'], r['calendar'], r['last_result'] or '') for r in rows]


def _parse(line: str) -> tuple[str, str]:
    """A line as (site address, calendar name or '')."""
    site, *calendar = ARROW.split(line.strip(), maxsplit=1)
    return _address(site) if site.strip() else '', calendar[0].strip() if calendar else ''


def save_list(lines: list[str]) -> None:
    """Make the watched sites exactly these lines ('site → calendar', or just the site for the default calendar)."""
    wanted: dict[str, tuple[str, str]] = {}
    for line in lines:
        site, calendar = _parse(line)
        if site:
            wanted.setdefault(_label(site), (site, calendar))
    with connect(write=True) as db:
        for row in db.execute('SELECT id, site FROM watched_sites').fetchall():
            if _label(row['site']) not in wanted:
                db.execute('DELETE FROM watched_sites WHERE id = ?', (row['id'],))
                db.execute('DELETE FROM watched_browser WHERE site_id = ?', (row['id'],))
        known = {_label(r['site']): r['site'] for r in db.execute('SELECT site FROM watched_sites')}
        for label, (site, calendar) in wanted.items():
            db.execute('INSERT INTO watched_sites (site, calendar) VALUES (?, ?) '
                       'ON CONFLICT (site) DO UPDATE SET calendar = excluded.calendar', (known.get(label, site), calendar))


def add_sites(found: list[tuple[str, str]]) -> int:
    """Add (site, calendar) pairs after the existing lines, skipping sites already watched; returns how many were added."""
    existing = {_label(s.site) for s in sites()}
    default = default_calendar().casefold()
    added = 0
    with connect(write=True) as db:
        for site, calendar in found:
            if _label(site) in existing:
                continue
            existing.add(_label(site))
            own = '' if calendar.casefold() == default else calendar
            db.execute('INSERT INTO watched_sites (site, calendar) VALUES (?, ?)', (site, own))
            added += 1
    return added


def as_lines() -> list[str]:
    """The list as the user edits it."""
    return [f'{_label(s.site)} → {s.calendar}' if s.calendar else _label(s.site) for s in sites()]


def default_calendar() -> str:
    """The calendar for sites without one of their own."""
    with connect() as db:
        row = db.execute("SELECT value FROM watched_settings WHERE key = 'default_calendar'").fetchone()
    return row[0] if row else DEFAULT_CALENDAR


def set_default_calendar(name: str) -> None:
    """Change the default calendar."""
    with connect(write=True) as db:
        db.execute("INSERT INTO watched_settings (key, value) VALUES ('default_calendar', ?) "
                   'ON CONFLICT (key) DO UPDATE SET value = excluded.value', (' '.join(name.split()) or DEFAULT_CALENDAR,))


def calendar_for(site: WatchedSite) -> str:
    """The calendar a site's events go to."""
    return site.calendar or default_calendar()


def browser_sites() -> set[int]:
    """The sites that show their agenda only in the browser: read that way when the user asks, left alone otherwise."""
    with connect() as db:
        return {row[0] for row in db.execute('SELECT site_id FROM watched_browser')}


def mark_browser(site_id: int) -> None:
    """Remember that a site was read through the browser."""
    with connect(write=True) as db:
        db.execute('INSERT OR IGNORE INTO watched_browser (site_id) VALUES (?)', (site_id,))


def set_result(site_id: int, result: str) -> None:
    """What the last check of a site found."""
    with connect(write=True) as db:
        db.execute('UPDATE watched_sites SET last_result = ? WHERE id = ?', (result, site_id))


def skip(keys: set[str]) -> None:
    """Events the user left unticked: never offered again."""
    now = datetime.now().astimezone().isoformat(timespec='seconds')
    with connect(write=True) as db:
        db.executemany('INSERT OR IGNORE INTO watched_skipped (key, skipped) VALUES (?, ?)', [(k, now) for k in keys])


def skipped() -> set[str]:
    """The events the user skipped before."""
    with connect() as db:
        return {r[0] for r in db.execute('SELECT key FROM watched_skipped')}


def start_progress(labels: list[str]) -> None:
    """A check begins: every site waits its turn."""
    with _progress_lock:
        _progress.clear()
        _progress['sites'] = {label: ('waiting', '') for label in labels}


def site_progress(label: str, state: str, detail: str = '') -> None:
    """A site's state: 'reading' (its pages and model answers are counted live from here), 'done', 'browser' or 'failed'."""
    job = jobs.current()
    with _progress_lock:
        _progress['sites'][label] = (state, detail)
        if state == 'reading':
            _progress['reading'] = (label, job, len(job.fetches) if job else 0, len(job.llm_calls) if job else 0)


def end_progress() -> None:
    """The check is over."""
    with _progress_lock:
        _progress.clear()


def _plural(count: int, word: str) -> str:
    """'1 page', '2 pages'."""
    return f'{count} {word}{"" if count == 1 else "s"}'


def progress() -> dict:
    """What the running check is doing, site by site; empty when none runs."""
    with _progress_lock:
        if not _progress:
            return {}
        sites = []
        for label, (state, detail) in _progress['sites'].items():
            if state == 'reading' and (reading := _progress.get('reading')) and reading[0] == label and reading[1]:
                _, job, fetches, answers = reading
                pages, asked = len(job.fetches) - fetches, len(job.llm_calls) - answers
                detail = ' · '.join([_plural(pages, 'page') + ' read'] + ([_plural(asked, 'model answer')] if asked else []))
            sites.append({'site': label, 'state': state, 'detail': detail})
        return {'sites': sites}
