"""Summaries by the model, in batches: 2-4 lines in the article's language and a subject.

Every answer is checked in code. The daily cost cap is kept by estimating a batch before sending it.
"""

from collections.abc import Callable
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from automation_desk.config import config
from automation_desk.groups.news.collect import Article
from automation_desk.llm import LLMError, ask

OTHER = 'Other'
NO_SUMMARY = 'no summary from the model'
OUTPUT_TOKENS_PER_ARTICLE = 120


class ArticleSummary(BaseModel):
    """The model's answer for one article."""

    number: int = Field(description='The number of the article.')
    summary: str = Field(description='2-4 lines with the facts of the article, in its language; empty when the text says '
                                     'nothing beyond the title.')
    subject: str = Field(description="One subject from the list, exactly as written; or 'suggest: <new subject in English>' when none "
                                     'fits; or Other.')


class BatchSummary(BaseModel):
    """The model's answer for a batch."""

    articles: list[ArticleSummary]


SYSTEM = """You summarise news articles for a personal daily digest. For every numbered article: write 2-4 lines with the facts it
gives, in the article's own language when that is Dutch, French or English (never translate those); for an article in
any other language, write it in English. Never describe the article itself (not 'the article discusses', not '<paper>
features an article titled') and never say that details are missing: when the text holds nothing beyond its title, give
an empty summary. Then pick the closest subject from the list, exactly as written, when the article is mainly about it
(e.g. a stock index, company results or interest rates are finance or economy); when no subject fits, answer 'suggest:
<name in English>' with a short, broad topic (e.g. Technology, War, Education); use Other only for items that are not
really an article, such as a daily cartoon or a column heading. Subject names are always English (e.g. Health, not Santé
or Gezondheid), whatever the article's language. Use only what the article says; do not repeat the title."""


@dataclass(frozen=True)
class Summarised:
    """An article with its summary and subject, ready for merging."""

    article: Article
    summary: str
    subject: str
    suggestion: str
    from_teaser: bool
    reason: str
    batch: int


def batch_size() -> int:
    """Articles per model call."""
    return config().news.batch_size


def estimate(batch: list[Article]) -> float:
    """Dollars a batch will likely cost, from its length and the model's prices (four characters per token)."""
    cfg = config()
    tokens_in = sum(len(a.text) + len(a.title) for a in batch) / 4 + 400
    tokens_out = OUTPUT_TOKENS_PER_ARTICLE * len(batch)
    return (tokens_in * cfg.price_in_per_m + tokens_out * cfg.price_out_per_m) / 1_000_000


def _teaser(article: Article, batch: int, reason: str) -> Summarised:
    """An article summarised by its teaser; no summary line when the teaser is missing or only repeats the title."""
    teaser = article.teaser.strip()
    summary = teaser if teaser and ' '.join(teaser.split()) != ' '.join(article.title.split()) else ''
    return Summarised(article, summary, OTHER, '', True, reason, batch)


def _prompt(batch: list[Article], subjects: list[str]) -> str:
    """The numbered articles and the subject list (the user's subjects and blocked topics alike)."""
    listed = '\n'.join(f'- {s}' for s in subjects) or '- (none yet)'
    body = '\n\n'.join(f'Article {n}\nTitle: {a.title}\nSite: {a.source_name}\n{a.text}' for n, a in enumerate(batch, 1))
    return f'Subjects:\n{listed}\n- {OTHER}\n\nAnswer all {len(batch)} articles, numbered 1 to {len(batch)}.\n\n{body}'


def _checked(batch: list[Article], reply: BatchSummary, subjects: list[str], index: int) -> list[Summarised]:
    """The model's answers kept only where they fit the batch; missing articles get their teaser.

    Subjects match in any case ('ai' is the user's 'AI'); a suggestion that is one of the subjects is that subject.
    """
    answers = {a.number: a for a in reply.articles if 1 <= a.number <= len(batch)}
    known = {s.casefold(): s for s in subjects}
    items = []
    for n, article in enumerate(batch, 1):
        answer = answers.get(n)
        if answer is None:
            items.append(_teaser(article, index, NO_SUMMARY))
            continue
        # CLAUDE> an empty summary is an answer: the text held nothing beyond its title, so the digest shows the title alone
        subject, suggestion = answer.subject.strip(), ''
        if subject.casefold().startswith('suggest:'):
            subject = subject.split(':', 1)[1].strip()
        if subject.casefold() in known:
            subject = known[subject.casefold()]
        elif subject.casefold() == OTHER.casefold():
            subject = OTHER
        else:
            subject, suggestion = OTHER, subject
        items.append(Summarised(article, answer.summary.strip(), subject, suggestion, bool(article.teaser_reason),
                                article.teaser_reason, index))
    return items


def summarise(articles: list[Article], subjects: list[str], budget_usd: float, http: httpx.Client | None = None,
              blocked: list[str] | None = None,
              report: Callable[[int, int], None] | None = None) -> tuple[list[Summarised], list[str]]:
    """Summaries of all articles, in batches; articles past the budget, or of failed batches, keep their teaser.

    Blocked topics are offered to the model as subjects like any other; the digest leaves their articles out.
    `report(batch, batches)` hears which batch starts, for the progress the page shows.
    """
    subjects = [*subjects, *(blocked or [])]
    items: list[Summarised] = []
    size, spent, capped, failed, failure = batch_size(), 0.0, 0, 0, ''
    batches = -(-len(articles) // size)
    for index, start in enumerate(range(0, len(articles), size)):
        if report:
            report(index + 1, batches)
        batch = articles[start:start + size]
        cost = estimate(batch)
        if spent + cost > budget_usd:
            items += [_teaser(a, index, 'daily cost cap reached') for a in batch]
            capped += len(batch)
            continue
        spent += cost
        done: list[Summarised] = []
        for attempt in range(2):
            try:
                reply = ask(SYSTEM, _prompt(batch, subjects), BatchSummary, http=http, purpose='summarise news articles')
                done = _checked(batch, reply, subjects, index)
                break
            except LLMError as error:
                if attempt == 1:
                    failed, failure = failed + 1, str(error)
                    items += [_teaser(a, index, 'the model could not summarise it') for a in batch]
        if done:
            # CLAUDE> the model sometimes skips articles without saying so (4 of 20 answered once); ask once for the rest alone
            missing = [i.article for i in done if i.reason == NO_SUMMARY]
            if missing and spent + estimate(missing) <= budget_usd:
                spent += estimate(missing)
                try:
                    again = _checked(missing, ask(SYSTEM, _prompt(missing, subjects), BatchSummary, http=http,
                                                  purpose='summarise skipped news articles'), subjects, index)
                    answered = {i.article.link: i for i in again if i.reason != NO_SUMMARY}
                    done = [answered.get(i.article.link, i) for i in done]
                except LLMError:
                    pass
            items += done
    problems = []
    if capped:
        problems.append(f'Daily cost cap (${budget_usd:.2f}) reached: {capped} article(s) use their teaser.')
    if failed:
        problems.append(f'{failed} batch(es) could not be summarised ({failure}); their articles use teasers.')
    return items, problems
