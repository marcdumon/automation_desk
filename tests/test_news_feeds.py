"""Feeds: finding them, reading RSS and Atom, and front-page article links."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from automation_desk.groups.calendar.web_page import Page
from automation_desk.groups.news import feeds
from automation_desk.groups.news.feeds import feed_items, find_feed, front_page_links

FIX = Path(__file__).parent / 'fixtures' / 'news'


def serve(routes: dict[str, tuple[int, str]]) -> httpx.Client:
    """An HTTP client answering from `routes` (path -> status, body); anything else is 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(request.url.path, (404, 'not found'))
        return httpx.Response(status, text=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_find_feed_from_the_page_then_common_paths_then_front_page() -> None:
    home = (FIX / 'home.html').read_text()
    with serve({'/': (200, home), '/feed.xml': (200, (FIX / 'rss.xml').read_text())}) as http:
        assert find_feed('https://example.org', http) == ('De Voorbeeldkrant', 'https://example.org/feed.xml', 'feed')
    with serve({'/': (200, '<html><title>Blog</title></html>'), '/rss': (200, (FIX / 'rss.xml').read_text())}) as http:
        assert find_feed('https://example.org', http)[1:] == ('https://example.org/rss', 'feed')
    with serve({'/': (200, '<html><head><title>No Feed Site</title></head></html>')}) as http:
        assert find_feed('https://example.org', http) == ('No Feed Site', '', 'frontpage')


def test_feed_items_rss_and_atom() -> None:
    with serve({'/rss': (200, (FIX / 'rss.xml').read_text()), '/atom': (200, (FIX / 'atom.xml').read_text())}) as http:
        rss = feed_items('https://krant.be/rss', http)
        atom = feed_items('https://blog.fr/atom', http)
    assert [i.link for i in rss] == ['https://krant.be/a/1', 'https://krant.be/a/2', 'https://krant.be/a/3']
    assert rss[0].published == datetime(2026, 9, 23, 16, 0, tzinfo=UTC) and rss[1].published is None
    assert rss[0].teaser == 'Na lange onderhandelingen ...'
    assert (atom[0].title, atom[0].teaser) == ('Un nouveau modèle', 'Résumé court.')


def test_front_page_links_are_long_titled_same_site_links() -> None:
    with serve({'/': (200, (FIX / 'home.html').read_text())}) as http:
        items = front_page_links('https://example.org', http)
    assert [i.link for i in items] == ['https://example.org/2026/09/24/city-council-approves-new-cycling-plan']


def test_a_feed_address_given_as_the_site_is_the_feed() -> None:
    with serve({'/services/xml/rss/nyt/World.xml': (200, (FIX / 'rss.xml').read_text())}) as http:
        name, feed, kind = find_feed('https://rss.nytimes.com/services/xml/rss/nyt/World.xml', http)
    assert (feed, kind) == ('https://rss.nytimes.com/services/xml/rss/nyt/World.xml', 'feed') and name


def test_a_section_without_a_title_is_named_by_its_address() -> None:
    """A site that refuses programs gives no title: 'wsj.com/world', not 'wsj.com' for every section."""
    with serve({'/world': (401, '')}) as http:
        assert find_feed('https://www.wsj.com/world/', http) == ('wsj.com/world', '', 'frontpage')


def test_a_front_page_that_refuses_programs_is_read_through_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    home = (FIX / 'home.html').read_text()
    monkeypatch.setattr(feeds, 'browser_page', lambda url: Page(url='https://example.org/', html=home, via='browser'))
    with serve({'/': (401, '')}) as http:
        items = front_page_links('https://example.org', http, allow_browser=True)
    assert [i.link for i in items] == ['https://example.org/2026/09/24/city-council-approves-new-cycling-plan']
    with serve({'/': (401, '')}) as http, pytest.raises(httpx.HTTPStatusError):
        front_page_links('https://example.org', http)
