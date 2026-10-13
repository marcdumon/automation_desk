"""The Calendar group's web import: code-side filtering, dedupe and event bodies."""

from dataclasses import replace
from datetime import date, time

import pytest

from automation_desk.dates import DateExprError
from automation_desk.groups.base import UserError
from automation_desk.groups.calendar import same_event
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
from automation_desk.llm import LLMError

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
            'text_filter': '', 'follow_pages': False, 'pdf_mail_from': [], 'pdf_mail_subject': []}
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
    # an organiser without name or address leaves titles and places as the page gave them
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None, site=None: Organiser('venue.be', '', ''))
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
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None, site=None: Organiser('venue.be', 'Venue', ''))
    ctx = make_ctx(calendar_api([]), SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    assert {r.cells['Title'] for r in preview.rows} >= {'Venue: Saturday talk', 'Venue: Long expo'}
    assert [o.value for o in preview.options] == ['Venue', '', 'Times from the site', '2h']

    preview, payload = AddEventsFromWeb().adjust(payload, {'organiser': 'VNU', 'organiser_address': 'Kade 1, 2000 Antwerpen'}, ctx)
    row = next(r for r in preview.rows if r.cells['Title'] == 'VNU: Saturday talk')
    assert row.cells['Place'] == 'VNU, Kade 1, 2000 Antwerpen', 'a missing place becomes the organiser and its address'
    assert organisers.stored('venue.be') == Organiser('venue.be', 'VNU', 'Kade 1, 2000 Antwerpen'), 'remembered for the site'


def test_renaming_the_organiser_updates_instead_of_duplicating(
        make_ctx, pages: dict, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from automation_desk.groups.calendar import organisers
    monkeypatch.setattr(organisers, 'STORE', tmp_path / 'organisers.json')
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None, site=None: Organiser('venue.be', 'Venue', ''))
    earlier = imported(named('Long expo'), event_id='ev-7')
    fake = calendar_api([earlier])
    ctx = make_ctx(fake, SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    row = next(r for r in preview.rows if r.cells['Title'] == 'Venue: Long expo')
    assert row.note == 'in the calendar; will be updated: title, place' and payload['updates'][row.id] == 'ev-7'


def test_durations_for_all_and_for_each(make_ctx, pages: dict) -> None:
    ctx = make_ctx(calendar_api([]), SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    rows = {r.cells['Title']: r for r in preview.rows}
    assert (rows['Friday concert'].cells['Time'], rows['Friday concert'].cells['Duration']) == ('20:30-22:30', '2h (assumed)')
    assert rows['Friday concert'].inputs['Duration'] == '2h' and 'Duration' not in rows['Long expo'].inputs, 'no duration for all-day'
    assert rows['Saturday talk'].cells['Duration'] == '1h30', 'the end time on the page counts'

    key = rows['Saturday talk'].id
    preview, payload = AddEventsFromWeb().adjust(payload, {'default_duration': '3 uur', f'Duration:{key}': '45 min'}, ctx)
    rows = {r.cells['Title']: r for r in preview.rows}
    assert rows['Friday concert'].cells['Time'] == '20:30-23:30', 'the new default applies to all without an end time'
    assert (rows['Saturday talk'].cells['Time'], rows['Saturday talk'].cells['Duration']) == ('14:00-14:45', '45 min')
    assert payload['bodies'][key]['end']['dateTime'] == '2026-09-26T14:45:00'
    with pytest.raises(DateExprError):
        AddEventsFromWeb().adjust(payload, {'default_duration': 'ages'}, ctx)


def test_a_year_far_from_the_others_is_flagged(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    typo = [event('Lezing', date(2026, 10, 3), start_time=time(9, 30)), event('Stemming', date(2028, 10, 6), start_time=time(20)),
            event('Bespreking', date(2026, 10, 13), start_time=time(20))]
    monkeypatch.setattr(module, 'read_events', lambda *a, **k: (typo, [], ''))
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None, site=None: Organiser('x', '', ''))
    preview, _ = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), make_ctx(calendar_api([]), SENTENCE))
    row = next(r for r in preview.rows if r.cells['Title'] == 'Stemming')
    assert not row.selected and row.selectable and row.note.startswith('the page says 2028 while the other events are in 2026')


def test_every_field_can_be_edited(make_ctx, pages: dict) -> None:
    ctx = make_ctx(calendar_api([]), SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    concert = next(r for r in preview.rows if r.cells['Title'] == 'Friday concert')
    expo = next(r for r in preview.rows if r.cells['Title'] == 'Long expo')
    assert set(concert.inputs) == {'Date', 'Time', 'Duration', 'Title', 'Place', 'Info'}, 'the source is where it comes from: fixed'
    edits = {f'Date:{concert.id}': '3/10/2026', f'Time:{concert.id}': '19u30', f'Duration:{concert.id}': '1h30',
             f'Title:{concert.id}': 'Jazz!', f'Place:{concert.id}': 'Zaal Nova', f'Info:{concert.id}': 'Nieuw',
             f'Time:{expo.id}': '10:00', f'Date:{expo.id}': '1 okt 2026'}
    preview, payload = AddEventsFromWeb().adjust(payload, edits, ctx)
    body = payload['bodies'][concert.id]
    assert (body['summary'], body['location'], body['start']['dateTime'], body['end']['dateTime']) == (
        'Jazz!', 'Zaal Nova', '2026-10-03T19:30:00', '2026-10-03T21:00:00')
    assert body['description'].startswith('Nieuw')
    expo_body = payload['bodies'][expo.id]
    assert expo_body['start']['dateTime'] == '2026-10-01T10:00:00', 'a date and time turn an exhibition into a timed event'

    preview, payload = AddEventsFromWeb().adjust(payload, {f'Time:{expo.id}': 'all day'}, ctx)
    assert payload['bodies'][expo.id]['start'] == {'date': '2026-10-01'}
    with pytest.raises(UserError, match="Could not read the date 'soon' of 'Jazz!'"):
        AddEventsFromWeb().adjust(payload, {f'Date:{concert.id}': 'soon'}, ctx)


def test_hours_on_several_days_are_those_hours_each_day() -> None:
    """'Zaterdag 26/9 - Zondag 27/9, van 14u tot 22u' is 14:00-22:00 on both days, not one 32-hour block."""
    expo = event('Expo', date(2026, 9, 26), end=date(2026, 9, 27), start_time=time(14), end_time=time(22))
    body = module.event_body(expo, 'Europe/Brussels', 'k', 't')
    assert (body['start']['dateTime'], body['end']['dateTime']) == ('2026-09-26T14:00:00', '2026-09-26T22:00:00')
    assert body['recurrence'] == ['RRULE:FREQ=DAILY;UNTIL=20260927T235959Z']
    night = event('Party', date(2026, 9, 26), end=date(2026, 9, 27), start_time=time(22), end_time=time(2))
    body = module.event_body(night, 'Europe/Brussels', 'k', 't')
    assert body['end']['dateTime'] == '2026-09-27T02:00:00' and 'recurrence' not in body, 'past midnight: one event'


def test_all_events_can_be_all_day(make_ctx, pages: dict) -> None:
    """A preview choice: the site's times, or every event all day without a time."""
    ctx = make_ctx(calendar_api([]), SENTENCE)
    preview, payload = AddEventsFromWeb().resolve(args(exclude_weekdays=[]), ctx)
    times = next(o for o in preview.options if o.name == 'times')
    assert (times.value, times.choices) == ('Times from the site', ['Times from the site', 'All day'])
    preview, payload = AddEventsFromWeb().adjust(payload, {'times': 'All day'}, ctx)
    talk = next(r for r in preview.rows if r.cells['Title'].endswith('Saturday talk'))
    assert talk.cells['Time'] == 'all day'
    assert 'date' in payload['bodies'][talk.id]['start'] and 'dateTime' not in payload['bodies'][talk.id]['start']


MAIL_51, MAIL_52 = 'https://mail.google.com/mail/u/0/#all/1a0e47eed3cc7507', 'https://mail.google.com/mail/u/0/#all/1a1088aef27f0a3b'


def calendar_holding(events: list[dict]) -> FakeGoogle:
    """A fake Calendar API with these events in calendar Exhibitions; a search on a private property finds only the events
    that carry it."""
    def listed(**kwargs: object) -> dict:
        """The events, or the ones with the asked property."""
        name, _, value = str(kwargs.get('privateExtendedProperty', '')).partition('=')
        return {'items': [e for e in events if not name or e.get('extendedProperties', {}).get('private', {}).get(name) == value]}
    return FakeGoogle({'calendarList.list': lambda **_: {'items': [{'id': 'cal-x', 'summary': 'Exhibitions'}]},
                       'events.list': listed, 'events.insert': lambda **kwargs: {}, 'events.patch': lambda **kwargs: {}})


def by_hand(title: str, start: str, event_id: str = 'mine') -> dict:
    """An event the user typed in, without a tag; `start` YYYY-MM-DDTHH:MM, or a day for an all-day event."""
    if 'T' in start:
        return {'id': event_id, 'summary': title, 'start': {'dateTime': f'{start}:00+02:00'},
                'end': {'dateTime': f'{start[:11]}23:00:00+02:00'}}
    return {'id': event_id, 'summary': title, 'start': {'date': start}, 'end': {'date': start}}


def newer_mail(make_ctx, earlier: list[dict], events: list[WebEvent]) -> tuple:
    """The preview of the events of a newer mail, with these events already in the calendar."""
    ctx = make_ctx(calendar_holding(earlier), 'add all events from the pdf in the last mail from joost to calendar Exhibitions')
    payload = {'url': MAIL_52, 'calendar_id': 'cal-x', 'calendar': 'Exhibitions', 'page_tag': page_tag(MAIL_52),
               'events': events, 'notes': [], 'excluded': set(), 'period': None, 'organiser': Organiser('mail.google.com', '', '')}
    return AddEventsFromWeb().compose(payload, ctx)


def from_mail_51(talk: WebEvent, end: str | None = None) -> dict:
    """The import of an event from the older mail, as the Calendar API gives it back; `end` an end time set by hand."""
    body = {**event_body(talk, 'Europe/Brussels', source_key(MAIL_51, talk), page_tag(MAIL_51)), 'id': 'ev-51'}
    body['start'] = {'dateTime': f"{body['start']['dateTime']}+02:00", 'timeZone': 'Europe/Brussels'}
    if end:
        body['end'] = {'dateTime': f'{talk.start.isoformat()}T{end}:00+02:00', 'timeZone': 'Europe/Brussels'}
    return body


def test_the_same_events_in_a_newer_mail_are_already_in_the_calendar(make_ctx) -> None:
    """Newsletter 52 repeats the events of newsletter 51, imported before; the user then set their end times by hand."""
    talk = event('Lecture', date(2026, 10, 13), start_time=time(20, 0), url=MAIL_51)
    moved = event('Debate', date(2026, 10, 20), start_time=time(20, 0), url=MAIL_51)
    preview, _ = newer_mail(make_ctx, [from_mail_51(talk, end='23:45'), from_mail_51(moved, end='23:45')],
                            [replace(talk, url=MAIL_52), replace(moved, url=MAIL_52, start_time=time(19, 30))])
    rows = {r.cells['Title']: r for r in preview.rows}
    assert (rows['Lecture'].note, rows['Lecture'].selectable) == ('already in the calendar, unchanged', False), \
        'another mail with the same event is no change, and a guessed end is not one either'
    assert rows['Debate'].note == 'in the calendar; will be updated: dates', 'a start time the agenda moved is a change'


def test_an_update_keeps_an_end_time_set_by_hand_when_the_agenda_gives_none(make_ctx) -> None:
    talk = event('Lecture', date(2026, 10, 13), start_time=time(20, 0), url=MAIL_51)
    preview, payload = newer_mail(make_ctx, [from_mail_51(talk, end='23:45')],
                                  [replace(talk, url=MAIL_52, location='Other hall')])
    row = preview.rows[0]
    assert row.note == 'in the calendar; will be updated: place'
    assert payload['bodies'][row.id]['end']['dateTime'][:16] == '2026-10-13T23:45', 'the guess does not replace it'


def no_model(*args: object, **kwargs: object) -> None:
    """Code decides the clear cases: the model is never asked."""
    pytest.fail('the model was asked')


def test_an_event_typed_in_with_a_slightly_other_title_is_already_in_the_calendar(make_ctx, pages: dict,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(same_event, 'ask', no_model)
    preview, payload = AddEventsFromWeb().resolve(args(), make_ctx(calendar_holding([by_hand('saturday talk!', '2026-09-26T14:00')]),
                                                                    SENTENCE))
    row = next(r for r in preview.rows if r.cells['Title'] == 'Saturday talk')
    assert (row.note, row.selected, row.selectable) == ('already in the calendar as "saturday talk!" at 14:00', False, True), \
        'the same start and a title alike: unticked, and it can still be ticked'
    assert row.id not in payload['updates'], 'an event the user made is never changed'


def test_an_earlier_import_the_model_worded_a_little_differently_is_updated_not_added_again(
        make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(same_event, 'ask', no_model)
    talk = event('Athena-lezing 2026 "Verzet in volle overgave".', date(2026, 10, 3), start_time=time(9, 30), url=MAIL_51)
    preview, payload = newer_mail(make_ctx, [from_mail_51(talk)],
                                  [replace(talk, url=MAIL_52, title='Athena-lezing 2026 \u201cVerzet in volle overgave\u201d')])
    row = preview.rows[0]
    assert row.note == 'in the calendar; will be updated: title' and payload['updates'][row.id] == 'ev-51'


def test_the_model_judges_an_event_typed_in_with_other_words_on_the_same_day(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = []

    def answer(system: str, user: str, schema: type, **kwargs: object) -> same_event.SameEvents:
        """The model pairs the inauguration with the user's event, and gives one pair that does not exist."""
        asked.append(user)
        return same_event.SameEvents(pairs=[same_event.SamePair(new=1, existing=1), same_event.SamePair(new=2, existing=5)])

    monkeypatch.setattr(same_event, 'ask', answer)
    inauguration = event('De Geuzen: Inwijding van de nieuwe leden', date(2026, 11, 18), start_time=time(10, 0), url=MAIL_52)
    play = event('De Geuzen: De Geheime Kamer', date(2026, 11, 11), start_time=time(20, 0), url=MAIL_52)
    calendar = [by_hand('Inweiding Geuzen', '2026-11-18T09:30'), by_hand('Tandarts', '2026-11-12T10:00', 'mine-2')]
    ctx = make_ctx(calendar_holding(calendar), 'add the events from the mail of joost to calendar Exhibitions')
    payload = {'url': MAIL_52, 'calendar_id': 'cal-x', 'calendar': 'Exhibitions', 'page_tag': page_tag(MAIL_52),
               'events': [inauguration, play], 'notes': [], 'excluded': set(), 'period': None,
               'organiser': Organiser('mail.google.com', '', '')}
    preview, payload = AddEventsFromWeb().compose(payload, ctx)
    rows = {r.cells['Title']: r for r in preview.rows}
    assert rows['De Geuzen: Inwijding van de nieuwe leden'].note == 'already in the calendar as "Inweiding Geuzen" at 09:30'
    assert not rows['De Geuzen: Inwijding van de nieuwe leden'].selected
    assert rows['De Geuzen: De Geheime Kamer'].note == '' and rows['De Geuzen: De Geheime Kamer'].selected, 'a pair that is not there'
    assert len(asked) == 1 and 'Inweiding Geuzen' in asked[0], 'one question for the whole agenda'
    assert 'Tandarts' not in asked[0] and 'Geheime Kamer' not in asked[0], 'only events with others on the same day'
    AddEventsFromWeb().adjust(payload, {'default_duration': '3h'}, ctx)
    assert len(asked) == 1, 'Update preview does not ask again'


def test_without_the_model_the_preview_still_comes_and_says_so(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*args: object, **kwargs: object) -> None:
        """The model is out of reach."""
        raise LLMError('no answer')

    monkeypatch.setattr(same_event, 'ask', down)
    play = event('De Geuzen: De Geheime Kamer', date(2026, 11, 11), start_time=time(20, 0), url=MAIL_52)
    preview, _ = newer_mail(make_ctx, [by_hand('Toneel Kapellen', '2026-11-11T20:00')], [play])
    assert preview.rows[0].selected and any('in another form' in note for note in preview.notes)
