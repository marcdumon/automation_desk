"""New articles of all sources, each with the teaser the site lists for it, or only its title."""

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from automation_desk.capture import CaptureError, NeedsPerson
from automation_desk.config import config
from automation_desk.groups.calendar.web_page import BLOCKED_STATUS
from automation_desk.groups.news import store
from automation_desk.groups.news.feeds import feed_items, front_page_links, site_label

SOURCE_WORKERS = 4
FIRST_READ_WINDOW = timedelta(hours=24)
LATER_READ_WINDOW = timedelta(days=7)
NEEDS_CHECK = 'needs you to pass a check'


@dataclass(frozen=True)
class Article:
    """A new article with the text the model will read (or its teaser)."""

    link: str
    source_id: int
    source_name: str
    title: str
    published: datetime | None
    teaser: str
    text: str
    # CLAUDE> why `text` is the teaser rather than the article: 'site blocks programs', 'page could not be read'...
    teaser_reason: str = ''


@dataclass
class Collected:
    """What a collection run found."""

    articles: list[Article] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # CLAUDE> sites that show a human check: listed for the user, who can pass it and continue the digest
    needs_check: list[int] = field(default_factory=list)


def _usable(teaser: str, title: str) -> bool:
    """Whether a teaser says something: not empty, and not the title again."""
    teaser = ' '.join(teaser.split())
    return bool(teaser) and teaser != ' '.join(title.split())


def _one_source(source: store.Source, now: datetime, http: httpx.Client, allow_browser: bool) -> tuple[list[Article], str]:
    """New articles of one source and a short result for the site list.

    A first read (until the site was once read well) takes only dated items of the last 24 hours and records the rest as
    seen, so a new site brings no backlog. Later reads take every unseen item of the last week, whatever the date of the
    previous digest, so an item listed late or a day the site was down loses nothing.
    """
    first_read = not source.last_checked
    found = feed_items(source.feed, http) if source.kind == 'feed' else front_page_links(source.site, http, allow_browser)
    listed = list({i.link: i for i in found}.values())
    known = store.known_links([i.link for i in listed])
    unseen = [i for i in listed if i.link not in known]
    if source.kind == 'frontpage' and first_read:
        store.mark_seen(source.id, [i.link for i in unseen])
        return [], f'first read: {len(unseen)} link recorded, new ones from the next digest'
    earliest = now - (FIRST_READ_WINDOW if first_read else LATER_READ_WINDOW)
    fresh = [i for i in unseen if (i.published >= earliest if i.published else not first_read)]
    taken = {i.link for i in fresh}
    store.mark_seen(source.id, [i.link for i in unseen if i.link not in taken])
    articles = []
    for item in fresh:
        # CLAUDE> the text shown is the teaser the site lists; without one, the title alone (article pages are never read)
        text = ' '.join(item.teaser.split()[:config().news.max_words]) if _usable(item.teaser, item.title) else ''
        articles.append(Article(item.link, source.id, source.name, item.title, item.published, item.teaser, text))
    return articles, f'{len(articles)} new'


def _plain(error: Exception) -> str:
    """Why a site could not be read, as a person would say it."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in BLOCKED_STATUS:
            return f"blocks bots ({status}): it can't be read automatically"
        return f'page not found ({status})' if status in (404, 410) else f'answered with error {status}'
    if isinstance(error, CaptureError):
        return f'blocks bots, and reading it through your browser failed too ({error})'
    if isinstance(error, httpx.TooManyRedirects):
        return 'keeps redirecting'
    if isinstance(error, httpx.TransportError):
        return 'did not answer'
    return f'could not be read ({error})'


def collect(now: datetime, http: httpx.Client, allow_browser: bool,
            report: Callable[[str, str], None] | None = None, only: set[int] | None = None) -> Collected:
    """New articles of every source, four sources at a time; a source that fails becomes a problem, not a failure.

    `report(site, status)` hears what each site is doing, for the progress the page shows.
    """
    result = Collected()
    tell = report or (lambda site, status: None)

    def run(source: store.Source) -> tuple[store.Source, list[Article] | Exception, str]:
        tell(site_label(source.site), 'reading…')
        try:
            articles, summary = _one_source(source, now, http, allow_browser)
            tell(site_label(source.site), summary)
            return source, articles, summary
        except NeedsPerson as error:
            tell(site_label(source.site), NEEDS_CHECK)
            return source, error, NEEDS_CHECK
        except (httpx.HTTPError, ValueError, CaptureError) as error:
            tell(site_label(source.site), _plain(error))
            return source, error, _plain(error)

    # CLAUDE> sites on one domain run one after another: several reads of wsj.com at once look like a bot and get refused
    by_domain: dict[str, list[store.Source]] = {}
    for source in (s for s in store.sources() if only is None or s.id in only):
        by_domain.setdefault(site_label(source.site).split('/')[0], []).append(source)

    def run_domain(sources: list[store.Source]) -> list[tuple[store.Source, list[Article] | Exception, str]]:
        """One domain's sites, in order."""
        return [run(source) for source in sources]

    with ThreadPoolExecutor(SOURCE_WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, run_domain, group) for group in by_domain.values()]
        outcomes = [outcome for f in futures for outcome in f.result()]
    seen: set[str] = set()
    for source, articles, summary in outcomes:
        if isinstance(articles, NeedsPerson):
            result.needs_check.append(source.id)
            store.set_source_result(source.id, NEEDS_CHECK, ok=False)
            continue
        if isinstance(articles, Exception):
            result.problems.append(f'{site_label(source.site)} {summary}.')
            store.set_source_result(source.id, summary, ok=False)
            continue
        store.set_source_result(source.id, summary)
        for article in articles:
            if article.link not in seen:
                seen.add(article.link)
                result.articles.append(article)
    return result
