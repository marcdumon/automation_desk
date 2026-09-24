"""Headlines sorted into subjects by the model, in batches. The model writes no text: what shows under a title is the
site's own teaser or the start of its page.

The model sees each headline with the first words of that text and answers only with a subject: one of the user's, a
blocked topic, a suggestion, or Other. Every answer is checked in code. The daily cost cap is kept by estimating a batch
before sending it.
"""

import contextlib
import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import httpx
from pydantic import BaseModel, Field

from automation_desk.config import config
from automation_desk.groups.news.collect import Article
from automation_desk.llm import LLMError, ask

OTHER = 'Other'
# CLAUDE> measured with gpt-6-luna on 232 headlines (24 Sep 2026): ~32 output tokens each
OUTPUT_TOKENS_PER_ARTICLE = 35
# CLAUDE> enough of the text to tell what a headline is about; the rest only costs input tokens
PROMPT_WORDS = 40


class ArticleSubject(BaseModel):
    """The model's answer for one headline."""

    number: int = Field(description='The number of the headline.')
    subject: str = Field(description="One subject from the list, exactly as written; or 'suggest: <new subject in English>' when "
                                     'none fits; or Other.')


class BatchSubjects(BaseModel):
    """The model's answer for a batch."""

    articles: list[ArticleSubject]


SYSTEM = """You sort news headlines for a personal daily digest. For every numbered headline (with the first words of its
text when there is some), pick the closest subject from the list, exactly as written, when the article is mainly about it
(e.g. a stock index, company results or interest rates are finance or economy); when no subject fits, answer 'suggest:
<name in English>' with a short, broad topic (e.g. Technology, War, Education); use Other only for items that are not
really an article, such as a daily cartoon or a column heading. Subject names are always English (e.g. Health, not Santé
or Gezondheid), whatever the headline's language. Answer for every number."""


@dataclass(frozen=True)
class Summarised:
    """An article with the text shown under its title and its subject, ready for merging.

    `summary` is the site's own text (empty when it only repeats the title); `from_teaser` and `reason` say when the page
    could not be read.
    """

    article: Article
    summary: str
    subject: str
    suggestion: str
    from_teaser: bool
    reason: str
    batch: int
    # CLAUDE> False: past the daily cap, not sent to the model; the digest holds it back until the user raises the cap
    sorted: bool = True


def batch_size() -> int:
    """Headlines per model call."""
    return config().news.batch_size


def estimate(batch: list[Article]) -> float:
    """Dollars a batch will likely cost, from its length and the model's prices (four characters per token)."""
    cfg = config()
    tokens_in = sum(len(' '.join(a.text.split()[:PROMPT_WORDS])) + len(a.title) for a in batch) / 4 + 400
    tokens_out = OUTPUT_TOKENS_PER_ARTICLE * len(batch)
    return (tokens_in * cfg.price_in_per_m + tokens_out * cfg.price_out_per_m) / 1_000_000


def _shown(article: Article) -> str:
    """The text shown under the title: the site's own, and nothing when it only repeats the title."""
    text = ' '.join(article.text.split())
    return '' if text == ' '.join(article.title.split()) else text


def _item(article: Article, batch: int, subject: str = OTHER, suggestion: str = '') -> Summarised:
    """An article with its subject."""
    return Summarised(article, _shown(article), subject, suggestion, bool(article.teaser_reason), article.teaser_reason, batch)


def _prompt(batch: list[Article], subjects: list[str]) -> str:
    """The numbered headlines with the first words of their text, and the subject list (subjects and blocked topics alike)."""
    listed = '\n'.join(f'- {s}' for s in subjects) or '- (none yet)'
    lines = []
    for n, a in enumerate(batch, 1):
        words = ' '.join(_shown(a).split()[:PROMPT_WORDS])
        lines.append(f'{n}. [{a.source_name}] {a.title}' + (f' | {words}' if words else ''))
    return f'Subjects:\n{listed}\n- {OTHER}\n\nAnswer all {len(batch)} headlines, numbered 1 to {len(batch)}.\n\n' + '\n'.join(lines)


def _answers(batch: list[Article], reply: BatchSubjects, subjects: list[str]) -> dict[str, tuple[str, str]]:
    """The model's subject per article link, checked: unknown numbers dropped, subjects matched in any case ('ai' is the
    user's 'AI'), a suggestion that is one of the subjects taken as that subject, anything else a suggestion."""
    known = {s.casefold(): s for s in subjects}
    answers = {}
    for answer in reply.articles:
        if not 1 <= answer.number <= len(batch):
            continue
        subject, suggestion = answer.subject.strip(), ''
        if subject.casefold().startswith('suggest:'):
            subject = subject.split(':', 1)[1].strip()
        if subject.casefold() in known:
            subject = known[subject.casefold()]
        elif subject.casefold() == OTHER.casefold():
            subject = OTHER
        else:
            subject, suggestion = OTHER, subject
        answers[batch[answer.number - 1].link] = (subject, suggestion)
    return answers


def summarise(articles: list[Article], subjects: list[str], budget_usd: float, http: httpx.Client | None = None,
              blocked: list[str] | None = None,
              report: Callable[[int, int], None] | None = None) -> tuple[list[Summarised], list[str]]:
    """Every article with its text and a subject, in batches; articles of failed batches go under Other, and those past the
    budget come back unsorted (`sorted` False) for the digest to hold back.

    Blocked topics are offered to the model as subjects like any other; the digest leaves their articles out.
    `report(batch, batches)` hears which batch starts, for the progress the page shows.
    """
    subjects = [*subjects, *(blocked or [])]
    items: list[Summarised] = []
    size, spent, failed, failure = batch_size(), 0.0, 0, ''
    batches = math.ceil(len(articles) / size)
    for index, start in enumerate(range(0, len(articles), size)):
        if report:
            report(index + 1, batches)
        batch = articles[start:start + size]
        cost = estimate(batch)
        if spent + cost > budget_usd:
            items += [replace(_item(a, index), sorted=False) for a in batch]
            continue
        spent += cost
        answers: dict[str, tuple[str, str]] = {}
        answered = False
        for attempt in range(2):
            try:
                answers = _answers(batch, ask(SYSTEM, _prompt(batch, subjects), BatchSubjects, http=http,
                                              purpose='sort news headlines into subjects'), subjects)
                answered = True
                break
            except LLMError as error:
                if attempt == 1:
                    failed, failure = failed + 1, str(error)
        # CLAUDE> the model sometimes skips headlines without saying so; ask once more for those alone
        missing = [a for a in batch if a.link not in answers]
        if answered and missing and spent + estimate(missing) <= budget_usd:
            spent += estimate(missing)
            with contextlib.suppress(LLMError):
                answers |= _answers(missing, ask(SYSTEM, _prompt(missing, subjects), BatchSubjects, http=http,
                                                 purpose='sort skipped news headlines'), subjects)
        items += [_item(a, index, *answers.get(a.link, (OTHER, ''))) for a in batch]
    problems = []
    if failed:
        problems.append(f'{failed} batch(es) could not be sorted into subjects ({failure}); their articles are under Other.')
    return items, problems
