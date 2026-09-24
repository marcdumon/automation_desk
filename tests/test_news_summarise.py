"""Sorting headlines into subjects: checked answers, suggestions, blocked topics, the cost cap and failures.

The model writes no text: what shows under a title is the site's own teaser or page text.
"""

import json
from datetime import UTC, datetime

import pytest

from automation_desk.groups.news import summarise as module
from automation_desk.groups.news.collect import Article
from automation_desk.groups.news.summarise import ArticleSubject, BatchSubjects, summarise
from automation_desk.llm import LLMError


def said() -> str:
    """The model's instructions as one line: where they wrap does not matter."""
    return ' '.join(module.SYSTEM.split())


def article(n: int, text: str = 'Tekst van het artikel.') -> Article:
    """A collected article number n, with the text the site gives about it."""
    return Article(f'https://k.be/{n}', 1, 'Krant', f'Titel {n}', datetime(2026, 9, 24, tzinfo=UTC), f'Teaser {n}', text)


def answer(*rows: tuple[int, str]) -> BatchSubjects:
    """A model answer: (number, subject)."""
    return BatchSubjects(articles=[ArticleSubject(number=n, subject=sub) for n, sub in rows])


def test_answers_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [answer((1, 'Politics BE'), (2, 'Politics BE'), (3, 'suggest: Housing'), (99, 'AI'), (4, 'Sport')), answer()]
    monkeypatch.setattr(module, 'ask', lambda *a, **k: replies.pop(0))
    items, problems = summarise([article(1), article(2), article(3), article(4), article(5)], ['Politics BE', 'AI'], 1.0)
    by_link = {i.article.link: i for i in items}
    assert (by_link['https://k.be/3'].subject, by_link['https://k.be/3'].suggestion) == ('Other', 'Housing')
    assert (by_link['https://k.be/4'].subject, by_link['https://k.be/4'].suggestion) == ('Other', 'Sport'), (
        'a subject not in the list becomes a suggestion')
    assert by_link['https://k.be/5'].subject == 'Other', 'an article the model skipped, also when asked again, goes under Other'
    assert len(items) == 5 and problems == []


def test_the_text_shown_is_the_sites_own_never_the_models(monkeypatch: pytest.MonkeyPatch) -> None:
    same_as_title = Article('https://k.be/9', 1, 'Krant', "Syria's unfinished reckoning", None, '', "Syria's unfinished reckoning")
    monkeypatch.setattr(module, 'ask', lambda *a, **k: answer((1, 'AI'), (2, 'AI')))
    items, _problems = summarise([article(1, 'Wat de site zelf schrijft.'), same_as_title], ['AI'], 1.0)
    assert [i.summary for i in items] == ['Wat de site zelf schrijft.', ''], 'text that only repeats the title shows nothing'


def test_the_model_gets_headlines_and_the_first_words_only(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []
    monkeypatch.setattr(module, 'ask', lambda system, user, *a, **k: prompts.append(user) or answer((1, 'AI')))
    summarise([article(1, ' '.join(f'woord{n}' for n in range(200)))], ['AI'], 1.0)
    assert 'Titel 1' in prompts[0] and 'woord39' in prompts[0] and 'woord40' not in prompts[0]


def test_empty_answer_and_failed_batch_go_under_other(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fail(*a: object, **k: object) -> BatchSubjects:
        """The first batch answers nothing; the model then errors."""
        calls.append(1)
        if len(calls) == 1:
            return BatchSubjects(articles=[])
        raise LLMError('down')

    monkeypatch.setattr(module, 'ask', fail)
    monkeypatch.setattr(module, 'batch_size', lambda: 2)
    items, problems = summarise([article(1), article(2), article(3)], [], 1.0)
    assert [i.subject for i in items] == ['Other'] * 3 and [i.summary for i in items] == ['Tekst van het artikel.'] * 3
    assert problems == ['1 batch(es) could not be sorted into subjects (down); their articles are under Other.']
    assert len(calls) == 4, 'the empty answer is asked again once; the failed batch is retried once'


def test_past_the_cost_cap_headlines_are_held_back_unsorted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not dumped under Other: the digest holds them back, and the user can raise the cap to sort them."""
    monkeypatch.setattr(module, 'ask', lambda *a, **k: answer((1, 'AI')))
    monkeypatch.setattr(module, 'batch_size', lambda: 1)
    monkeypatch.setattr(module, 'estimate', lambda batch: 0.2)
    items, problems = summarise([article(1), article(2), article(3)], ['AI'], 0.3)
    assert [(i.subject, i.sorted) for i in items] == [('AI', True), ('Other', False), ('Other', False)]
    assert problems == [], 'the page offers raising the cap; no problem line'


def test_an_article_whose_page_could_not_be_read_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    unreadable = Article('https://k.be/7', 1, 'Krant', 'Titel 7', None, '', '', 'site blocks programs')
    monkeypatch.setattr(module, 'ask', lambda *a, **k: answer((1, 'AI')))
    items, _problems = summarise([unreadable], ['AI'], 1.0)
    assert (items[0].summary, items[0].from_teaser, items[0].reason) == ('', True, 'site blocks programs')


def test_skipped_articles_are_asked_again_once(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []

    def reply(system: str, user: str, *a: object, **k: object) -> BatchSubjects:
        """The model skips headline 2 of 3, then answers it when asked for it alone."""
        prompts.append(user)
        return answer((1, 'AI'), (3, 'AI')) if len(prompts) == 1 else answer((1, 'Politics'))

    monkeypatch.setattr(module, 'ask', reply)
    items, problems = summarise([article(1), article(2), article(3)], ['AI', 'Politics'], 1.0)
    assert [i.subject for i in items] == ['AI', 'Politics', 'AI']
    assert 'Answer all 3 headlines' in prompts[0] and 'Titel 2' in prompts[1] and 'Titel 1' not in prompts[1]
    assert problems == []


def test_blocked_topics_are_offered_as_subjects_and_matched_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []

    def reply(system: str, user: str, *a: object, **k: object) -> BatchSubjects:
        """Sport as a blocked topic, as a suggestion of it, and a subject in another case."""
        prompts.append(user)
        return answer((1, 'Sports'), (2, 'suggest: sports'), (3, 'ai'))

    monkeypatch.setattr(module, 'ask', reply)
    items, _problems = summarise([article(1), article(2), article(3)], ['AI'], 1.0, blocked=['Sports'])
    assert [(i.subject, i.suggestion) for i in items] == [('Sports', ''), ('Sports', ''), ('AI', '')]
    assert '- Sports' in prompts[0]


def test_each_batch_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, 'ask', lambda *a, **k: answer((1, 'AI'), (2, 'AI')))
    monkeypatch.setattr(module, 'batch_size', lambda: 2)
    reports = []
    summarise([article(n) for n in range(1, 6)], ['AI'], 1.0, report=lambda n, total: reports.append((n, total)))
    assert reports == [(1, 3), (2, 3), (3, 3)]


def test_the_instructions() -> None:
    """Subjects only: English names, the closest subject before a suggestion, Other only for non-articles, no merging."""
    assert 'suggest: <name in English>' in said() and 'Subject names are always English' in said()
    assert 'closest subject' in said() and 'stock index' in said() and 'Other only' in said()
    assert 'same story' not in said() and 'summar' not in said().casefold(), 'the model writes no text and merges nothing here'
    assert 'same_story' not in json.dumps(BatchSubjects.model_json_schema())
