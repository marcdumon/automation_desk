"""The weighted requirements of a research: made by the model from the request and the answers, changed by the user before
the search, and used to score every product found."""

from typing import Literal

import httpx
from pydantic import BaseModel

from automation_desk.llm import ask

WEIGHTS = {'must': 3, 'important': 2, 'nice': 1}
MAX_MADE, MAX_KEPT = 8, 12
SYSTEM = '''From a buyer's need and answers, list the requirements a {kind} must meet to be the right choice: 4 to 8 short,
checkable requirements, each one thing that a shop page can confirm or deny ("Pumps dirty water with particles up to
20 mm", "Lifts water at least 3 m high", "Starts and stops by itself at a low water level"). Use the numbers the buyer
gave. Weight each one: "must" (the {kind} is useless without it), "important" or "nice". Do not list the budget or the
country: they are checked apart. English.'''


class Requirement(BaseModel):
    """One requirement and its weight."""

    text: str
    weight: Literal['must', 'important', 'nice']


class Requirements(BaseModel):
    """The requirements the model proposes."""

    requirements: list[Requirement]


def clean(given: list[dict]) -> list[dict]:
    """The requirements as kept: text on one line, a known weight, no empty or double ones, ids r1.. in order."""
    kept, seen = [], set()
    for item in given:
        # CLAUDE> no closing full stop: the reasons join requirements into sentences of their own
        text = ' '.join(str(item.get('text', '')).split()).rstrip('.').strip()
        weight = item.get('weight') if item.get('weight') in WEIGHTS else 'important'
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            kept.append({'text': text, 'weight': weight})
    return [{'id': f'r{n}', **item} for n, item in enumerate(kept[:MAX_KEPT], 1)]


def make(request: str, kind: str, answers: dict, http: httpx.Client | None = None) -> list[dict]:
    """The proposed requirements, at most MAX_MADE, musts first."""
    told = '\n'.join(f'- {k}: {v}' for k, v in answers.items())
    found = ask(SYSTEM.format(kind='service company' if kind == 'service' else 'product'),
                f'Need: {request}\nAnswers:\n{told}', Requirements, http=http, purpose='research: requirements')
    ordered = sorted((r.model_dump() for r in found.requirements), key=lambda r: -WEIGHTS[r['weight']])
    return clean(ordered)[:MAX_MADE]


def lines(requirements: list[dict]) -> str:
    """The requirements as the model reads them: one per line with id and weight."""
    return '\n'.join(f'- {r["id"]} ({r["weight"]}): {r["text"]}' for r in requirements)
