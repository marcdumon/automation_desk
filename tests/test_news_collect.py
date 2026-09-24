"""Collecting new articles: only unseen links, the 24-hour rule, text or teaser, blocked sites without waiting."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from automation_desk.capture import CaptureError
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
    monkeypatch.setattr(module, 'front_page_links', lambda url, http, allow_browser=False: state['feeds'][url])

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

    def down(url: str, http: object, allow_browser: bool = False) -> list[Item]:
        """The site does not answer."""
        raise httpx.ConnectError('no route')

    monkeypatch.setattr(module, 'front_page_links', down)
    collect(NOW, httpx.Client(), allow_browser=False)
    monkeypatch.setattr(module, 'front_page_links', lambda url, http, allow_browser=False: items(('https://blog.org/p/1', None)))
    got = collect(NOW + timedelta(days=1), httpx.Client(), allow_browser=False)
    assert got.articles == [], "a front page's links become articles only after one good read"


def test_unreadable_pages_use_the_teaser_and_say_why(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://k.be', 'K', 'https://k.be/rss', 'feed')
    web['feeds']['https://k.be/rss'] = items(*((f'https://k.be/x/{n}', NOW - timedelta(hours=1)) for n in (1, 2, 3, 4)))

    def download(url: str, http: object) -> httpx.Response:
        """A missing page, a page without article text, one that times out, and a paywall (402)."""
        request = httpx.Request('GET', url)
        if url.endswith('3'):
            raise httpx.ReadTimeout('slow', request=request)
        body = '<html><body><p>Log in to read.</p></body></html>'
        return httpx.Response({'1': 404, '4': 402}.get(url[-1], 200), text=body, request=request)

    monkeypatch.setattr(module, 'download', download)
    got = collect(NOW, httpx.Client(), allow_browser=False)
    assert [(a.text, a.teaser_reason) for a in got.articles] == [
        ('Teaser 1', 'page could not be read (404)'), ('Teaser 2', 'only the teaser is readable'),
        ('Teaser 3', 'page could not be read'), ('Teaser 4', 'paywall: only the teaser is readable')]


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
    assert got.articles == [] and got.problems == ['down.be did not answer.']


def test_each_site_reports_what_it_is_doing(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://www.tijd.be', 'De Tijd', 'https://www.tijd.be/rss', 'feed')
    store.add_source('https://down.be', 'Down', 'https://down.be/rss', 'feed')
    web['feeds']['https://www.tijd.be/rss'] = items(('https://www.tijd.be/a/1', NOW - timedelta(hours=1)))

    def feed(url: str, http: object) -> list[Item]:
        """Down does not answer."""
        if 'down' in url:
            raise httpx.ConnectError('no route')
        return web['feeds'][url]

    monkeypatch.setattr(module, 'feed_items', feed)
    reports: list[tuple[str, str]] = []
    collect(NOW, httpx.Client(), allow_browser=False, report=lambda site, status: reports.append((site, status)))
    assert sorted(reports) == [('down.be', 'did not answer'), ('down.be', 'reading…'),
                               ('tijd.be', '1 new'), ('tijd.be', 'reading…')]


def status(code: int) -> httpx.HTTPStatusError:
    """The error a site answering `code` raises."""
    return httpx.HTTPStatusError('x', request=httpx.Request('GET', 'https://wsj.com/'), response=httpx.Response(code))


@pytest.mark.parametrize(('error', 'said'), [
    (status(401), "refuses programs (401): it can't be read automatically"),
    (status(404), 'page not found (404)'),
    (status(500), 'answered with error 500'),
    (httpx.ConnectError('no route'), 'did not answer'),
    (httpx.TooManyRedirects('loop'), 'keeps redirecting'),
])
def test_a_site_that_cannot_be_read_is_said_plainly(web: dict, monkeypatch: pytest.MonkeyPatch, error: Exception,
                                                     said: str) -> None:
    store.add_source('https://wsj.com', 'wsj.com', '', 'frontpage')

    def fail(url: str, http: object, allow_browser: bool = False) -> list[Item]:
        """The site cannot be read."""
        raise error

    monkeypatch.setattr(module, 'front_page_links', fail)
    got = collect(NOW, httpx.Client(), allow_browser=False)
    assert got.problems == [f'wsj.com {said}.']
    assert store.sources()[0].last_result == said



def test_a_front_page_is_read_through_the_browser_only_when_allowed(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://wsj.com', 'wsj.com', '', 'frontpage')
    asked = []

    def front(url: str, http: object, allow_browser: bool = False) -> list[Item]:
        """Refuses programs; the browser read fails as well."""
        asked.append(allow_browser)
        raise CaptureError('The Automation desk page is not open in your browser.')

    monkeypatch.setattr(module, 'front_page_links', front)
    got = collect(NOW, httpx.Client(), allow_browser=True)
    assert asked == [True]
    assert got.problems == ['wsj.com refuses programs, and reading it through your browser failed too '
                            '(The Automation desk page is not open in your browser.).']


def test_sites_on_one_domain_are_read_one_after_another(web: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Four wsj.com sections read at the same moment look like a bot: WSJ let one through and refused three."""
    import threading
    import time

    for section in ('world', 'business', 'economy', 'finance'):
        store.add_source(f'https://wsj.com/{section}', f'WSJ {section}', '', 'frontpage')
    store.add_source('https://lesoir.be', 'Le Soir', '', 'frontpage')
    busy: dict[str, int] = {}
    most: dict[str, int] = {}
    lock = threading.Lock()

    def front(url: str, http: object, allow_browser: bool = False) -> list[Item]:
        """Count how many reads of each domain run at once."""
        host = url.split('/')[2]
        with lock:
            busy[host] = busy.get(host, 0) + 1
            most[host] = max(most.get(host, 0), busy[host])
        time.sleep(0.05)
        with lock:
            busy[host] -= 1
        return []

    monkeypatch.setattr(module, 'front_page_links', front)
    collect(NOW, httpx.Client(), allow_browser=False)
    assert most == {'wsj.com': 1, 'lesoir.be': 1}
    assert [s.last_result.startswith('first read') for s in store.sources()] == [True] * 5, 'every site was still read'
