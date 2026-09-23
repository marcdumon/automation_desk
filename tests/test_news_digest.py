"""A digest run: one job, since the previous digest, budget from today's spending, stored with problems and suggestions."""

import threading
from datetime import datetime, timedelta

import pytest

from automation_desk import ledger
from automation_desk.groups.news import digest as module
from automation_desk.groups.news import store
from automation_desk.groups.news.collect import Article, Collected
from automation_desk.groups.news.store import ArticleRecord, StoryRecord
from automation_desk.groups.news.summarise import Summarised

from .conftest import TZ

NOW = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)


@pytest.fixture
def parts(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stand-in collect, summarise and merge that record what they were given."""
    seen: dict = {}
    art = Article('https://k.be/1', 1, 'Krant', 'Titel', NOW, 'Teaser', 'Tekst')

    def collect(now: datetime, http: object, allow_browser: bool) -> Collected:
        """One article and one problem."""
        seen.update(allow_browser=allow_browser)
        return Collected([art], ['B could not be read: 500'])

    def summarise(articles: list, subjects: list, budget: float, http: object = None, blocked: list | None = None) -> tuple:
        """One summary with a suggestion."""
        seen.update(budget=budget, summarise_http=http)
        return [Summarised(art, 'Samenvatting.', 'Other', 'Housing', False, '', 0)], []

    def merge(items: list, http: object = None) -> tuple:
        """One story."""
        seen['merge_http'] = http
        return [StoryRecord('Other', 'Titel', 'Samenvatting.', [ArticleRecord(art.link, 1, 'Titel', NOW, 'Teaser', False, '')])], []

    monkeypatch.setattr(module, 'collect', collect)
    monkeypatch.setattr(module, 'summarise', summarise)
    monkeypatch.setattr(module, 'merge_stories', merge)
    monkeypatch.setattr(ledger, 'cost_since', lambda group, since: 0.05)
    return seen


def test_a_digest_is_one_job_and_is_stored(parts: dict) -> None:
    digest_id = module.make_digest('button', allow_browser=True, now=NOW)
    stored = store.digest(digest_id)
    assert stored['problems'] == ['B could not be read: 500'] and stored['trigger'] == 'button'
    assert stored['covers_from'] == (NOW - timedelta(hours=24)).isoformat(), 'the first digest looks back 24 hours'
    assert (parts['summarise_http'], parts['merge_http']) == (None, None), (
        'model calls use their own client and its long timeout, not the 30-second page client')
    assert round(parts['budget'], 2) == 0.25, 'cap $0.30 minus $0.05 already spent today'
    assert [s['name'] for s in store.open_suggestions()] == ['Housing']
    job = ledger.detail(stored['job_id'])
    assert (job['group'], job['task_name']) == ('news', 'Make a digest')

    later = NOW + timedelta(days=1)
    later_id = module.make_digest('scheduled', allow_browser=False, now=later)
    assert store.digest(later_id)['covers_from'] == NOW.isoformat() and parts['allow_browser'] is False, (
        'the next digest starts where the last one ended')


def test_never_two_runs_at_once(parts: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    gate, started = threading.Event(), threading.Event()
    original = module.collect

    def slow(*args: object, **kwargs: object) -> Collected:
        """Hold the first run until the second one has asked."""
        started.set()
        gate.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'collect', slow)
    first = threading.Thread(target=module.make_digest, args=('button', True), kwargs={'now': NOW})
    first.start()
    started.wait(5)
    assert module.running()
    second_done = []
    second = threading.Thread(target=lambda: second_done.append(module.make_digest('button', True, now=NOW + timedelta(hours=1))))
    second.start()
    assert not second_done, 'the second request waits'
    gate.set()
    first.join(5)
    second.join(5)
    assert len(store.digests()) == 2 and not module.running()


def test_a_failed_digest_is_reported_until_one_succeeds(parts: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    good = module.collect

    def broken(now: datetime, http: object, allow_browser: bool) -> Collected:
        """Collection breaks."""
        raise RuntimeError('disk full')

    monkeypatch.setattr(module, 'collect', broken)
    with pytest.raises(RuntimeError):
        module.make_digest('button', allow_browser=True, now=NOW)
    assert module.failure() == 'disk full'
    monkeypatch.setattr(module, 'collect', good)
    module.make_digest('button', allow_browser=True, now=NOW)
    assert module.failure() == ''


def test_articles_on_a_blocked_topic_are_left_out(parts: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    sport = Article('https://k.be/2', 1, 'Krant', 'Doelpunt', NOW, 'Teaser', 'Tekst')
    news = Article('https://k.be/3', 1, 'Krant', 'Akkoord', NOW, 'Teaser', 'Tekst')
    store.add_source('https://k.be', 'Krant', 'https://k.be/rss', 'feed')
    store.set_blocked(['Sports'])
    given = {}
    monkeypatch.setattr(module, 'collect', lambda now, http, allow_browser: Collected([sport, news], []))

    def summarise(articles: list, subjects: list, budget: float, http: object = None, blocked: list | None = None) -> tuple:
        """The model files the goal under the blocked topic."""
        given['blocked'] = blocked
        return [Summarised(sport, 'Doelpunt.', 'Sports', '', False, '', 0),
                Summarised(news, 'Akkoord.', 'Other', '', False, '', 0)], []

    def merge(items: list, http: object = None) -> tuple:
        """One story per item."""
        given['merged'] = [i.article.link for i in items]
        return [StoryRecord('Other', 'Akkoord', 'Akkoord.', [ArticleRecord(news.link, 1, 'Akkoord', NOW, 'Teaser', False, '')])], []

    monkeypatch.setattr(module, 'summarise', summarise)
    monkeypatch.setattr(module, 'merge_stories', merge)
    stored = store.digest(module.make_digest('button', allow_browser=True, now=NOW))
    assert given == {'blocked': ['Sports'], 'merged': ['https://k.be/3']}
    assert [(a['link'], a['topic']) for a in stored['left_out']] == [('https://k.be/2', 'Sports')]
    assert stored['article_count'] == 1
