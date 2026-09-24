"""New articles of all sources, with ~500 words of their text, or their teaser."""

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from automation_desk.capture import CaptureError
from automation_desk.config import config
from automation_desk.groups.calendar.web_page import browser_page, download, full_description, is_blocked
from automation_desk.groups.news import store
from automation_desk.groups.news.feeds import Item, feed_items, front_page_links

SOURCE_WORKERS = 4
FIRST_READ_WINDOW = timedelta(hours=24)
LATER_READ_WINDOW = timedelta(days=7)


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


def _text(item: Item, http: httpx.Client, allow_browser: bool) -> tuple[str, str]:
    """About max_words words of the article, or its teaser with the reason the article itself could not be read."""
    words = config().news.max_words
    fallback = item.teaser or item.title or item.link
    try:
        response = download(item.link, http)
        if is_blocked(response):
            if not allow_browser:
                return fallback, 'site blocks programs'
            page_html = browser_page(item.link).html
        elif response.status_code == 402:
            return fallback, 'paywall: only the teaser is readable'
        elif not response.is_success:
            return fallback, f'page could not be read ({response.status_code})'
        else:
            page_html = response.text
    except (httpx.HTTPError, CaptureError):
        return fallback, 'page could not be read'
    soup = BeautifulSoup(page_html, 'lxml')
    for tag in soup(['script', 'style', 'noscript', 'nav', 'header', 'footer']):
        tag.decompose()
    text = full_description(soup, item.teaser)
    # CLAUDE> full_description falls back to the teaser itself when the page holds no article text (paywall, login)
    if not text or ' '.join(text.split()) == ' '.join(item.teaser.split()):
        return fallback, 'only the teaser is readable'
    return ' '.join(text.split()[:words]), ''


def _one_source(source: store.Source, now: datetime, http: httpx.Client, allow_browser: bool) -> tuple[list[Article], str]:
    """New articles of one source and a short result for the site list.

    A first read (until the site was once read well) takes only dated items of the last 24 hours and records the rest as
    seen, so a new site brings no backlog. Later reads take every unseen item of the last week, whatever the date of the
    previous digest, so an item listed late or a day the site was down loses nothing.
    """
    first_read = not source.last_checked
    found = feed_items(source.feed, http) if source.kind == 'feed' else front_page_links(source.site, http)
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
        text, reason = _text(item, http, allow_browser)
        articles.append(Article(item.link, source.id, source.name, item.title, item.published, item.teaser, text, reason))
    return articles, f'{len(articles)} new'


def site_label(site: str) -> str:
    """A site as people write it: tijd.be, not https://www.tijd.be/."""
    return site.removeprefix('https://').removeprefix('http://').removeprefix('www.').rstrip('/')


def collect(now: datetime, http: httpx.Client, allow_browser: bool,
            report: Callable[[str, str], None] | None = None) -> Collected:
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
        except (httpx.HTTPError, ValueError) as error:
            tell(site_label(source.site), 'could not be read')
            return source, error, str(error)

    with ThreadPoolExecutor(SOURCE_WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, run, s) for s in store.sources()]
        outcomes = [f.result() for f in futures]
    seen: set[str] = set()
    for source, articles, summary in outcomes:
        if isinstance(articles, Exception):
            result.problems.append(f'{source.name} could not be read: {summary}')
            store.set_source_result(source.id, f'could not be read: {summary}', ok=False)
            continue
        store.set_source_result(source.id, summary)
        for article in articles:
            if article.link not in seen:
                seen.add(article.link)
                result.articles.append(article)
    return result
