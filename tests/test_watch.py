"""Watched agenda sites: the list with a calendar per line, and checking them for new events only."""

from datetime import date, time

import pytest

from automation_desk.capture import NeedsPerson
from automation_desk.groups.calendar import watch
from automation_desk.groups.calendar.organisers import Organiser
from automation_desk.groups.calendar.tasks import check_watched as module
from automation_desk.groups.calendar.tasks.add_events_from_web import event_body, page_tag, source_key
from automation_desk.groups.calendar.tasks.check_watched import CheckWatchedSites
from automation_desk.groups.calendar.web_page import WebEvent

from .conftest import FakeGoogle


def test_the_list_takes_a_calendar_per_line_or_the_default() -> None:
    assert watch.default_calendar() == 'events'
    watch.save_list(['kmska.be/nl/agenda → Exhibitions', 'https://www.mas.be/ -> events', '', 'tate.org.uk', 'tate.org.uk'])
    assert [(s.site, s.calendar) for s in watch.sites()] == [
        ('https://kmska.be/nl/agenda', 'Exhibitions'), ('https://www.mas.be', 'events'), ('https://tate.org.uk', '')]
    assert watch.as_lines() == ['kmska.be/nl/agenda → Exhibitions', 'mas.be → events', 'tate.org.uk']
    watch.set_default_calendar('Musea')
    assert watch.calendar_for(watch.sites()[2]) == 'Musea'
    watch.save_list(['tate.org.uk'])
    assert [s.site for s in watch.sites()] == ['https://tate.org.uk'], 'a line deleted is a site no longer watched'


def ev(title: str, day: date, url: str, **kwargs: object) -> WebEvent:
    """An event as the import reads it."""
    fields = {'start_time': time(14), 'end': None, 'end_time': time(16), 'location': '', 'description': '', 'url': url, **kwargs}
    return WebEvent(title=title, start=day, **fields)


A, B = 'https://a.be/agenda', 'https://b.be/agenda'
PAGES = {A: [ev('Nieuw', date(2026, 10, 3), A), ev('Al gezien', date(2026, 10, 4), A), ev('Voorbij', date(2026, 9, 1), A)],
         B: [ev('Concert', date(2026, 10, 9), B), ev('Overgeslagen', date(2026, 10, 10), B)]}


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Two watched sites, their pages as stand-ins, and a calendar holding one earlier import."""
    watch.save_list(['a.be/agenda → Exhibitions', 'b.be/agenda'])
    watch.skip({source_key(B, PAGES[B][1])})
    seen: dict = {'inserted': []}

    def read(url: str, today: date, tz: object, text_filter: str, max_pages: int, http: object, browser_first: bool = False) -> tuple:
        """Stand-in for read_events."""
        return PAGES[url], [], '<html></html>'

    monkeypatch.setattr(module, 'read_events', read)
    monkeypatch.setattr(module, 'organiser_for', lambda url, html, http=None, site=None: Organiser(url, '', ''))
    earlier = event_body(PAGES[A][1], 'Europe/Brussels', source_key(A, PAGES[A][1]), page_tag(A))
    seen['google'] = FakeGoogle({
        'calendarList.list': lambda **_: {'items': [{'id': 'cal-x', 'summary': 'Exhibitions'}, {'id': 'cal-e', 'summary': 'Events'}]},
        'events.list': lambda **kw: {'items': [{**earlier, 'id': 'ev-1'}] if kw.get('calendarId') == 'cal-x' else []},
        'events.insert': lambda **kw: seen['inserted'].append((kw['calendarId'], kw['body']['summary'])) or {},
        'events.patch': lambda **kw: {},
    })
    return seen


def test_a_check_shows_new_events_and_keeps_declined_ones_unticked(make_ctx, sites: dict) -> None:
    ctx = make_ctx(sites['google'], 'check my agenda sites')
    preview, payload = CheckWatchedSites().check(ctx)
    assert {r.cells['Title']: r.selected for r in preview.rows} == {'Nieuw': True, 'Concert': True, 'Overgeslagen': False}, (
        'not the earlier import, not the past one; the one declined before stays in the list, unticked')
    assert next(r.note for r in preview.rows if r.cells['Title'] == 'Overgeslagen') == 'declined before'
    assert preview.summary == '2 new events'
    assert [(g.name, g.calendar, g.note) for g in preview.groups] == [
        ('a.be/agenda', 'Exhibitions', '1 new'), ('b.be/agenda', 'Events', '1 new · 1 declined before')]
    assert {r.cells['Title']: r.group for r in preview.rows} == {'Nieuw': 'a.be/agenda', 'Concert': 'b.be/agenda',
                                                                 'Overgeslagen': 'b.be/agenda'}
    concert = next(r.id for r in preview.rows if r.cells['Title'] == 'Concert')
    CheckWatchedSites().execute(payload, {concert}, ctx)
    assert sites['inserted'] == [('cal-e', 'Concert')]
    preview, _payload = CheckWatchedSites().check(ctx)
    assert {r.cells['Title']: r.selected for r in preview.rows} == {'Concert': True, 'Nieuw': False, 'Overgeslagen': False}, (
        'Nieuw was left unticked: from now on it is shown unticked (Concert stays new: the stand-in calendar keeps no inserts)')


def test_one_site_is_added_on_its_own_and_the_rest_stays(make_ctx, sites: dict) -> None:
    ctx = make_ctx(sites['google'], 'x')
    preview, payload = CheckWatchedSites().check(ctx)
    ids = {r.cells['Title']: r.id for r in preview.rows}
    results, done = CheckWatchedSites().execute_group(payload, {ids['Concert'], ids['Nieuw']}, 'b.be/agenda', ctx)
    assert sites['inserted'] == [('cal-e', 'Concert')], "only b.be's ticked event, not a.be's"
    assert done == {ids['Concert'], ids['Overgeslagen']} and len(results) == 1
    assert [s['label'] for s in payload['sites']] == ['a.be/agenda'] and payload['offered'] == [ids['Nieuw']]
    CheckWatchedSites().execute(payload, {ids['Nieuw']}, ctx)
    assert sites['inserted'] == [('cal-e', 'Concert'), ('cal-x', 'Nieuw')], 'the rest is added later, b.be not again'


def test_a_site_with_nothing_ticked_declines_all_its_events(make_ctx, sites: dict) -> None:
    ctx = make_ctx(sites['google'], 'x')
    preview, payload = CheckWatchedSites().check(ctx)
    ids = {r.cells['Title']: r.id for r in preview.rows}
    CheckWatchedSites().execute_group(payload, set(), 'a.be/agenda', ctx)
    assert sites['inserted'] == [] and ids['Nieuw'] in watch.skipped()
    preview, _payload = CheckWatchedSites().check(ctx)
    assert preview.groups[0].note == 'nothing new · 1 declined before'


def test_a_site_that_needs_the_browser_is_listed_and_no_tab_opens(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(url: str, *a: object) -> tuple:
        """a.be blocks bots."""
        if url == A:
            raise NeedsPerson(f'{url} can only be read through your browser.')
        return PAGES[url], [], ''

    monkeypatch.setattr(module, 'read_events', blocked)
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert [r.cells['Title'] for r in preview.rows if r.selected] == ['Concert']
    assert watch.sites()[0].last_result == watch.NEEDS_BROWSER


def test_a_site_the_model_cannot_read_does_not_stop_the_others(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk.llm import LLMError

    def unanswered(url: str, *a: object) -> tuple:
        """The model gives no usable answer for a.be."""
        if url == A:
            raise LLMError('The model gave no valid answer twice.')
        return PAGES[url], [], ''

    monkeypatch.setattr(module, 'read_events', unanswered)
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert [r.cells['Title'] for r in preview.rows if r.selected] == ['Concert']
    assert [(g.name, g.note) for g in preview.groups] == [
        ('a.be/agenda', 'could not be read: the language model gave no usable answer; check again later'),
        ('b.be/agenda', '1 new · 1 declined before')]
    assert preview.notes == [], 'said once, at the site'


def test_a_site_without_events_is_nothing_new(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, 'read_events', lambda url, *a: ([] if url == A else PAGES[url], [], ''))
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert [r.cells['Title'] for r in preview.rows if r.selected] == ['Concert']
    assert watch.sites()[0].last_result == 'nothing new'


def test_an_event_added_by_hand_is_already_in_the_calendar(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """MuHKA's 'The Situation is Fluid' was typed in by the user as 'MHKA: ...': same day, same words, so not new."""
    by_hand = {'id': 'mine', 'summary': 'CONCERT!', 'start': {'date': '2026-10-09'}, 'end': {'date': '2026-10-10'}}
    linked = {'id': 'mine-2', 'summary': 'Iets anders', 'start': {'dateTime': '2026-10-03T14:00:00+02:00'},
              'description': '<a href="https://a.be/agenda/nieuw">meer</a>'}
    calls = sites['google'].handlers
    earlier = calls['events.list']
    calls['events.list'] = lambda **kw: {'items': [by_hand] if kw['calendarId'] == 'cal-e' else earlier(**kw)['items'] + [linked]}
    pages = {**PAGES, A: [ev('Nieuw', date(2026, 10, 3), 'https://a.be/agenda/nieuw'), *PAGES[A][1:]]}
    monkeypatch.setattr(module, 'read_events', lambda url, *a: (pages[url], [], ''))
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert [r.cells['Title'] for r in preview.rows] == ['Overgeslagen'], 'Concert by its title and day, Nieuw by its link'
    assert [g.note for g in preview.groups] == ['nothing new', 'nothing new · 1 declined before']


def test_an_imported_event_whose_page_changed_is_not_offered(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """MuHKA's Lee Bul was imported before; the page now words its place differently. It is in the calendar: not new."""
    moved = {**PAGES, A: [PAGES[A][0], ev('Al gezien', date(2026, 10, 4), A, location='Elders 1, Antwerpen'), PAGES[A][2]]}
    monkeypatch.setattr(module, 'read_events', lambda url, *a: (moved[url], [], ''))
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert 'Al gezien' not in [r.cells['Title'] for r in preview.rows]


def test_a_site_read_through_the_browser_is_read_that_way_at_every_check(make_ctx, sites: dict,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Kanal: the download holds only 'Discover Kanal from 28 November'; the list is in the browser. Once read that way,
    every check reads it through the browser straight away (in the background), never its misleading download."""
    reads = []
    monkeypatch.setattr(module, 'read_events', lambda url, *a: reads.append((url, a[-1])) or (PAGES[url], [], ''))
    kanal = watch.sites()[0]
    CheckWatchedSites().check(make_ctx(sites['google'], 'x'), via_browser=True, only={kanal.id})
    assert reads == [(A, True)], 'the browser straight away, not the download first'
    reads.clear()
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert reads == [(A, True), (B, False)]
    assert preview.groups[0].note == '1 new'


def test_a_site_that_fell_back_to_the_browser_is_remembered(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def read(url: str, *a: object) -> tuple:
        """a.be's download had no events, so the import read it in the browser."""
        return PAGES[url], [f'{url} (read in your browser): 3 event(s) from page text.'] if url == A else [], ''

    monkeypatch.setattr(module, 'read_events', read)
    CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert watch.browser_sites() == {watch.sites()[0].id}


def test_a_browser_read_that_fails_marks_only_that_site(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk.capture import CaptureError

    def read(url: str, *a: object) -> tuple:
        """The extension is not loaded for a.be's browser read."""
        if url == A:
            raise CaptureError('The Automation desk reader did not answer.')
        return PAGES[url], [], ''

    monkeypatch.setattr(module, 'read_events', read)
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert preview.groups[0].note == 'could not be read: The Automation desk reader did not answer.'
    assert [r.cells['Title'] for r in preview.rows if r.selected] == ['Concert']


def test_a_check_never_brings_a_browser_tab_forward_unless_asked(make_ctx, sites: dict,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The browser reads in the background at every check; only Read via browser may show a site's human check."""
    from automation_desk.groups.calendar import web_page

    may_ask = []
    monkeypatch.setattr(module, 'read_events', lambda url, *a: may_ask.append(web_page.BROWSER_MAY_ASK.get()) or (PAGES[url], [], ''))
    CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    CheckWatchedSites().check(make_ctx(sites['google'], 'x'), via_browser=True, only={watch.sites()[1].id})
    assert may_ask == [False, False, True]


def test_adding_needs_sites(make_ctx) -> None:
    with pytest.raises(Exception, match='Add agenda sites'):
        CheckWatchedSites().check(make_ctx(FakeGoogle({'calendarList.list': lambda **_: {'items': []}}), 'x'))


def test_sites_come_from_the_events_imported_before() -> None:
    """Each import keeps its agenda page's fingerprint and the event's own link: the page is found among the link's parents."""
    def imported(link: str, page: str) -> dict:
        """An event imported from `page`, whose own page is `link`."""
        return {'id': link, 'summary': 'x', 'description': f'Info.\n\nSource: {link}',
                'extendedProperties': {'private': {'automation_desk_page': page_tag(page), 'automation_desk_key': link}}}

    events = {'cal-x': [imported('https://www.kmska.be/nl/agenda/expo-rubens', 'https://kmska.be/nl/agenda'),
                        imported('https://www.kmska.be/nl/agenda/lezing', 'https://kmska.be/nl/agenda'),
                        imported('https://www.muhka.be/en/exhibitions/ongoing/', 'https://www.muhka.be/en/exhibitions/ongoing/'),
                        imported('https://www.muhka.be/en/exhibitions/fluid/', 'https://www.muhka.be/en/exhibitions/ongoing/')],
              'cal-e': [imported('https://mas.be/nl/expo', 'https://mas.be'),
                        {'id': 'p', 'description': 'Source: file:abc',
                         'extendedProperties': {'private': {'automation_desk_page': 'zz'}}},
                        {'id': 'own', 'summary': 'Dentist'}]}
    google = FakeGoogle({
        'calendarList.list': lambda **_: {'items': [{'id': 'cal-x', 'summary': 'Exhibitions'}, {'id': 'cal-e', 'summary': 'events'}]},
        'events.list': lambda **kw: {'items': events[kw['calendarId']]},
    })
    found, unmatched = module.sites_from_events(google)
    assert found == [('https://kmska.be/nl/agenda', 'Exhibitions'), ('https://www.muhka.be/en/exhibitions/ongoing', 'Exhibitions'),
                     ('https://mas.be', 'events')], (
        'the page itself (with or without www), in the calendar its events are in; also when an event links to the page itself')
    assert unmatched == 1, 'the PDF import has no web page'
    watch.save_list(['tate.org.uk/whats-on'])
    assert watch.add_sites(found) == 3
    assert watch.as_lines() == ['tate.org.uk/whats-on', 'kmska.be/nl/agenda → Exhibitions',
                                'muhka.be/en/exhibitions/ongoing → Exhibitions', 'mas.be'], (
        'added after the existing lines; the default calendar needs no arrow')
    assert watch.add_sites(found) == 0, 'nothing twice'


def test_the_import_commands_give_pages_that_are_no_parent_and_mail_is_no_agenda() -> None:
    """MuHKA's agenda was /en/programma/ while its events live under /en/exhibitions/: the import command has the address."""
    from automation_desk import ledger

    with ledger.connect(write=True) as db:
        db.execute("INSERT INTO jobs (id, grp, sentence, started) VALUES ('j1', 'calendar', "
                   "'add all events from https://www.muhka.be/en/programma/ to calendar Test', '2026-09-23T19:48:00')")

    def imported(link: str, page: str) -> dict:
        """An event imported from `page`, whose own page is `link`."""
        tags = {'automation_desk_page': page_tag(page)}
        return {'id': link, 'description': f'Source: {link}', 'extendedProperties': {'private': tags}}

    events = [imported('https://www.muhka.be/en/exhibitions/fluid/', 'https://www.muhka.be/en/programma/'),
              imported('https://mail.google.com/mail/u/0/#all/1a0c', 'https://mail.google.com/mail/u/0/#all/1a0c')]
    google = FakeGoogle({'calendarList.list': lambda **_: {'items': [{'id': 'cal-t', 'summary': 'Test'}]},
                         'events.list': lambda **_: {'items': events}})
    assert module.sites_from_events(google) == ([('https://www.muhka.be/en/programma', 'Test')], 1)


def test_events_added_by_hand_give_the_page_that_lists_them() -> None:
    """Axel Vervoordt events were added by hand: no fingerprint, so the parent page that links to them is the agenda."""
    import httpx

    def own(link: str) -> dict:
        """An event added by hand with its page's link in the description."""
        return {'id': link, 'summary': 'x', 'description': f'<a href="{link}">{link}</a>'}

    calendars = {'cal-e': [own('https://www.axel-vervoordt.com/gallery/exhibitions/jef-verheyen'),
                           own('https://www.axel-vervoordt.com/gallery/exhibitions/germaine-kruip')],
                 'cal-p': [own('https://zoom.us/j/123')]}
    google = FakeGoogle({'calendarList.list': lambda **_: {'items': [{'id': 'cal-e', 'summary': 'Events'},
                                                                     {'id': 'cal-p', 'summary': 'me@gmail.com'}]},
                         'events.list': lambda **kw: {'items': calendars[kw['calendarId']]}})
    pages = {'/gallery/exhibitions': '<a href="/gallery/exhibitions/jef-verheyen">Jef</a><a href="/gallery/exhibitions/other">x</a>',
             '/gallery': '<a href="/gallery/exhibitions">Exhibitions</a>'}
    asked = []

    def serve(request: httpx.Request) -> httpx.Response:
        """The site's pages."""
        asked.append(request.url.path)
        return httpx.Response(200, text=pages.get(request.url.path, 'nothing'), request=request)

    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        found, _unmatched = module.sites_from_events(google, http)
    assert found == [('https://www.axel-vervoordt.com/gallery/exhibitions', 'Events')]
    assert asked == ['/gallery/exhibitions'], 'nearest parent first; a meeting link in another calendar is not read'


def _hand_added(links: list[str], pages: dict[str, str]) -> list[tuple[str, str]]:
    """The sites found for events added by hand with these links, reading these pages (path → html)."""
    import httpx
    events = [{'id': str(i), 'summary': 'x', 'description': f'<a href="{link}">{link}</a>'} for i, link in enumerate(links)]
    google = FakeGoogle({'calendarList.list': lambda **_: {'items': [{'id': 'cal-e', 'summary': 'Events'}]},
                         'events.list': lambda **_: {'items': events}})
    def serve(request): return httpx.Response(200 if request.url.path in pages else 404, text=pages.get(request.url.path, ''),
                                              request=request)
    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        return module.sites_from_events(google, http)[0]


def test_a_home_page_listing_the_events_gives_its_agenda_link() -> None:
    """Bozar: /nl/kalender does not exist and /nl links to the event, but the agenda is the /nl/calendar it links to."""
    pages = {'/nl': '<a href="/nl/calendar">Kalender</a><a href="/nl/kalender/jean-brusselmans">Jean</a>'}
    found = _hand_added(['https://www.bozar.be/nl/kalender/jean-brusselmans'], pages)
    assert found == [('https://www.bozar.be/nl/calendar', 'Events')]


def test_an_event_linking_only_to_the_home_page_gives_the_agenda_with_the_events() -> None:
    """Patrick Derom: the event links the home page; of its /events/ and /exhibitions/, the latter holds the shows."""
    pages = {'/': '<a href="/events/">Events</a><a href="/exhibitions/">Exhibitions</a><a href="/exhibitions/11-ai-weiwei/">Ai</a>'}
    assert _hand_added(['https://patrickderomgallery.com'], pages) == [('https://patrickderomgallery.com/exhibitions', 'Events')]


def test_adding_sites_from_events_can_be_stopped() -> None:
    """Stop after the first site: the one found is kept, the rest is not read."""
    import httpx

    from automation_desk import stop

    links = ['https://www.axel-vervoordt.com/gallery/exhibitions/jef', 'https://www.bozar.be/nl/kalender/jean']
    events = [{'id': str(i), 'summary': 'x', 'description': link} for i, link in enumerate(links)]
    google = FakeGoogle({'calendarList.list': lambda **_: {'items': [{'id': 'cal-e', 'summary': 'Events'}]},
                         'events.list': lambda **_: {'items': events}})
    pages = {'/gallery/exhibitions': '<a href="/gallery/exhibitions/jef">Jef</a>', '/nl/kalender': '<a href="/nl/kalender/jean">J</a>'}
    asked = []

    def serve(request: httpx.Request) -> httpx.Response:
        """The user presses Stop during the first read."""
        asked.append(request.url.host)
        stop.request('watch-from-events')
        return httpx.Response(200 if request.url.path in pages else 404, text=pages.get(request.url.path, ''), request=request)

    with httpx.Client(transport=httpx.MockTransport(serve)) as http:
        found, _ = module.sites_from_events(google, http)
    assert found == [('https://www.axel-vervoordt.com/gallery/exhibitions', 'Events')] and set(asked) == {'www.axel-vervoordt.com'}


def test_a_check_can_be_stopped_and_shows_what_was_read(make_ctx, sites: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from automation_desk import stop

    def read(url: str, *a: object) -> tuple:
        """The user presses Stop while a.be is read."""
        stop.request('watch-check')
        return PAGES[url], [], ''

    monkeypatch.setattr(module, 'read_events', read)
    preview, _payload = CheckWatchedSites().check(make_ctx(sites['google'], 'x'))
    assert [(g.name, g.note) for g in preview.groups] == [('a.be/agenda', '1 new'),
                                                          ('b.be/agenda', 'not checked: you stopped the check')]
