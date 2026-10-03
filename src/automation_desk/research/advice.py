"""Follow-up questions during a research, and the advice. Code chooses the recommended product from the scores; the model
only writes why, its risks and a summary, by the product's name. Code removes amounts the table does not have, links,
and any product number."""

import re

import httpx
from pydantic import BaseModel

from automation_desk.llm import ask
from automation_desk.research.money import amount
from automation_desk.research.questions import Question

MAX_FOLLOWUPS = 2
FOLLOW = '''You see the products found for a buyer, numbered, with their specifications. If an important difference
between them depends on something the buyer has not told yet, ask about it (at most 2 short questions with answer
choices). Ask nothing when the answers already decide it. English.'''
ADVISE = '''A {kind} was chosen for a buyer because it meets their requirements best inside the budget. Write, about that
{kind} only and by its name: "reasons", why it fits the buyer's need, in 2 to 4 sentences, from its checks, pros and
cons; "risks", points to check before buying or installing; "summary", one sentence. Do not compare it with other
{kind}s, do not number anything, do not name shops or websites, and use only the price given. English.'''
NO_BEST = 'No {kind} found fits all your must requirements inside your budget. The table shows why each one is out.'
NOTHING = 'No {kind} could be compared: no shop page with a {kind} was read. Start a new research to search again.'
VERDICTS = {'yes': 'meets', 'partly': 'partly meets', 'no': 'does not meet', 'unknown': 'not shown on the page'}


class Followups(BaseModel):
    """Questions to ask before advising."""

    questions: list[Question]


class Advice(BaseModel):
    """Why the chosen product fits, its risks and a summary."""

    reasons: str
    risks: list[str]
    summary: str


def _listing(comparison: dict) -> str:
    """The products as the model sees them: number, name, specs, cheapest total, inside or over the budget."""
    lines = []
    for part, label in (('products', 'inside budget'), ('over_budget', 'over budget'), ('no_price', 'no price found')):
        for p in comparison[part]:
            specs = '; '.join(f'{k}: {v}' for k, v in p.get('specs', {}).items())
            price = f'€ {p["best_total"]:.2f}' if p.get('best_total') is not None else 'no price'
            lines.append(f'{p["n"]}. {p["name"]} ({label}, {price}, {len(p["offers"])} shop(s)) {specs}')
    return '\n'.join(lines)


def _brief(request: str, answers: dict) -> str:
    """The need and the answers."""
    return f'Need: {request}\nAnswers:\n' + '\n'.join(f'- {k}: {v}' for k, v in answers.items())


def followups(request: str, answers: dict, comparison: dict, http: httpx.Client | None = None) -> list[Question]:
    """At most two questions whose answers change the advice, ids f1, f2."""
    asked = ask(FOLLOW, f'{_brief(request, answers)}\n\nProducts:\n{_listing(comparison)}', Followups, http=http,
                purpose='research: follow-up questions').questions[:MAX_FOLLOWUPS]
    return [q.model_copy(update={'id': f'f{n}'}) for n, q in enumerate(asked, 1)]


# CLAUDE> "Product 21", "option 3", "#4": the numbers the model saw in a list, which the page never shows
NUMBERED = re.compile(r'(?:\b(?:product|option|item|nr|number|nummer)\.?\s*#?|\bno\.\s*|#)\d+\b', re.IGNORECASE)
# CLAUDE> an amount next to a sign or a currency word, in euro, dollar or pound
CURRENCY = r'€|\$|£|eur\b|euros?\b|usd\b|dollars?\b|gbp\b|pounds?\b'
AMOUNT = re.compile(rf'(?:{CURRENCY})\s*(\d[\d.,]*)|(\d[\d.,]*)\s*(?:{CURRENCY})', re.IGNORECASE)


def _has_url_or_domain(text: str) -> bool:
    """Check if text contains a URL (http://, https://, www.) or a domain name (word.tld)."""
    if re.search(r'https?://', text) or 'www.' in text:
        return True
    # CLAUDE> Match domain patterns: alphanumeric with dots, TLD must be letters only.
    # Pattern: domain-part.domain-part.tld where tld is 2+ letters (safe for decimals like 0.75)
    return bool(re.search(r'\b[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}\b', text, re.IGNORECASE))


def strip_unknown_amounts(text: str, known: set[float]) -> str:
    """Remove each sentence that names an amount in euro, dollar or pound the table does not have."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept = [s for s in sentences if all((v := amount(a or b)) is not None and round(v, 2) in known
                                        for a, b in AMOUNT.findall(s))]
    return ' '.join(kept)


def strip_urls_and_domains(text: str) -> str:
    """Remove each sentence that contains a URL or domain name."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept = [s for s in sentences if not _has_url_or_domain(s)]
    return ' '.join(kept)


def clean_text(text: str, known: set[float]) -> str:
    """A text without unknown amounts, links or product numbers: each such sentence is left out."""
    sentences = re.split(r'(?<=[.!?])\s+', strip_urls_and_domains(strip_unknown_amounts(text, known)))
    return ' '.join(s for s in sentences if not NUMBERED.search(s))


def write(request: str, answers: dict, requirements: list[dict], ranking: list[dict], stopped_by: str,
          http: httpx.Client | None = None, kind: str = 'product', budget: float | None = None) -> dict:
    """The advice for the product the ranking recommends; without one, a plain sentence and no model call."""
    noun = 'service company' if kind == 'service' else 'product'
    best = next((e for e in ranking if e['tag'] == 'recommended'), None)
    if best is None:
        text = (NO_BEST if ranking else NOTHING).format(kind=noun)
        return {'best': None, 'summary': text, 'reasons': '', 'risks': [], 'stopped_by': stopped_by}
    checks = '\n'.join(f'- {r["text"]} ({r["weight"]}): {VERDICTS[best["checks"][r["id"]]]}' for r in requirements)
    # CLAUDE> "with delivery" only when a shop showed its delivery cost: research 1 claimed it without one
    if best['total'] is None:
        price = 'Price: not on the website'
    elif best.get('delivery_known'):
        price = f'Price with delivery: € {best["total"]:.2f}'
    else:
        price = f'Price: € {best["total"]:.2f} (delivery cost not known)'
    facts = (f'Chosen {noun}: {best["title"]}\n{price}\nChecks:\n{checks}\n'
             f'Pros: {"; ".join(best["pros"]) or "none listed"}\nCons: {"; ".join(best["cons"]) or "none listed"}')
    advice = ask(ADVISE.format(kind=noun), f'{_brief(request, answers)}\n\n{facts}', Advice, http=http,
                 purpose='research: advice')
    # CLAUDE> the amounts the text may name: the chosen price and the budget, never another product's price
    known = {round(v, 2) for v in (best['total'], budget) if v is not None}
    return {'best': best, 'reasons': clean_text(advice.reasons, known), 'summary': clean_text(advice.summary, known),
            'risks': [r for r in (clean_text(r, known) for r in advice.risks) if r], 'stopped_by': stopped_by}
