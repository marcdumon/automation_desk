"""One digest run: collect, summarise within the day's budget, merge stories, store. Recorded as one job."""

import threading
from datetime import datetime, timedelta

import httpx

from automation_desk import jobs, ledger
from automation_desk.groups.news import store
from automation_desk.groups.news.collect import collect
from automation_desk.groups.news.merge import merge_stories
from automation_desk.groups.news.store import DigestRecord
from automation_desk.groups.news.summarise import summarise

GROUP = 'news'
_lock = threading.Lock()
_active = threading.Event()


def running() -> bool:
    """Whether a digest is being made right now."""
    return _active.is_set()


def make_digest(trigger: str, allow_browser: bool, now: datetime | None = None) -> int:
    """Make and store a digest of everything since the previous one; a second call waits for a running one."""
    with _lock:
        _active.set()
        try:
            return _make(trigger, allow_browser, now or datetime.now().astimezone())
        finally:
            _active.clear()


def _make(trigger: str, allow_browser: bool, now: datetime) -> int:
    """The run itself, as one job."""
    since = store.latest_made_at() or now - timedelta(hours=24)
    job = jobs.Job(group=GROUP, sentence=f'News digest ({trigger})', task_id='make_digest', task_name='Make a digest')
    with jobs.run(job), httpx.Client(timeout=30.0) as http:
        found = collect(since, now, http, allow_browser)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec='seconds')
        budget = max(0.0, store.cap() - ledger.cost_since(GROUP, today))
        items, summary_problems = summarise(found.articles, store.subjects(), budget, http)
        stories, merge_problems = merge_stories(items, http)
        suggestions: dict[str, list[str]] = {}
        for i in items:
            if i.suggestion:
                suggestions.setdefault(i.suggestion, []).append(i.article.title)
        record = DigestRecord(made_at=now, covers_from=since, trigger=trigger, job_id=job.id,
                              problems=[*found.problems, *summary_problems, *merge_problems], stories=stories,
                              suggestions=suggestions)
        digest_id = store.save_digest(record)
        job.message = f'{len(found.articles)} article(s), {len(stories)} stor(y/ies)'
        job.preview = {'summary': job.message, 'columns': [], 'rows': [], 'notes': record.problems, 'read_only': True}
    return digest_id
