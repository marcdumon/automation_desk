"""News storage: sources, subjects, seen links, digests with stories, suggestions, cap."""

from datetime import datetime

from automation_desk import ledger
from automation_desk.groups.news import store
from automation_desk.groups.news.store import ArticleRecord, DigestRecord, LeftOut, StoryRecord

from .conftest import TZ


def test_sources_subjects_and_cap() -> None:
    first = store.add_source('https://www.standaard.be', 'De Standaard', 'https://www.standaard.be/rss', 'feed')
    store.add_source('https://example.org', 'Example', '', 'frontpage')
    store.set_source_result(first, '12 new')
    assert [(s.name, s.kind, s.last_result) for s in store.sources()] == [('De Standaard', 'feed', '12 new'),
                                                                        ('Example', 'frontpage', '')]
    store.remove_source(first)
    assert [s.name for s in store.sources()] == ['Example']
    store.set_subjects(['Politics BE', 'AI'])
    assert store.subjects() == ['Politics BE', 'AI']
    assert store.cap() == 0.30
    store.set_cap(0.5)
    assert store.cap() == 0.5


def test_digest_with_stories_and_known_links() -> None:
    source = store.add_source('https://a.be', 'A', 'https://a.be/feed', 'feed')
    store.mark_seen(source, ['https://a.be/old'])
    made = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    record = DigestRecord(made_at=made, covers_from=made.replace(day=23), trigger='scheduled', job_id='job1', problems=['B down'],
                          stories=[StoryRecord(subject='AI', title='Model X', summary='Samenvatting.', articles=[
                              ArticleRecord(link='https://a.be/x', source_id=source, title='Model X', published=made,
                                            teaser='t', from_teaser=False, reason='')])],
                          suggestions={'Housing': ['Huurprijzen stijgen']})
    digest_id = store.save_digest(record)
    assert store.known_links(['https://a.be/x', 'https://a.be/old', 'https://a.be/new']) == {'https://a.be/x', 'https://a.be/old'}
    stored = store.digest(digest_id)
    assert stored['problems'] == ['B down'] and stored['subjects'][0]['subject'] == 'AI'
    assert stored['subjects'][0]['stories'][0]['articles'][0]['source'] == 'A'
    assert store.latest_made_at() == made
    assert [s['name'] for s in store.open_suggestions()] == ['Housing']
    store.set_suggestion('Housing', 'accepted')
    assert store.open_suggestions() == [] and store.subjects()[-1] == 'Housing'


def test_a_story_links_its_lead_article_first_and_shows_its_cost() -> None:
    source = store.add_source('https://a.be', 'A', 'https://a.be/feed', 'feed')
    made = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    early, late = made.replace(hour=5), made.replace(hour=6)
    story = StoryRecord(subject='AI', title='Het lange stuk', summary='S.', articles=[
        ArticleRecord('https://b.fr/x', source, 'Le court', early, 't', False, ''),
        ArticleRecord('https://a.be/x', source, 'Het lange stuk', late, 't', False, '')])
    record = DigestRecord(made_at=made, covers_from=made.replace(day=23), trigger='button', job_id='job9', problems=[],
                          stories=[story], suggestions={'Cycling': [f'Titel {n}' for n in range(8)]})
    digest_id = store.save_digest(record)
    with ledger.connect(write=True) as db:
        db.execute("INSERT INTO jobs (id, grp, sentence, started) VALUES ('job9', 'news', 'News digest', '2026-09-24T07:00:00')")
        db.execute("INSERT INTO llm_calls (job_id, seq, cost_usd) VALUES ('job9', 0, 0.0125)")
    stored = store.digest(digest_id)
    assert stored['subjects'][0]['stories'][0]['articles'][0]['link'] == 'https://a.be/x', 'the title links to its own article'
    assert stored['cost_usd'] == 0.0125
    assert len(store.open_suggestions()[0]['examples']) == 8, 'all headlines, so the page can count them'


def test_blocked_topics_and_left_out_articles() -> None:
    source = store.add_source('https://a.be', 'A', 'https://a.be/feed', 'feed')
    store.set_blocked(['Sports', ' TV  programmes ', 'sports', ''])
    assert store.blocked() == ['Sports', 'TV programmes']
    made = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    record = DigestRecord(made_at=made, covers_from=made.replace(day=23), trigger='scheduled', job_id='j', problems=[], stories=[],
                          suggestions={'Sports': ['Doelpunt'], 'Housing': ['Huur']},
                          left_out=[LeftOut('https://a.be/goal', source, 'Doelpunt in de 90ste minuut', 'Sports')])
    digest_id = store.save_digest(record)
    assert store.digest(digest_id)['left_out'] == [
        {'link': 'https://a.be/goal', 'title': 'Doelpunt in de 90ste minuut', 'source': 'A', 'topic': 'Sports'}]
    assert store.known_links(['https://a.be/goal']) == {'https://a.be/goal'}, 'a left-out article never comes back'
    assert [s['name'] for s in store.open_suggestions()] == ['Housing'], 'a blocked topic is never suggested'


def test_deleting_a_story_keeps_its_articles_seen() -> None:
    source = store.add_source('https://a.be', 'A', 'https://a.be/feed', 'feed')
    made = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    art = lambda link: ArticleRecord(link, source, 'T', made, 't', False, '')  # noqa: E731
    record = DigestRecord(made_at=made, covers_from=made.replace(day=23), trigger='button', job_id='j', problems=[], stories=[
        StoryRecord('AI', 'Weg', 'S.', [art('https://a.be/1'), art('https://a.be/2')]), StoryRecord('AI', 'Blijft', 'S.', [art('https://a.be/3')])])
    digest_id = store.save_digest(record)
    story_id = store.digest(digest_id)['subjects'][0]['stories'][0]['id']
    assert store.delete_story(story_id) is True
    stored = store.digest(digest_id)
    assert [s['title'] for g in stored['subjects'] for s in g['stories']] == ['Blijft']
    assert (stored['story_count'], stored['article_count']) == (1, 1)
    assert store.known_links(['https://a.be/1', 'https://a.be/2']) == {'https://a.be/1', 'https://a.be/2'}, 'never back in a digest'
    assert store.delete_story('nope') is False


def test_a_suggestion_can_be_blocked() -> None:
    store.set_blocked(['Sports'])
    made = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    store.save_digest(DigestRecord(made_at=made, covers_from=made.replace(day=23), trigger='button', job_id='j', problems=[],
                                   stories=[], suggestions={'Celebrities': ['Ster trouwt']}))
    store.set_suggestion('Celebrities', 'blocked')
    assert store.blocked() == ['Sports', 'Celebrities'] and store.open_suggestions() == []
    assert 'Celebrities' not in store.subjects()


def _two_subject_digest(made: datetime) -> tuple[int, int]:
    """A digest with an AI story (two articles) and a Politics story; returns (digest id, source id)."""
    source = store.add_source('https://a.be', 'A', 'https://a.be/feed', 'feed')
    art = lambda link: ArticleRecord(link, source, 'T', made, 't', False, '')  # noqa: E731
    record = DigestRecord(made_at=made, covers_from=made.replace(day=23), trigger='button', job_id='j', problems=[], stories=[
        StoryRecord('AI', 'Een', 'S.', [art('https://a.be/1'), art('https://a.be/2')]), StoryRecord('Politics', 'Twee', 'S.', [art('https://a.be/3')])],
        left_out=[LeftOut('https://a.be/sport', source, 'Goal', 'Sports')])
    return store.save_digest(record), source


def test_deleting_a_whole_subject() -> None:
    digest_id, _ = _two_subject_digest(datetime(2026, 9, 24, 7, 0, tzinfo=TZ))
    assert store.delete_subject(digest_id, 'AI') == 1
    stored = store.digest(digest_id)
    assert [g['subject'] for g in stored['subjects']] == ['Politics'] and stored['article_count'] == 1
    assert store.known_links(['https://a.be/1', 'https://a.be/2']) == {'https://a.be/1', 'https://a.be/2'}
    assert store.delete_subject(digest_id, 'Nope') == 0


def test_deleting_a_digest_keeps_its_articles_seen_and_its_time() -> None:
    made = datetime(2026, 9, 24, 7, 0, tzinfo=TZ)
    digest_id, _ = _two_subject_digest(made)
    assert store.delete_digest(digest_id) is True
    assert store.digest(digest_id) is None and store.digests() == []
    assert store.known_links(['https://a.be/1', 'https://a.be/3', 'https://a.be/sport']) == {
        'https://a.be/1', 'https://a.be/3', 'https://a.be/sport'}, 'its articles, left-out ones included, never come back'
    assert store.latest_made_at() == made, 'the next digest starts where the deleted one ended'
    assert store.delete_digest(digest_id) is False
