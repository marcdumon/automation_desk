"""One digest run: collect, summarise within the day's budget, merge stories, store. Recorded as one job."""

import math
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx

from automation_desk import jobs, stop
from automation_desk.groups.news import store
from automation_desk.groups.news.collect import Article, Collected, collect
from automation_desk.groups.news.merge import merge_stories
from automation_desk.groups.news.store import DigestRecord, LeftOut
from automation_desk.groups.news.summarise import batch_size, estimate, summarise

GROUP = 'news'
_lock = threading.Lock()
_active = threading.Event()
# CLAUDE> why the latest run failed, shown on the News page until a run succeeds
_failure: dict[str, str] = {}
# CLAUDE> what the running digest is doing, for the page: {'step': ..., 'sites': {site: status}}
_progress: dict = {}
_progress_lock = threading.Lock()


def running() -> bool:
    """Whether a digest is being made right now."""
    return _active.is_set()


def progress() -> dict:
    """What the running digest is doing: its step, and each site's status; empty when none runs."""
    with _progress_lock:
        return {'step': _progress['step'], 'sites': dict(_progress['sites'])} if _progress else {}


class Stopped(Exception):
    """The user pressed Stop: the run ends between two steps and saves nothing."""


STOPPED = 'you stopped it. Nothing is lost: the next digest takes the same new articles'


def _stop_here() -> None:
    """End the run when the user pressed Stop; called as each site and each sorting batch starts."""
    if stop.requested('news-digest'):
        raise Stopped


def _step(step: str) -> None:
    """Enter a step of the run."""
    _stop_here()
    with _progress_lock:
        _progress.setdefault('sites', {})
        _progress['step'] = step


def _site(site: str, status: str) -> None:
    """A site's status; the sites are read four at a time, so from several threads."""
    if status == 'reading…':
        _stop_here()
    with _progress_lock:
        _progress.setdefault('sites', {})[site] = status


def failure() -> str:
    """Why the latest digest run failed; empty when it succeeded."""
    return _failure.get('message', '')


def make_digest(trigger: str, now: datetime | None = None) -> int | None:
    """Make and store a digest of everything since the previous one; a second call waits for a running one.

    Never reads through the user's browser: every tab the extension opens brings the browser to the front. Sites that
    refuse programs are listed instead, for the user to pass their check and continue the digest.
    Returns the digest's id, or None when nothing new was found (then no digest is saved).
    """
    return _run(lambda: _make(trigger, now or datetime.now().astimezone()))


def continue_digest(digest_id: int, raise_cap: bool, now: datetime | None = None) -> None:
    """Continue a digest: read its sites that needed a human check (the user has passed it by now) and sort the articles it
    held back at the daily cap, raising the cap first to what they need when `raise_cap`. Their stories join the digest."""
    _run(lambda: _continue(digest_id, raise_cap, now or datetime.now().astimezone()))


def _run[T](work: Callable[[], T]) -> T:
    """One digest run at a time, with the page told it runs, what it does, and why it failed."""
    with _lock:
        _active.set()
        stop.begin('news-digest')
        try:
            result = work()
        except Stopped:
            # CLAUDE> articles are marked seen only when a digest is saved, so the next run finds them again
            _failure['message'] = STOPPED
            return None
        except Exception as error:
            _failure['message'] = str(error) or type(error).__name__
            raise
        finally:
            _active.clear()
            with _progress_lock:
                _progress.clear()
        _failure.clear()
        return result


def _today(now: datetime) -> str:
    """Midnight of `now`'s day, as the daily cap counts."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec='seconds')


def _as_dict(article: Article) -> dict:
    """A held-back article as the digest keeps it."""
    return {'link': article.link, 'source_id': article.source_id, 'source_name': article.source_name, 'title': article.title,
            'published': article.published.isoformat(timespec='seconds') if article.published else None,
            'teaser': article.teaser, 'text': article.text}


def _article(kept: dict) -> Article:
    """A held-back article, back as collected."""
    published = datetime.fromisoformat(kept['published']) if kept['published'] else None
    return Article(kept['link'], kept['source_id'], kept['source_name'], kept['title'], published, kept['teaser'], kept['text'])


def cap_to_sort(digest_id: int, now: datetime | None = None) -> float | None:
    """The daily cap that would let a digest's held-back articles be sorted; None when it holds none back."""
    held = [_article(kept) for kept in store.held(digest_id)]
    if not held:
        return None
    size = batch_size()
    need = sum(estimate(held[start:start + size]) for start in range(0, len(held), size))
    spent = store.digest_cost_since(_today(now or datetime.now().astimezone()))
    # CLAUDE> rounded up to whole cents, plus one cent for the estimate's margin
    return (math.ceil((spent + need) * 100) + 1) / 100


def _record(articles: list[Article], found: Collected, now: datetime, since: datetime, trigger: str, job: jobs.Job) -> DigestRecord:
    """Sort articles into subjects within today's budget and merge same-story ones: the digest's part of this run."""
    budget = max(0.0, store.cap() - store.digest_cost_since(_today(now)))
    # CLAUDE> model calls use their own client with its long timeout; page reads have theirs
    blocked = store.blocked()
    summarised, summary_problems = summarise(articles, store.subjects(), budget, blocked=blocked,
                                             report=lambda n, total: _step(f'Sorting into subjects: batch {n} of {total}'))
    held = [_as_dict(i.article) for i in summarised if not i.sorted]
    done = [i for i in summarised if i.sorted]
    items = [i for i in done if i.subject not in blocked]
    left_out = [LeftOut(i.article.link, i.article.source_id, i.article.title, i.subject) for i in done if i.subject in blocked]
    _step('Merging stories')
    stories, merge_problems = merge_stories(items) if items else ([], [])
    suggestions: dict[str, list[str]] = {}
    for i in items:
        if i.suggestion:
            suggestions.setdefault(i.suggestion, []).append(i.article.title)
    record = DigestRecord(made_at=now, covers_from=since, trigger=trigger, job_id=job.id,
                          problems=[*found.problems, *summary_problems, *merge_problems], stories=stories, suggestions=suggestions,
                          left_out=left_out, needs_check=found.needs_check, held=held, failed=found.failed)
    job.message = (f'{len(articles)} article(s), {len(stories)} stor(y/ies), {len(left_out)} left out (blocked topics), '
                   f'{len(held)} held back (cost cap), {len(found.needs_check)} site(s) block bots, '
                   f'{len(found.failed)} could not be read')
    job.preview = {'summary': job.message, 'columns': [], 'rows': [], 'notes': record.problems, 'read_only': True}
    return record


def _make(trigger: str, now: datetime) -> int | None:
    """The run itself, as one job."""
    since = store.latest_made_at() or now - timedelta(hours=24)
    job = jobs.Job(group=GROUP, sentence=f'News digest ({trigger})', task_id='make_digest', task_name='Make a digest')
    with jobs.run(job), httpx.Client(timeout=30.0) as http:
        _step('Reading sites')
        found = collect(now, http, False, report=_site)
        if not found.articles and not found.needs_check and not found.failed:
            store.record_nothing_new(now, since, found.problems)
            job.message = f"Nothing new since {since.strftime('%d %b %H:%M')}"
            job.preview = {'summary': job.message, 'columns': [], 'rows': [], 'notes': found.problems, 'read_only': True}
            return None
        return store.save_digest(_record(found.articles, found, now, since, trigger, job))


def _continue(digest_id: int, raise_cap: bool, now: datetime) -> None:
    """The follow-up run, as one job whose cost counts with the digest."""
    if raise_cap and (needed := cap_to_sort(digest_id, now)) is not None:
        store.set_cap(max(needed, store.cap()))
    check = {s.id for s in [*store.needs_check(digest_id), *store.failed_sites(digest_id)]}
    held = [_article(kept) for kept in store.held(digest_id)]
    job = jobs.Job(group=GROUP, sentence='News digest (continued)', task_id='make_digest', task_name='Continue a digest')
    with jobs.run(job), httpx.Client(timeout=30.0) as http:
        found = Collected()
        if check:
            _step('Reading those sites again')
            found = collect(now, http, True, report=_site, only=check)
        store.add_to_digest(digest_id, _record(held + found.articles, found, now, now, 'continued', job))
