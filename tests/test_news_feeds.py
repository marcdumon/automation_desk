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
        assert find_feed('https://example.org', http) == ('Example News', 'https://example.org/feed.xml', 'feed'), (
            'the title naming the site itself (example.org) wins over the feed title')
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
    """A site that refuses programs gives no title: named after its domain (the site list shows each address)."""
    with serve({'/world': (401, '')}) as http:
        assert find_feed('https://www.wsj.com/world/', http) == ('wsj', '', 'frontpage')


def test_a_front_page_that_refuses_programs_is_read_through_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    home = (FIX / 'home.html').read_text()
    monkeypatch.setattr(feeds, 'browser_page', lambda url: Page(url='https://example.org/', html=home, via='browser'))
    with serve({'/': (401, '')}) as http:
        items = front_page_links('https://example.org', http, allow_browser=True)
    assert [i.link for i in items] == ['https://example.org/2026/09/24/city-council-approves-new-cycling-plan']
    with serve({'/': (401, '')}) as http, pytest.raises(httpx.HTTPStatusError):
        front_page_links('https://example.org', http)


def test_a_sections_own_feed_comes_before_the_sites_root_feed() -> None:
    """theins.press/en has its English feed at /en/feed; the root /feed is the Russian one."""
    with serve({'/en': (200, '<html><title>THE INSIDER</title></html>'), '/en/feed': (200, (FIX / 'rss.xml').read_text()),
                '/feed': (200, (FIX / 'atom.xml').read_text())}) as http:
        assert find_feed('https://theins.press/en', http)[1] == 'https://theins.press/en/feed'


def test_a_bot_check_page_never_names_a_site() -> None:
    with serve({'/': (403, '<html><title>Just a moment...</title></html>')}) as http:
        assert find_feed('https://politico.com', http)[0] == 'politico'


@pytest.mark.parametrize(('title', 'site', 'name'), [
    ('The Jerusalem Post - All News from the Middle East, Israel, and the Jewish World', 'https://jpost.com', 'The Jerusalem Post'),
    ('Breaking News, Latest News and Videos | CNN', 'https://cnn.com', 'CNN'),
    ('BBC Home - Breaking News, World News, US News, Sports, Business, Innovation', 'https://bbc.com', 'BBC Home'),
    ('De Tijd - Financieel, economisch, en politiek nieuws', 'https://www.tijd.be', 'De Tijd'),
    ('VRT NWS Nieuws | Betrouwbaar Nieuws uit België & de Wereld', 'https://vrt.be/vrtnws/nl', 'VRT NWS Nieuws'),
    ('HLN:home', 'https://hln.be', 'HLN'),
    ('DM: homepage', 'https://demorgen.be', 'DM'),
    ('Just a moment...', 'https://politico.com', 'politico'),
    ('', 'https://wsj.com/world', 'wsj'),
    ('De Standaard', 'https://standaard.be', 'De Standaard'),
    ('Home - Financial Times', 'https://ft.com', 'Financial Times'),
])
def test_site_names_are_short(title: str, site: str, name: str) -> None:
    assert feeds.short_name(title, site) == name



def test_the_title_naming_the_site_wins_over_a_generic_feed_title() -> None:
    """The FT feed calls itself 'International homepage'; its page says 'Home - Financial Times' (ft = its initials)."""
    assert feeds._name('https://ft.com', 'International homepage', 'Home - Financial Times') == 'Financial Times'
