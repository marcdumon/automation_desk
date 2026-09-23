"""Summarising: checked answers, suggestions, the cost cap, and failures; merging is left to the story merger."""

import json
from datetime import UTC, datetime

import pytest

from automation_desk.groups.news import summarise as module
from automation_desk.groups.news.collect import Article
from automation_desk.groups.news.summarise import ArticleSummary, BatchSummary, summarise
from automation_desk.llm import LLMError


def article(n: int, text: str = 'Tekst van het artikel.') -> Article:
    """A collected article number n."""
    return Article(f'https://k.be/{n}', 1, 'Krant', f'Titel {n}', datetime(2026, 9, 24, tzinfo=UTC), f'Teaser {n}', text)


def answer(*rows: tuple[int, str, str]) -> BatchSummary:
    """A model answer: (number, summary, subject)."""
    return BatchSummary(articles=[ArticleSummary(number=n, summary=s, subject=sub) for n, s, sub in rows])


def test_answers_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [answer((1, 'Akkoord over de begroting.', 'Politics BE'), (2, 'Ook over de begroting.', 'Politics BE'),
                      (3, 'Nieuwe huurwet.', 'suggest: Housing'), (99, 'Niet gestuurd.', 'AI'),
                      (4, 'Over AI.', 'Sport')), answer()]
    monkeypatch.setattr(module, 'ask', lambda *a, **k: replies.pop(0))
    items, problems = summarise([article(1), article(2), article(3), article(4), article(5)], ['Politics BE', 'AI'], 1.0)
    by_link = {i.article.link: i for i in items}
    assert (by_link['https://k.be/3'].subject, by_link['https://k.be/3'].suggestion) == ('Other', 'Housing')
    assert (by_link['https://k.be/4'].subject, by_link['https://k.be/4'].suggestion) == ('Other', 'Sport'), (
        'a subject not in the list becomes a suggestion')
    assert (by_link['https://k.be/5'].summary, by_link['https://k.be/5'].from_teaser) == ('Teaser 5', True), (
        'an article the model skipped, also when asked again, keeps its teaser')
    assert len(items) == 5 and problems == []


def test_empty_answer_and_failed_batch_fall_back_to_teasers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fail(*a: object, **k: object) -> BatchSummary:
        """The first batch answers nothing; the model then errors."""
        calls.append(1)
        if len(calls) == 1:
            return BatchSummary(articles=[])
        raise LLMError('down')

    monkeypatch.setattr(module, 'ask', fail)
    monkeypatch.setattr(module, 'batch_size', lambda: 2)
    items, problems = summarise([article(1), article(2), article(3)], [], 1.0)
    assert all(i.from_teaser for i in items) and len(items) == 3
    assert problems == ['1 batch(es) could not be summarised (down); their articles use teasers.']
    assert len(calls) == 4, 'the empty answer is asked again once; the failed batch is retried once'


def test_cost_cap_switches_the_rest_to_teasers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, 'ask', lambda *a, **k: answer((1, 'Samenvatting.', 'AI')))
    monkeypatch.setattr(module, 'batch_size', lambda: 1)
    monkeypatch.setattr(module, 'estimate', lambda batch: 0.2)
    items, problems = summarise([article(1), article(2), article(3)], ['AI'], 0.3)
    assert [i.from_teaser for i in items] == [False, True, True]
    assert [i.reason for i in items][1:] == ['daily cost cap reached', 'daily cost cap reached']
    assert problems == ['Daily cost cap ($0.30) reached: 2 article(s) use their teaser.']


def test_articles_read_from_their_teaser_are_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    unreadable = Article('https://k.be/7', 1, 'Krant', 'Titel 7', None, 'Teaser 7', 'Teaser 7', 'page could not be read (404)')
    bare = Article('https://k.be/8', 1, 'Krant', '', None, '', '', 'page could not be read')
    replies = [answer((1, 'Over de teaser.', 'AI')), answer()]
    monkeypatch.setattr(module, 'ask', lambda *a, **k: replies.pop(0))
    items, _problems = summarise([unreadable, bare], ['AI'], 1.0)
    assert (items[0].from_teaser, items[0].reason) == (True, 'page could not be read (404)')
    assert items[1].summary == 'https://k.be/8', 'an item with no teaser and no title still shows something'


def test_the_batch_call_asks_for_no_same_story_numbers() -> None:
    """A cheap model marked whole batches as one story; merging is the story merger's job alone (and saves output tokens)."""
    assert 'same_story' not in json.dumps(BatchSummary.model_json_schema())
    assert 'same story' not in module.SYSTEM


def test_skipped_articles_are_asked_again_once(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []

    def reply(system: str, user: str, *a: object, **k: object) -> BatchSummary:
        """The model skips article 2 of 3, then answers it when asked for it alone."""
        prompts.append(user)
        return answer((1, 'Eerste.', 'AI'), (3, 'Derde.', 'AI')) if len(prompts) == 1 else answer((1, 'Tweede.', 'AI'))

    monkeypatch.setattr(module, 'ask', reply)
    items, problems = summarise([article(1), article(2), article(3)], ['AI'], 1.0)
    assert [(i.article.link, i.summary, i.from_teaser) for i in items] == [
        ('https://k.be/1', 'Eerste.', False), ('https://k.be/2', 'Tweede.', False), ('https://k.be/3', 'Derde.', False)]
    assert 'Answer all 3 articles' in prompts[0] and 'Titel 2' in prompts[1] and 'Titel 1' not in prompts[1]
    assert problems == []


def test_suggested_subjects_are_asked_in_english() -> None:
    """Summaries stay in the article's language; subject names are always English ('Health', never 'Santé')."""
    assert 'suggest: <name in English>' in module.SYSTEM
    assert 'in English' in ArticleSummary.model_fields['subject'].description


def test_blocked_topics_are_offered_as_subjects_and_matched_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []

    def reply(system: str, user: str, *a: object, **k: object) -> BatchSummary:
        """Sport as a blocked topic, as a suggestion of it, and a subject in another case."""
        prompts.append(user)
        return answer((1, 'Doelpunt.', 'Sports'), (2, 'Wielrennen.', 'suggest: sports'), (3, 'Over AI.', 'ai'))

    monkeypatch.setattr(module, 'ask', reply)
    items, _problems = summarise([article(1), article(2), article(3)], ['AI'], 1.0, blocked=['Sports'])
    assert [(i.subject, i.suggestion) for i in items] == [('Sports', ''), ('Sports', ''), ('AI', '')]
    assert '- Sports' in prompts[0]


def test_the_model_is_told_to_prefer_a_subject_or_a_suggestion_over_other() -> None:
    """The model filed 47 of 77 articles under Other, a stock index included, and suggested nothing."""
    assert 'closest subject' in module.SYSTEM
    assert 'stock index' in module.SYSTEM
    assert 'Other only' in module.SYSTEM
