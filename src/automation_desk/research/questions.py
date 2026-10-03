"""The question form at the start of a research, and the price classes when the user has no budget."""

from typing import Literal

import httpx
from pydantic import BaseModel, Field

from automation_desk.llm import ask
from automation_desk.research.search import Hit

MAX_QUESTIONS = 8
SYSTEM = '''A user wants to buy a product or hire a service and describes the need. Decide whether it is a product or a
service. Write 4 to 8 short questions whose answers change which product or company is best (situation, sizes, use,
must-haves, installation, brand wishes); each with 2 to 5 short answer choices. Do not ask about budget or country. List
the facts that matter but are unknown from the description ("Water depth", "Distance to the drain"). Give the research a
short title of 2 to 5 words naming what is needed ("Lift pit sump pump"). English.'''
CLASSES = '''Give the usual price classes (2 to 4) for what the user needs, in euro, from the search results given and
general knowledge: a name, the low and high price, and in one sentence what the higher class gives more. English.'''


class Question(BaseModel):
    """One question with answer choices."""

    id: str
    text: str
    choices: list[str]


class Form(BaseModel):
    """The questions before the research."""

    kind: Literal['product', 'service']
    questions: list[Question]
    unknowns: list[str]
    title: str = Field('', description='A short title for the research, 2 to 5 words, what is needed: "Lift pit sump pump".')


class PriceClass(BaseModel):
    """A usual price range and what it gives."""

    name: str
    low: float
    high: float
    difference: str


class PriceClasses(BaseModel):
    """The price classes found."""

    classes: list[PriceClass]


def make(request: str, budget: float | None, countries: list[str], http: httpx.Client | None = None) -> Form:
    """The form: product or service, 4 to 8 questions with ids q1.. made by code, and the unknown facts."""
    form = ask(SYSTEM, f'Need: {request}', Form, http=http, purpose='research: questions')
    form.questions = [q.model_copy(update={'id': f'q{n}'}) for n, q in enumerate(form.questions[:MAX_QUESTIONS], 1)]
    form.title = ' '.join(form.title.split()).rstrip('.').strip()
    return form


def price_classes(request: str, answers: dict, hits: list[Hit], http: httpx.Client | None = None) -> list[PriceClass]:
    """Price classes from a few search results; impossible ranges dropped, cheapest first."""
    found = '\n'.join(f'- {h.title}: {h.snippet}' for h in hits[:20])
    told = '\n'.join(f'- {k}: {v}' for k, v in answers.items())
    classes = ask(CLASSES, f'Need: {request}\nAnswers:\n{told}\n\nSearch results:\n{found}', PriceClasses, http=http,
                  purpose='research: price classes').classes
    return sorted((c for c in classes if 0 <= c.low <= c.high), key=lambda c: c.low)
