"""The Calendar group's web import: code-side filtering, dedupe and event bodies."""

from dataclasses import replace
from datetime import date, time

import pytest

from automation_desk.groups.base import UserError
from automation_desk.groups.calendar.organisers import Organiser
from automation_desk.groups.calendar.tasks import add_events_from_web as module
from automation_desk.groups.calendar.tasks.add_events_from_web import (
    AddEventsArgs,
    AddEventsFromWeb,
    event_body,
    page_tag,
    source_key,
)
from automation_desk.groups.calendar.web_page import WebEvent

from .conftest import FakeGoogle

URL = 'https://venue.be/agenda'
SENTENCE = f'add all events from {URL}. to calendar Exhibitions except the ones on fridays'


def event(title: str, start: date, **kwargs: object) -> WebEvent:
    """A WebEvent with empty optional fields unless given."""
    fields = {'start_time': None, 'end': None, 'end_time': None, 'location': '', 'description': '', 'url': URL, **kwargs}
    return WebEvent(title=title, start=start, **fields)


EVENTS = [
    event('Friday concert', date(2026, 9, 25), start_time=time(20, 30)),
    event('Earlier tonight', date(2026, 9, 22), start_time=time(18, 0)),
    event('Later tonight', date(2026, 9, 22), start_time=time(22, 0)),
    event('Saturday talk', date(2026, 9, 26), start_time=time(14, 0), end_time=time(15, 30)),
    event('Long expo', date(2026, 9, 25), end=date(2026, 12, 1)),
    event('Old news', date(2026, 9, 1)),
    event('Already imported', date(2026, 9, 30)),
    event('Filtered out', date(2026, 10, 1), matches_filter=False),
    event('Open ended', date(2026, 8, 1), end_text='Summer 2027'),
]


def named(title: str) -> WebEvent:
    """The event in EVENTS with this title."""
    return next(e for e in EVENTS if e.title == title)


def imported(event: WebEvent, key: str | None = None, event_id: str = 'ev-1') -> dict:
    """An earlier import of `event` as the Calendar API returns it."""
    body = event_body(event, 'Europe/Brussels', key or source_key(URL, event), page_tag(URL))
    return {**body, 'id': event_id}


def calendar_api(earlier: list[dict]) -> FakeGoogle:
    """A fake Calendar API holding two calendars and these earlier imports."""
    return FakeGoogle({
        'calendarList.list': lambda **_: {'items': [{'id': 'cal-x', 'summary': 'Exhibitions'}, {'id': 'cal-m', 'summary': 'Music'}]},
        'events.list': lambda **_: {'items': earlier},
        'events.insert': lambda **kwargs: {},
        'events.patch': lambda **kwargs: {},
    })


def args(**kwargs: object) -> AddEventsArgs:
    """AddEventsArgs for the Exhibitions example, overridable per test."""
    base = {'status': 'ok', 'message': '', 'calendar_name': 'exhibitions', 'exclude_weekdays': ['friday'], 'date_range': '',
            'text_filter': '', 'follow_pages': False}
    return AddEventsArgs(**{**base, **kwargs})


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace page reading with the fixed EVENTS list and record how it was called."""
    seen: dict = {}

    def fake_read(url: str, today: date, tz: object, text_filter: str, max_pages: int, http: object) -> tuple:
        """Stand-in for read_events."""
        seen.update(url=url, max_pages=max_pages)
        return EVENTS, ['1 page read'], '<html></html>'

    monkeypatch.setattr(module, 'read_events', fake_read)
    # CLAUDE> an organiser without name or address leaves titles and places as the page gave them
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None: Organiser('venue.be', '', ''))
    return seen


def test_preview_applies_code_side_exclusions(make_ctx, pages: dict) -> None:
    fake = calendar_api([imported(named('Already imported'))])
    preview, payload = AddEventsFromWeb().resolve(args(), make_ctx(fake, SENTENCE))

    assert pages == {'url': URL, 'max_pages': 1}, 'the trailing period is not part of the URL'
    rows = {r.cells['Title']: r for r in preview.rows}
    assert rows['Friday concert'].note == 'excluded: friday' and not rows['Friday concert'].selected
    assert rows['Friday concert'].selectable, 'excluded rows can still be ticked by hand'
    assert rows['Long expo'].selected, 'multi-day events are not dropped by a weekday rule'
    assert rows['Saturday talk'].selected
    assert rows['Old news'].note == 'in the past'
    assert rows['Earlier tonight'].note == 'in the past', 'ended at 20:00, the clock says 21:00'
    assert rows['Later tonight'].selected
    assert rows['Filtered out'].note == 'does not match your filter'
    assert rows['Open ended'].note == 'no end date on the page (ends: Summer 2027)' and rows['Open ended'].selectable
    assert not rows['Already imported'].selectable and rows['Already imported'].note == 'already in the calendar, unchanged'
    assert payload['calendar_id'] == 'cal-x'


def test_execute_inserts_selected_and_skips_duplicates(make_ctx, pages: dict) -> None:
    fake = calendar_api([])
    ctx = make_ctx(fake, SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(), ctx)
    selected = {r.id for r in preview.rows if r.selected}
    results = AddEventsFromWeb().execute(payload, selected, ctx)
    inserted = [kw['body']['summary'] for name, kw in fake.calls if name == 'events.insert']
    assert inserted == ['Later tonight', 'Long expo', 'Saturday talk', 'Already imported']
    assert all(r.startswith('Added') for r in results)


def test_period_and_missing_url(make_ctx, pages: dict) -> None:
    preview, _ = AddEventsFromWeb().resolve(args(exclude_weekdays=[], date_range='this week'), make_ctx(calendar_api([]), SENTENCE))
    rows = {r.cells['Title']: r.note for r in preview.rows}
    assert rows['Filtered out'] != '' and rows['Friday concert'] == '' and rows['Already imported'] == 'outside the period'
    assert rows['Later tonight'] == ''
    with pytest.raises(UserError, match='address'):
        AddEventsFromWeb().resolve(args(), make_ctx(calendar_api([]), 'add the events to Exhibitions'))


def test_source_key_prefers_the_event_link_over_the_title() -> None:
    linked = event('Jazz op zolder', date(2026, 9, 25), url='https://venue.be/agenda/jazz')
    reworded = event('Jazz op zolder!', date(2026, 9, 25), url='https://venue.be/agenda/jazz')
    assert source_key(URL, linked) == source_key(URL, reworded)
    assert source_key(URL, event('A', date(2026, 9, 25))) != source_key(URL, event('B', date(2026, 9, 25)))


def test_event_bodies() -> None:
    timed = event_body(named('Friday concert'), 'Europe/Brussels', 'k', 't')
    assert timed['start'] == {'dateTime': '2026-09-25T20:30:00', 'timeZone': 'Europe/Brussels'}
    assert timed['end'] == {'dateTime': '2026-09-25T22:30:00', 'timeZone': 'Europe/Brussels'}
    assert timed['extendedProperties'] == {'private': {'automation_desk_page': 't', 'automation_desk_key': 'k'}}
    expo = event_body(named('Long expo'), 'Europe/Brussels', 'k', 't')
    assert (expo['start'], expo['end']) == ({'date': '2026-09-25'}, {'date': '2026-12-02'})
    assert event_body(named('Saturday talk'), 'Europe/Brussels', 'k', 't')['end']['dateTime'] == '2026-09-26T15:30:00'


def test_earlier_import_with_wrong_link_and_no_info_is_updated_not_duplicated(make_ctx, pages: dict) -> None:
    right = named('Saturday talk')
    wrong = replace(right, url='https://venue.be/agenda/other', description='', location='')
    fake = calendar_api([imported(wrong, event_id='ev-old')])
    ctx = make_ctx(fake, SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(), ctx)
    row = next(r for r in preview.rows if r.cells['Title'] == 'Saturday talk')
    assert row.selected and row.note == 'in the calendar; will be updated: info and source'
    AddEventsFromWeb().execute(payload, {row.id}, ctx)
    assert [(name, kw.get('eventId')) for name, kw in fake.calls if name in ('events.insert', 'events.patch')] == [
        ('events.patch', 'ev-old')]


def test_organiser_titles_places_and_adjusting_it(make_ctx, pages: dict, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from automation_desk.groups.calendar import organisers
    monkeypatch.setattr(organisers, 'STORE', tmp_path / 'organisers.json')
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None: Organiser('venue.be', 'Venue', ''))
    ctx = make_ctx(calendar_api([]), SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    assert {r.cells['Title'] for r in preview.rows} >= {'Venue: Saturday talk', 'Venue: Long expo'}
    assert [o.value for o in preview.options] == ['Venue', '']

    preview, payload = AddEventsFromWeb().adjust(payload, {'organiser': 'VNU', 'organiser_address': 'Kade 1, 2000 Antwerpen'}, ctx)
    row = next(r for r in preview.rows if r.cells['Title'] == 'VNU: Saturday talk')
    assert row.cells['Place'] == 'VNU, Kade 1, 2000 Antwerpen', 'a missing place becomes the organiser and its address'
    assert organisers.stored('venue.be') == Organiser('venue.be', 'VNU', 'Kade 1, 2000 Antwerpen'), 'remembered for the site'


def test_renaming_the_organiser_updates_instead_of_duplicating(
        make_ctx, pages: dict, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from automation_desk.groups.calendar import organisers
    monkeypatch.setattr(organisers, 'STORE', tmp_path / 'organisers.json')
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None: Organiser('venue.be', 'Venue', ''))
    earlier = imported(named('Long expo'), event_id='ev-7')
    fake = calendar_api([earlier])
    ctx = make_ctx(fake, SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    row = next(r for r in preview.rows if r.cells['Title'] == 'Venue: Long expo')
    assert row.note == 'in the calendar; will be updated: title, place' and payload['updates'][row.id] == 'ev-7'
