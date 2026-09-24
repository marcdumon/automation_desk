"""Merging stories across batches: checked groups, majority subject, longest article's summary, failure keeps batch merges."""

from datetime import UTC, datetime

import pytest

from automation_desk.groups.news import merge as module
from automation_desk.groups.news.collect import Article
from automation_desk.groups.news.merge import MergeGroups, merge_stories
from automation_desk.groups.news.summarise import Summarised
from automation_desk.llm import LLMError


def item(n: int, subject: str, batch: int, text_words: int = 10) -> Summarised:
    """A summarised article number n."""
    article = Article(f'https://s.be/{n}', n, f'Site {n}', f'Titel {n}', datetime(2026, 9, 24, tzinfo=UTC), '',
                      'w ' * text_words)
    return Summarised(article, f'Samenvatting {n}', subject, '', False, '', batch)


def test_groups_across_batches_are_checked_and_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [item(1, 'Politics BE', 0), item(2, 'Politics BE', 0), item(3, 'Other', 1, text_words=50), item(4, 'AI', 1)]
    monkeypatch.setattr(module, 'ask', lambda *a, **k: MergeGroups(groups=[[1, 2, 3], [2, 99], [4, 4]]))
    stories, problems = merge_stories(items)
    merged = next(s for s in stories if len(s.articles) == 3)
    assert {a.link for a in merged.articles} == {'https://s.be/1', 'https://s.be/2', 'https://s.be/3'}
    assert merged.subject == 'Politics BE', 'the subject most of its articles got'
    assert merged.summary == 'Samenvatting 3', "the longest article's summary"
    assert [len(s.articles) for s in stories if s.subject == 'AI'] == [1]
    assert problems == ['1 same-story group from the model was dropped: it named articles that were not sent.'], (
        '[2, 99] names an unknown article; [4, 4] is one article')


def test_merger_failure_leaves_every_article_its_own_story(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*a: object, **k: object) -> MergeGroups:
        """The merger call fails every time."""
        raise LLMError('down')

    monkeypatch.setattr(module, 'ask', down)
    stories, problems = merge_stories([item(1, 'AI', 0), item(2, 'AI', 0), item(3, 'AI', 1)])
    assert sorted(len(s.articles) for s in stories) == [1, 1, 1]
    assert problems == ['Stories were not merged (down); a story may appear twice.']


def test_a_single_article_needs_no_merger_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, 'ask', lambda *a, **k: pytest.fail('no call for one article'))
    stories, _ = merge_stories([item(1, 'AI', 0)])
    assert len(stories) == 1


def test_an_empty_summary_does_not_break_the_merger(monkeypatch: pytest.MonkeyPatch) -> None:
    blank = item(1, 'Other', 0)
    items = [Summarised(blank.article, '', 'Other', '', True, 'no summary from the model', 0), item(2, 'AI', 0)]
    monkeypatch.setattr(module, 'ask', lambda *a, **k: MergeGroups(groups=[]))
    stories, _problems = merge_stories(items)
    assert len(stories) == 2



def test_a_story_takes_a_real_summary_when_its_lead_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    long_but_empty = item(1, 'War', 0, text_words=80)
    empty = Summarised(long_but_empty.article, '', 'War', '', False, '', 0)
    monkeypatch.setattr(module, 'ask', lambda *a, **k: MergeGroups(groups=[[1, 2]]))
    stories, _problems = merge_stories([empty, item(2, 'War', 0)])
    assert stories[0].summary == 'Samenvatting 2'
