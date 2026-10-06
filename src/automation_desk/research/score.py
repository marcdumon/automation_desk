"""Score the products found against the weighted requirements.

The model only says, per product and requirement, whether the shop page's data meets it (yes, partly, no, unknown), and
names a few pros and cons. Code computes the scores, chooses the recommendation and writes why each other product is not
it, so no number or reason in the ranking is invented.
"""

import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from automation_desk.llm import ask
from automation_desk.research.advice import clean_text
from automation_desk.research.requirements import WEIGHTS, lines

VALUES = {'yes': 1.0, 'partly': 0.5, 'unknown': 0.25, 'no': 0.0}
BATCH = 10
MAX_POINTS = 3
GOOD_GAP = 15
SYSTEM = '''You check {kind}s against a buyer's requirements, using only the data given for each (name and specifications
from its shop page). For each {kind} and each requirement id answer "yes" (the data shows it meets it), "partly", "no"
(the data shows it does not) or "unknown" (the data does not say). Never guess: missing data is "unknown". With each
answer give a note of at most 10 words saying what the data shows, e.g. "Lifts up to 10 m; route length not stated"
(empty for "unknown"). Also give at most 3 short pros and 3 short cons for the buyer's need, from the data only, without
prices. English.'''


class Check(BaseModel):
    """One requirement checked."""

    requirement: str = Field(description='The id of the requirement only, like "r1".')
    verdict: Literal['yes', 'partly', 'no', 'unknown']
    note: str = Field('', description='At most 10 words: what the data shows for this requirement; empty when unknown.')


class Rated(BaseModel):
    """One product checked against every requirement."""

    n: int
    checks: list[Check]
    pros: list[str]
    cons: list[str]


class Ratings(BaseModel):
    """The products of one batch."""

    products: list[Rated]


# CLAUDE> the real model wrote "r1: Handles muddy water…" for "r1" (research 1): the id is read from the start
REQUIREMENT_ID = re.compile(r'^\W*(r\d+)\b', re.IGNORECASE)


def _requirement_id(written: str) -> str:
    """The requirement id the model meant, '' when none."""
    found = REQUIREMENT_ID.match(written.strip())
    return found.group(1).casefold() if found else ''


def candidates(comparison: dict, kind: str) -> list[dict]:
    """The products to score: those with a price inside the budget, the few over it, and for services the companies without
    a price (they seldom show one)."""
    parts = ('products', 'over_budget', *(('no_price',) if kind == 'service' else ()))
    return [p for part in parts for p in comparison.get(part, [])]


def _listing(rows: list[dict]) -> str:
    """The products of a batch as the model reads them: number, name, brand and model, specifications."""
    out = []
    for p in rows:
        specs = '; '.join(f'{k}: {v}' for k, v in p.get('specs', {}).items()) or 'no specifications on the page'
        out.append(f'{p["n"]}. {p.get("title", p["name"])} | {specs}')
    return '\n'.join(out)


def rate(request: str, kind: str, requirements: list[dict], rows: list[dict], http: httpx.Client | None = None,
         answers: dict | None = None) -> dict[int, dict]:
    """Every product's verdict per requirement, pros and cons; a product or a check the model left out is 'unknown'. The
    buyer's answers (follow-up ones too) go along, so they count in the checks."""
    told = '\n'.join(f'- {k}: {v}' for k, v in (answers or {}).items())
    noun = 'service company' if kind == 'service' else 'product'
    ids = {r['id'] for r in requirements}
    rated: dict[int, dict] = {}
    # CLAUDE> about ten products per call: forty at once can pass the output limit and get cut off
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        answer = ask(SYSTEM.format(kind=noun), f'Need: {request}\nAnswers:\n{told}\n\nRequirements:\n{lines(requirements)}\n\n'
                     f'{noun.capitalize()}s:\n{_listing(batch)}', Ratings, http=http, purpose='research: score products')
        asked = {p['n'] for p in batch}
        for item in answer.products:
            if item.n in asked:
                found = [(key, c) for c in item.checks if (key := _requirement_id(c.requirement)) in ids]
                # CLAUDE> the note says why ("Lifts up to 10 m; route not stated"): "partly" alone means nothing to the user
                rated[item.n] = {'checks': {key: c.verdict for key, c in found},
                                 'notes': {key: clean_text(c.note.strip(), set()) for key, c in found},
                                 'pros': _said(item.pros), 'cons': _said(item.cons)}
    for p in rows:
        entry = rated.setdefault(p['n'], {'checks': {}, 'notes': {}, 'pros': [], 'cons': []})
        entry['checks'] = {r['id']: entry['checks'].get(r['id'], 'unknown') for r in requirements}
        entry['notes'] = {r['id']: entry.get('notes', {}).get(r['id'], '') for r in requirements}
    return rated


def _said(said: list[str]) -> list[str]:
    """Pros or cons without amounts, links or product numbers: the model saw no prices, so any amount is made up."""
    return [kept for s in said if (kept := clean_text(s.strip(), set()))][:MAX_POINTS]


def _points(requirements: list[dict], checks: dict[str, str]) -> dict[str, float]:
    """Per requirement the points earned: its weight (must 3, important 2, nice 1) times the answer (yes 1, partly 0.5,
    unknown 0.25, no 0)."""
    return {r['id']: round(WEIGHTS[r['weight']] * VALUES[checks[r['id']]], 2) for r in requirements}


def _score(requirements: list[dict], checks: dict[str, str]) -> int:
    """The points earned out of all points, as a percentage."""
    total = sum(WEIGHTS[r['weight']] for r in requirements)
    return round(100 * sum(_points(requirements, checks).values()) / total) if total else 0


def _euro(value: float) -> str:
    """An amount as the page shows it."""
    return f'€ {value:,.2f}'.replace(',', ' ')


def _labels(requirements: list[dict], keep: object) -> str:
    """The requirements that pass `keep`, by their number as the page shows it (R1, R2 …)."""
    return ', '.join(f'R{n}' for n, r in enumerate(requirements, 1) if keep(r))


def _why_not(entry: dict, best: dict | None, requirements: list[dict], budget: float | None) -> str:
    """Why a product is not the recommendation, from its checks and price only. Requirements go by their number: the full
    texts repeated in every row were long and told nothing the R columns do not show."""
    checks = entry['checks']
    musts = [r for r in requirements if r['weight'] == 'must']
    if entry['fails']:
        return f'Fails a must: {_labels(requirements, lambda r: r in musts and checks[r["id"]] == "no")}'
    if entry['over_budget'] and budget is not None and entry['total'] is not None:
        return f'{_euro(entry["total"] - budget)} over your budget'
    if entry['total'] is None and entry['kind'] == 'product':
        return 'No price found'
    if best is None:
        return ''
    reasons = []
    unconfirmed = {r['id'] for r in musts if checks[r['id']] == 'unknown'} if not best['unconfirmed'] else set()
    if unconfirmed:
        reasons.append(f'Not confirmed: {_labels(requirements, lambda r: r["id"] in unconfirmed)}')
    # CLAUDE> a requirement already named as not confirmed is not named again as weaker
    def weaker_on(r: dict) -> bool:
        """Answered worse than the recommendation, and not already named as not confirmed."""
        return r['id'] not in unconfirmed and VALUES[checks[r['id']]] < VALUES[best['checks'][r['id']]]

    if weaker := _labels(requirements, weaker_on):
        reasons.append(f'Weaker on: {weaker}')
    if entry['total'] is not None and best['total'] is not None and entry['total'] > best['total']:
        reasons.append(f'{_euro(entry["total"] - best["total"])} more')
    if reasons:
        return '. '.join(reasons)
    return 'Same score and price' if entry['score'] == best['score'] and entry['total'] == best['total'] else 'Scores lower'


def rank(requirements: list[dict], rows: list[dict], rated: dict[int, dict], comparison: dict, budget: float | None,
         kind: str) -> list[dict]:
    """The scored products, the recommendation first: products that fail no must, inside the budget, with every must
    confirmed, then the highest score, then the lowest price. Each says why it is not the recommendation."""
    over = {p['n'] for p in comparison.get('over_budget', [])}
    musts = [r for r in requirements if r['weight'] == 'must']
    entries = []
    for p in rows:
        checks = rated[p['n']]['checks']
        offer = p['offers'][0] if p['offers'] else {}
        entries.append({
            'n': p['n'], 'name': p['name'], 'title': p.get('title', p['name']), 'brand': p.get('brand', ''),
            'model': p.get('model', ''),
            'url': offer.get('url', ''), 'shop': offer.get('shop', ''), 'total': p.get('best_total'),
            # CLAUDE> the first offer gives the shown price: only its page counts for seen or not, and for delivery
            'shops': len(p['offers']), 'price_seen': offer.get('price_seen', True), 'original': offer.get('original'),
            'delivery_known': offer.get('delivery') is not None,
            'contact': offer.get('contact', ''), 'region': offer.get('region', ''), 'reviews': offer.get('reviews', ''),
            'score': _score(requirements, checks), 'checks': checks, 'points': _points(requirements, checks),
            'points_total': round(sum(_points(requirements, checks).values()), 2),
            'points_max': sum(WEIGHTS[r['weight']] for r in requirements), 'pros': rated[p['n']]['pros'],
            'cons': rated[p['n']]['cons'], 'notes': rated[p['n']].get('notes', {}),
            'fails': [r['text'] for r in musts if checks[r['id']] == 'no'],
            'unconfirmed': [r['text'] for r in musts if checks[r['id']] == 'unknown'], 'over_budget': p['n'] in over,
            'kind': kind, 'tag': '', 'why_not': ''})
    # CLAUDE> a product without a price cannot be recommended (a service company can: companies seldom show prices)
    entries.sort(key=lambda e: (bool(e['fails']), e['over_budget'], e['total'] is None and kind == 'product',
                                bool(e['unconfirmed']), -e['score'], e['total'] if e['total'] is not None else float('inf')))
    best = entries[0] if entries and not entries[0]['fails'] and not entries[0]['over_budget'] and \
        (entries[0]['total'] is not None or kind == 'service') else None
    if best:
        best['tag'] = 'recommended'
        # CLAUDE> "good": no must failed or unconfirmed, and a score close to the recommendation's
        cheaper = [e for e in entries if e is not best and not e['fails'] and not e['unconfirmed'] and not e['over_budget']
                   and e['score'] >= best['score'] - GOOD_GAP and e['total'] is not None and best['total'] is not None
                   and e['total'] < best['total']]
        if cheaper:
            min(cheaper, key=lambda e: e['total'])['tag'] = 'cheapest good choice'
    above = next((e for e in entries if e['over_budget'] and not e['fails']), None)
    if above:
        above['tag'] = 'best above your budget'
    for entry in entries:
        if entry is not best:
            entry['why_not'] = _why_not(entry, best, requirements, budget)
    return entries
