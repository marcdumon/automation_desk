"""News storage: sources, subjects, seen links, digests with stories, suggestions, cap."""

from datetime import datetime

from automation_desk.groups.news import store
from automation_desk.groups.news.store import ArticleRecord, DigestRecord, StoryRecord

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
