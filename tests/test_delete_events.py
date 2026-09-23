"""Deleting events: filters in code, a fresh check before each delete."""

import pytest
from googleapiclient.errors import HttpError

from llm_automation.groups.base import UserError
from llm_automation.groups.calendar.tasks.delete_events import DeleteEvents, DeleteEventsArgs

from .conftest import FakeGoogle

EVENTS = [
    {'id': 'a', 'etag': '1', 'summary': 'KMSKA: Kosmorama', 'start': {'date': '2026-09-28'},
     'description': 'Tekst.\n\nSource: https://kmska.be/nl/kosmorama'},
    {'id': 'b', 'etag': '2', 'summary': 'Dentist', 'start': {'dateTime': '2026-09-29T09:00:00+02:00'}},
    {'id': 'c', 'etag': '3', 'summary': 'MoMA: Full Disclosure', 'start': {'date': '2026-09-27'}, 'recurringEventId': 'r',
     'description': 'Source: https://www.moma.org/calendar/exhibitions/5926'},
]


def calendar_api(events: list[dict], current: dict | None = None) -> FakeGoogle:
    """A fake Calendar API with one calendar 'Test' holding `events`; `current` overrides what events.get returns."""
    def get(calendarId: str, eventId: str) -> dict:
        """events.get, or a 404 for an event that is gone."""
        found = (current or {}).get(eventId) or next((e for e in events if e['id'] == eventId), None)
        if found is None:
            raise HttpError(type('R', (), {'status': 404, 'reason': 'Not Found'})(), b'')
        return found

    return FakeGoogle({
        'calendarList.list': lambda **_: {'items': [{'id': 'cal-t', 'summary': 'Test'}]},
        'events.list': lambda **_: {'items': events},
        'events.get': get,
        'events.delete': lambda **kwargs: '',
    })


def args(**kwargs: str) -> DeleteEventsArgs:
    """DeleteEventsArgs for calendar Test, overridable per test."""
    base = {'status': 'ok', 'message': '', 'calendar_name': 'test', 'date_range': '', 'title_contains': [], 'source_contains': ''}
    return DeleteEventsArgs(**{**base, **kwargs})


def test_filters_by_title_and_source_in_code(make_ctx) -> None:
    ctx = make_ctx(calendar_api(EVENTS), '')
    preview, _ = DeleteEvents().resolve(args(source_contains='kmska.be'), ctx)
    assert [r.cells['Title'] for r in preview.rows] == ['KMSKA: Kosmorama']
    preview, _ = DeleteEvents().resolve(args(title_contains=['moma']), ctx)
    assert [(r.cells['Title'], r.note) for r in preview.rows] == [('MoMA: Full Disclosure', 'repeating event: only this occurrence')]
    preview, _ = DeleteEvents().resolve(args(date_range='all events'), ctx)
    assert len(preview.rows) == 3 and all(r.selected for r in preview.rows)


def test_period_is_sent_as_bounds_in_the_user_timezone(make_ctx) -> None:
    fake = calendar_api(EVENTS)
    DeleteEvents().resolve(args(date_range='next week'), make_ctx(fake, 'delete next week'))
    listed = next(kw for name, kw in fake.calls if name == 'events.list')
    assert (listed['timeMin'], listed['timeMax']) == ('2026-09-28T00:00:00+02:00', '2026-10-05T00:00:00+02:00')
    assert listed['singleEvents'] is True


def test_nothing_matching_is_said_plainly(make_ctx) -> None:
    with pytest.raises(UserError, match="No events in 'Test' match"):
        DeleteEvents().resolve(args(title_contains=['opera']), make_ctx(calendar_api(EVENTS), ''))


def test_deletes_only_selected_and_unchanged_events(make_ctx) -> None:
    changed = {**EVENTS[1], 'etag': 'new'}
    fake = calendar_api(EVENTS, current={'b': changed})
    ctx = make_ctx(fake, '')
    _, payload = DeleteEvents().resolve(args(), ctx)
    results = DeleteEvents().execute(payload, {'a', 'b'}, ctx)
    assert [kw['eventId'] for name, kw in fake.calls if name == 'events.delete'] == ['a']
    assert results[1].startswith('Skipped "Dentist"') and 'changed since the preview' in results[1]
