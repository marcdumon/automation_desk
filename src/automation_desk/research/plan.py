"""The search plan: what to search for, per selected country, in that country's languages."""

import httpx
from pydantic import BaseModel

from automation_desk.llm import ask

LANGUAGES = {'BE': 'Dutch and French', 'NL': 'Dutch', 'DE': 'German', 'FR': 'French', 'LU': 'French and German',
             'AT': 'German', 'IT': 'Italian', 'ES': 'Spanish', 'UK': 'English', 'PL': 'Polish', 'US': 'English'}
# CLAUDE> plans filled the limit (16 to 24 searches); each costs a search fee plus its results' tokens
SYSTEM = '''Write web searches that find shop pages ({kind}s for sale, with price) and manufacturer pages for the need.
Per country use its languages and the words shops there use; add brand or model searches when the answers point to them,
and a few general searches (country "") for reviews and comparisons in English. For a service, search companies near the
municipality given. Most useful searches first. Write the fewest searches that cover the need: about 3 per country and
2 general ones, more only when the answers name several brands or kinds of {kind}; never more than {limit}.'''


class Query(BaseModel):
    """One search."""

    country: str
    text: str


class Plan(BaseModel):
    """The searches, most useful first."""

    queries: list[Query]


def make(request: str, kind: str, answers: dict, budget: float | None, countries: list[str], municipality: str,
         max_searches: int, http: httpx.Client | None = None, requirements: list[dict] | None = None) -> list[dict]:
    """The searches to run, only for the selected countries (and general ones), each once, at most `max_searches`."""
    langs = '\n'.join(f'- {c}: {LANGUAGES.get(c, "the local language")}' for c in countries)
    told = '\n'.join(f'- {k}: {v}' for k, v in answers.items())
    needs = '\n'.join(f'- {r["text"]} ({r["weight"]})' for r in requirements or [])
    where = f'\nMunicipality: {municipality}' if kind == 'service' and municipality else ''
    budget_line = f'Budget: € {budget:.0f}' if budget else 'Budget: none given'
    found = ask(SYSTEM.format(kind=kind, limit=max_searches),
                f'Need: {request}\n{budget_line}{where}\nAnswers:\n{told}\nRequirements:\n{needs}\nCountries:\n{langs}', Plan,
                http=http,
                purpose='research: search plan')
    queries, seen = [], set()
    for q in found.queries:
        text = ' '.join(q.text.split())
        if q.country in (*countries, '') and text and text.casefold() not in seen:
            seen.add(text.casefold())
            queries.append({'country': q.country, 'text': text})
    return queries[:max_searches]
