"""Collecting new articles: only unseen links, the 24-hour rule, text or teaser, blocked sites without waiting."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from automation_desk.groups.news import collect as module
from automation_desk.groups.news import store
from automation_desk.groups.news.collect import collect
from automation_desk.groups.news.feeds import Item

NOW = datetime(2026, 9, 24, 7, 0, tzinfo=UTC)
ARTICLE = '<html><body><main><p>' + 'Woord ' * 800 + '</p><p>Tweede alinea.</p></main></body></html>'


def items(*specs: tuple[str, datetime | None]) -> list[Item]:
    """Feed items with a link and publish time."""
    return [Item(link, f'Titel {link[-1]}', when, f'Teaser {link[-1]}') for link, when in specs]


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stand-in feeds and article pages; 'blocked' links answer 403."""
    state = {'feeds': {}, 'blocked': set(), 'browser': []}
    monkeypatch.setattr(module, 'feed_items', lambda url, http: state['feeds'][url])
    monkeypatch.setattr(module, 'front_page_links', lambda url, http: state['feeds'][url])

    def download(url: str, http: object) -> httpx.Response:
        """403 for blocked links, the article page for the rest."""
        request = httpx.Request('GET', url)
        return httpx.Response(403 if url in state['blocked'] else 200, text=ARTICLE, request=request)

    def browser_page(url: str) -> object:
        """Record that the browser was asked."""
        state['browser'].append(url)
        raise AssertionError('an unattended run must not wait on the browser')

    monkeypatch.setattr(module, 'download', download)
    monkeypatch.setattr(module, 'browser_page', browser_page)
    return state


def test_new_links_only_and_the_first_read_rule(web: dict) -> None:
    feed = store.add_source('https://krant.be', 'Krant', 'https://krant.be/rss', 'feed')
    front = store.add_source('https://blog.org', 'Blog', '', 'frontpage')
    web['feeds']['https://krant.be/rss'] = items(('https://krant.be/a/1', NOW - timedelta(hours=2)),
                                                 ('https://krant.be/a/2', None),
                                                 ('https://krant.be/a/3', NOW - timedelta(days=3)))
    web['feeds']['https://blog.org'] = items(('https://blog.org/p/1', None))
    got = collect(NOW, httpx.Client(), allow_browser=False)
    assert [a.link for a in got.articles] == ['https://krant.be/a/1'], (
        'first read of a feed: only items of the last 24 hours; undated and older ones are recorded as seen')
    assert store.known_links(['https://krant.be/a/2', 'https://krant.be/a/3', 'https://blog.org/p/1']) == {
        'https://krant.be/a/2', 'https://krant.be/a/3', 'https://blog.org/p/1'}
    assert len(got.articles[0].text.split()) == 500, 'at most 500 words go to the model'
    assert [s.last_result for s in store.sources()] == ['1 new', 'first read: 1 link recorded, new ones from the next digest']
    assert feed and front
    web['feeds']['https://krant.be/rss'] = items(('https://krant.be/a/1', NOW - timedelta(hours=2)), ('https://krant.be/a/4', None))
    store.mark_seen(feed, [a.link for a in got.articles])
    again = collect(NOW + timedelta(hours=1), httpx.Client(), allow_browser=False)
    assert [a.link for a in again.articles] == ['https://krant.be/a/4'], 'a new undated item is picked up on later reads'
    store.mark_seen(feed, ['https://krant.be/a/4'])
    assert collect(NOW + timedelta(hours=2), httpx.Client(), allow_browser=False).articles == [], 'and never repeated'


def test_later_reads_keep_unseen_items_whatever_the_previous_digest(web: dict) -> None:
    store.add_source('https://krant.be', 'Krant', 'https://krant.be/rss', 'feed')
    web['feeds']['https://krant.be/rss'] = []
    collect(NOW, httpx.Client(), allow_browser=False)
    web['feeds']['https://krant.be/rss'] = items(('https://krant.be/a/5', NOW - timedelta(days=2)),
                                                 ('https://krant.be/a/6', NOW - timedelta(days=30)))
    got = collect(NOW + timedelta(days=1), httpx.Client(), allow_browser=False)
    assert [a.link for a in got.articles] == ['https://krant.be/a/5'], (
        'an item listed late, or of a day the site was down, still comes; month-old ones do not')


def test_a_failed_first_read_stays_a_first_read(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://blog.org', 'Blog', '', 'frontpage')

    def down(url: str, http: object) -> list[Item]:
        """The site does not answer."""
        raise httpx.ConnectError('no route')

    monkeypatch.setattr(module, 'front_page_links', down)
    collect(NOW, httpx.Client(), allow_browser=False)
    monkeypatch.setattr(module, 'front_page_links', lambda url, http: items(('https://blog.org/p/1', None)))
    got = collect(NOW + timedelta(days=1), httpx.Client(), allow_browser=False)
    assert got.articles == [], "a front page's links become articles only after one good read"


def test_unreadable_pages_use_the_teaser_and_say_why(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://k.be', 'K', 'https://k.be/rss', 'feed')
    web['feeds']['https://k.be/rss'] = items(*((f'https://k.be/x/{n}', NOW - timedelta(hours=1)) for n in (1, 2, 3)))

    def download(url: str, http: object) -> httpx.Response:
        """A missing page, a page without article text, and one that times out."""
        request = httpx.Request('GET', url)
        if url.endswith('3'):
            raise httpx.ReadTimeout('slow', request=request)
        body = '<html><body><p>Log in to read.</p></body></html>'
        return httpx.Response(404 if url.endswith('1') else 200, text=body, request=request)

    monkeypatch.setattr(module, 'download', download)
    got = collect(NOW, httpx.Client(), allow_browser=False)
    assert [(a.text, a.teaser_reason) for a in got.articles] == [
        ('Teaser 1', 'page could not be read (404)'), ('Teaser 2', 'only the teaser is readable'),
        ('Teaser 3', 'page could not be read')]


def test_a_link_in_two_feeds_is_taken_once(web: dict) -> None:
    store.add_source('https://a.be', 'A', 'https://a.be/rss', 'feed')
    store.add_source('https://b.be', 'B', 'https://b.be/rss', 'feed')
    shared = ('https://news.be/shared', NOW - timedelta(hours=1))
    web['feeds']['https://a.be/rss'] = items(shared)
    web['feeds']['https://b.be/rss'] = items(shared)
    got = collect(NOW, httpx.Client(), allow_browser=False)
    assert [a.link for a in got.articles] == ['https://news.be/shared']


def test_blocked_site_uses_the_teaser_at_once_when_unattended(web: dict) -> None:
    store.add_source('https://k.be', 'K', 'https://k.be/rss', 'feed')
    web['feeds']['https://k.be/rss'] = items(('https://k.be/x/9', NOW - timedelta(hours=1)))
    web['blocked'].add('https://k.be/x/9')
    got = collect(NOW, httpx.Client(), allow_browser=False)
    article = got.articles[0]
    assert (article.text, article.teaser_reason) == ('Teaser 9', 'site blocks programs') and web['browser'] == []


def test_a_broken_feed_is_a_problem_not_a_failure(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://down.be', 'Down', 'https://down.be/rss', 'feed')

    def broken(url: str, http: object) -> list[Item]:
        """A feed that cannot be read."""
        raise httpx.ConnectError('no route')

    monkeypatch.setattr(module, 'feed_items', broken)
    got = collect(NOW, httpx.Client(), allow_browser=False)
    assert got.articles == [] and got.problems == ['Down could not be read: no route']
