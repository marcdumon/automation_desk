"""New articles of all sources since the previous digest, with ~500 words of their text, or their teaser."""

import contextvars
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
    blocked: bool = False


@dataclass
class Collected:
    """What a collection run found."""

    articles: list[Article] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _text(item: Item, http: httpx.Client, allow_browser: bool) -> tuple[str, bool]:
    """About max_words words of the article, and whether the site blocked it; the teaser when nothing better is readable."""
    words = config().news.max_words
    try:
        response = download(item.link, http)
        blocked = is_blocked(response)
        if blocked and not allow_browser:
            return item.teaser or item.title, True
        page_html = browser_page(item.link).html if blocked else response.text
    except (httpx.HTTPError, CaptureError):
        return item.teaser or item.title, False
    soup = BeautifulSoup(page_html, 'lxml')
    for tag in soup(['script', 'style', 'noscript', 'nav', 'header', 'footer']):
        tag.decompose()
    text = full_description(soup, item.teaser) or item.teaser or item.title
    return ' '.join(text.split()[:words]), False


def _one_source(source: store.Source, since: datetime, now: datetime, http: httpx.Client,
                allow_browser: bool) -> tuple[list[Article], str]:
    """New articles of one source and a short result for the site list."""
    first_read = not source.last_checked
    found = feed_items(source.feed, http) if source.kind == 'feed' else front_page_links(source.site, http)
    unseen = [i for i in dict((i.link, i) for i in found).values() if i.link not in store.known_links([i.link for i in found])]
    if source.kind == 'frontpage' and first_read:
        store.mark_seen(source.id, [i.link for i in unseen])
        return [], f'first read: {len(unseen)} link recorded, new ones from the next digest'
    earliest = now - FIRST_READ_WINDOW if first_read else since
    fresh = [i for i in unseen if i.published is None or i.published >= earliest]
    articles = []
    for item in fresh:
        text, blocked = _text(item, http, allow_browser)
        articles.append(Article(item.link, source.id, source.name, item.title, item.published, item.teaser, text, blocked))
    return articles, f'{len(articles)} new'


def collect(since: datetime, now: datetime, http: httpx.Client, allow_browser: bool) -> Collected:
    """New articles of every source, four sources at a time; a source that fails becomes a problem, not a failure."""
    result = Collected()

    def run(source: store.Source) -> tuple[store.Source, list[Article] | Exception, str]:
        try:
            articles, summary = _one_source(source, since, now, http, allow_browser)
            return source, articles, summary
        except (httpx.HTTPError, ValueError) as error:
            return source, error, str(error)

    with ThreadPoolExecutor(SOURCE_WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, run, s) for s in store.sources()]
        outcomes = [f.result() for f in futures]
    seen: set[str] = set()
    for source, articles, summary in outcomes:
        if isinstance(articles, Exception):
            result.problems.append(f'{source.name} could not be read: {summary}')
            store.set_source_result(source.id, f'could not be read: {summary}')
            continue
        store.set_source_result(source.id, summary)
        for article in articles:
            if article.link not in seen:
                seen.add(article.link)
                result.articles.append(article)
    return result
