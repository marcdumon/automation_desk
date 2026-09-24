"""Merge articles that report the same story, with one model call over all headlines.

Summarising batches do not mark same-story articles: a cheap model marked whole batches as one story there.
"""

from collections import Counter

import httpx
from pydantic import BaseModel, Field

from automation_desk.groups.news.store import ArticleRecord, StoryRecord
from automation_desk.groups.news.summarise import OTHER, Summarised
from automation_desk.llm import LLMError, ask


class MergeGroups(BaseModel):
    """Groups of numbers that report the same story."""

    groups: list[list[int]] = Field(description='Each group: the numbers of headlines about the same story (2 or more). '
                                                'Headlines about different stories are not listed.')


SYSTEM = """You get numbered news headlines from several sites, with the first line of their summary.
Group the numbers of headlines that report the SAME story (the same event, not merely the same topic).
Leave out headlines that have no partner."""


class _Groups:
    """Union-find over article links."""

    def __init__(self, links: list[str]) -> None:
        """Every link starts in a group of its own."""
        self.parent = {link: link for link in links}

    def find(self, link: str) -> str:
        """The representative of a link's group."""
        while self.parent[link] != link:
            self.parent[link] = self.parent[self.parent[link]]
            link = self.parent[link]
        return link

    def join(self, a: str, b: str) -> None:
        """Put two links in one group."""
        self.parent[self.find(a)] = self.find(b)


def _model_groups(items: list[Summarised], http: httpx.Client | None) -> tuple[list[list[int]], str]:
    """The model's groups (checked: known numbers, no article twice) and a problem line, if any."""
    listing = '\n'.join(f'{n}. [{i.article.source_name}] {i.article.title} | {(i.summary.splitlines() or [i.article.title])[0][:160]}'
                        for n, i in enumerate(items, 1))
    reply = None
    for attempt in range(2):
        try:
            reply = ask(SYSTEM, listing, MergeGroups, http=http, purpose='merge news stories')
            break
        except LLMError as error:
            if attempt == 1:
                return [], f'Stories were not merged ({error}); a story may appear twice.'
    used: set[int] = set()
    kept, dropped = [], 0
    for group in reply.groups if reply else []:
        numbers = list(dict.fromkeys(group))
        if any(not 1 <= n <= len(items) for n in numbers) or used & set(numbers):
            dropped += 1
            continue
        if len(numbers) >= 2:
            used |= set(numbers)
            kept.append(numbers)
    problem = (f'{dropped} same-story group from the model was dropped: it named articles that were not sent.'
               if dropped == 1 else f'{dropped} same-story groups from the model were dropped.' if dropped else '')
    return kept, problem


def merge_stories(items: list[Summarised], http: httpx.Client | None = None) -> tuple[list[StoryRecord], list[str]]:
    """Stories from summarised articles; the subject most of a story's articles got, the summary of its longest article."""
    groups = _Groups([i.article.link for i in items])
    problems = []
    if len(items) > 1:
        model_groups, problem = _model_groups(items, http)
        for numbers in model_groups:
            for n in numbers[1:]:
                groups.join(items[numbers[0] - 1].article.link, items[n - 1].article.link)
        if problem:
            problems.append(problem)
    members: dict[str, list[Summarised]] = {}
    for i in items:
        members.setdefault(groups.find(i.article.link), []).append(i)
    stories = []
    for group in members.values():
        subjects = Counter(i.subject for i in group if i.subject != OTHER)
        lead = max(group, key=lambda i: (not i.from_teaser, len(i.article.text)))
        stories.append(StoryRecord(
            subject=subjects.most_common(1)[0][0] if subjects else OTHER, title=lead.article.title,
            summary=lead.summary or next((i.summary for i in group if i.summary), ''),
            articles=[ArticleRecord(i.article.link, i.article.source_id, i.article.title, i.article.published, i.article.teaser,
                                    i.from_teaser, i.reason) for i in group]))
    return stories, problems
