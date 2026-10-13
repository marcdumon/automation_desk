"""Whether an event of an agenda is already in the calendar in another form: typed in by the user, imported from another
source, or imported before with a title worded a little differently. Code takes the clear cases: the same start and titles
alike (85 % or more, case and spaces aside). The model judges the rest against the other events of the same day, in one
question for the whole agenda; code checks its answer (the same day, each calendar event once)."""

from difflib import SequenceMatcher

import httpx
from pydantic import BaseModel, Field

from automation_desk.llm import ask

ALIKE = 0.85
SYSTEM = ('You compare the events of an agenda with the events already in a calendar on the same days. An agenda event is a '
          'calendar event when both are the same happening: the titles may be worded differently, shortened, misspelt, in '
          'another language, or carry the name of the organiser, and the times may differ a little or be missing. Two '
          'different happenings on the same day are not the same, also not at the same place. When in doubt, they are not '
          'the same.')


class SamePair(BaseModel):
    """An agenda event that is a calendar event."""

    new: int = Field(description='The number of the agenda event (A1 is 1).')
    existing: int = Field(description='The number of the calendar event it is (C1 is 1).')


class SameEvents(BaseModel):
    """The agenda events already in the calendar."""

    pairs: list[SamePair] = Field(description='Only the agenda events that are a calendar event; [] when none is.')


def _norm(text: str) -> str:
    """Case- and space-insensitive form of a title."""
    return ' '.join(text.casefold().split())


def alike(a: str, b: str) -> bool:
    """Whether two titles are the same apart from small differences: other quote marks, a letter more or less."""
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= ALIKE


def start_of(event: dict) -> str:
    """The start of a Calendar API event or body: YYYY-MM-DD for a whole day, else YYYY-MM-DDTHH:MM."""
    start = event.get('start', {})
    return start.get('date') or start.get('dateTime', '')[:16]


def _line(name: str, start: str, title: str, place: str) -> str:
    """One event in the question to the model."""
    return f"{name} | {start[:10]} {start[11:16] or 'all day'} | {title} | {place}"


def find(new: dict[str, dict], calendar: list[dict], http: httpx.Client | None = None) -> dict[str, dict]:
    """The calendar event each new event already is, by key; a new event is {'titles': its titles, 'start': as start_of
    gives it, 'place'}. Raises LLMError when the model gives no answer."""
    found: dict[str, dict] = {}
    taken: set[str] = set()
    for key, event in new.items():
        same = next((c for c in calendar if c['id'] not in taken and start_of(c) == event['start']
                     and any(alike(title, c.get('summary', '')) for title in event['titles'])), None)
        if same:
            found[key], taken = same, taken | {same['id']}
    # the rest: only the events with other events on the same day go to the model
    open_events = [(key, event) for key, event in new.items() if key not in found
                   and any(start_of(c)[:10] == event['start'][:10] and c['id'] not in taken for c in calendar)]
    if not open_events:
        return found
    days = {event['start'][:10] for _, event in open_events}
    others = [c for c in calendar if c['id'] not in taken and start_of(c)[:10] in days]
    question = '\n'.join(['Agenda events:',
                          *(_line(f'A{n}', e['start'], e['titles'][-1], e['place']) for n, (_, e) in enumerate(open_events, 1)),
                          'Calendar events on these days:',
                          *(_line(f'C{n}', start_of(c), c.get('summary', ''), c.get('location', ''))
                            for n, c in enumerate(others, 1))])
    answer = ask(SYSTEM, question, SameEvents, http=http, purpose='find agenda events already in the calendar')
    for pair in answer.pairs:
        if not (1 <= pair.new <= len(open_events) and 1 <= pair.existing <= len(others)):
            continue
        key, event = open_events[pair.new - 1]
        same = others[pair.existing - 1]
        if key not in found and same['id'] not in taken and start_of(same)[:10] == event['start'][:10]:
            found[key], taken = same, taken | {same['id']}
    return found
