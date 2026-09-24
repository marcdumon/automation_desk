"""News feeds: find a site's RSS/Atom feed, read its items, and fall back to article links on the front page."""

import html
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from automation_desk.groups.calendar.web_page import browser_page, download, is_blocked

COMMON_FEED_PATHS = ['/feed', '/rss', '/rss.xml', '/feed.xml', '/atom.xml', '/index.xml', '/feeds/posts/default']
TEASER_CHARS = 600
MIN_LINK_TITLE = 25


@dataclass(frozen=True)
class Item:
    """An article a feed or front page announces."""

    link: str
    title: str
    published: datetime | None
    teaser: str


def _plain(text: str) -> str:
    """Text without markup, entities or runs of spaces."""
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', text or '')).split())


def _parse(content: bytes) -> feedparser.FeedParserDict | None:
    """A parsed feed, or None when the content is not a feed with entries."""
    parsed = feedparser.parse(content)
    return parsed if parsed.entries else None


def site_label(site: str) -> str:
    """A site as people write it: tijd.be or wsj.com/world, not https://www.tijd.be/."""
    return site.removeprefix('https://').removeprefix('http://').removeprefix('www.').rstrip('/')


def find_feed(site: str, http: httpx.Client) -> tuple[str, str, str]:
    """The site's name, feed address and kind: the address itself when it is a feed, else the feed the page links to,
    else a common feed path, else its front page."""
    response = download(site, http)
    # CLAUDE> a section feed typed as the site (rss.nytimes.com/.../World.xml) is taken as it is
    if response.status_code == 200 and (itself := _parse(response.content)):
        return itself.feed.get('title', '').strip() or site_label(site), site, 'feed'
    soup = BeautifulSoup(response.text, 'lxml')
    name = (soup.title.string or '').strip() if soup.title else ''
    candidates = [urljoin(str(response.url), link['href']) for link in soup.find_all('link', href=True)
                  if 'alternate' in (link.get('rel') or []) and re.search(r'(rss|atom)\+xml', link.get('type', ''))]
    candidates += [urljoin(site, path) for path in COMMON_FEED_PATHS]
    for candidate in dict.fromkeys(candidates):
        answer = download(candidate, http)
        if answer.status_code == 200 and (parsed := _parse(answer.content)):
            return parsed.feed.get('title', '').strip() or name or site_label(site), candidate, 'feed'
    return name or site_label(site), '', 'frontpage'


def feed_items(feed_url: str, http: httpx.Client) -> list[Item]:
    """The items of a feed, in feed order, each with its publish time when the feed gives one."""
    response = download(feed_url, http)
    response.raise_for_status()
    parsed = _parse(response.content)
    items = []
    for entry in parsed.entries if parsed else []:
        stamp = entry.get('published_parsed') or entry.get('updated_parsed')
        published = datetime.fromtimestamp(timegm(stamp), UTC) if stamp else None
        link = urljoin(feed_url, entry.get('link', ''))
        if link:
            items.append(Item(link, _plain(entry.get('title', '')), published, _plain(entry.get('summary', ''))[:TEASER_CHARS]))
    return items


def front_page_links(site: str, http: httpx.Client, allow_browser: bool = False) -> list[Item]:
    """Links on the front page that look like articles: same site, and a title of at least MIN_LINK_TITLE characters.

    A front page that refuses programs (wsj.com) is read through the user's browser when that is allowed.
    """
    response = download(site, http)
    if is_blocked(response) and allow_browser:
        page = browser_page(site)
        url, text = page.url, page.html
    else:
        response.raise_for_status()
        url, text = str(response.url), response.text
    host = urlparse(url).netloc
    items: dict[str, Item] = {}
    for anchor in BeautifulSoup(text, 'lxml').find_all('a', href=True):
        link = urljoin(url, anchor['href']).split('#')[0]
        title = ' '.join(anchor.get_text(' ').split())
        if urlparse(link).netloc == host and len(title) >= MIN_LINK_TITLE and link not in items:
            items[link] = Item(link, title, None, '')
    return list(items.values())
