"""Reading agenda pages: dates always come from code, the model only points at numbered spans."""

from datetime import date, time
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from automation_desk import jobs
from automation_desk.capture import Captured
from automation_desk.groups.calendar import web_page
from automation_desk.groups.calendar.web_page import (
    Extraction,
    FoundEvent,
    ics_events,
    jsonld_events,
    marked_text,
    next_page,
    read_events,
    text_events,
)

from .conftest import TODAY, TZ

FIXTURES = Path(__file__).parent / 'fixtures'


def soup(name: str) -> BeautifulSoup:
    """A fixture page parsed with lxml."""
    return BeautifulSoup((FIXTURES / name).read_text(), 'lxml')


def test_jsonld_events_with_timezone_conversion_across_dst() -> None:
    events = jsonld_events(soup('jsonld.html'), 'https://museum.be/agenda', TZ)
    by_title = {e.title: e for e in events}
    expo = by_title['Rubens & zijn tijd']
    assert (expo.start, expo.end, expo.start_time) == (date(2026, 10, 1), date(2027, 1, 10), None)
    assert expo.location == 'KMSKA, Leopold De Waelplaats 1, Antwerpen'
    assert expo.description == 'Grote overzichtstentoonstelling.'
    assert expo.url == 'https://museum.be/expo/rubens'
    # CLAUDE> 24 Oct is still summer time (UTC+2); 25 Oct is winter time (UTC+1)
    assert by_title['Concert à midi'].start_time == time(22, 0)
    assert (by_title['Nacht'].start, by_title['Nacht'].start_time, by_title['Nacht'].end_time) == (
        date(2026, 10, 25), time(20, 0), time(23, 0))


def test_ics_events_all_day_end_is_inclusive() -> None:
    events = ics_events((FIXTURES / 'cal.ics').read_bytes(), 'https://x.be', TZ)
    studio, talk = events
    assert (studio.start, studio.end, studio.start_time) == (date(2026, 10, 3), date(2026, 10, 4), None)
    assert (talk.start, talk.start_time, talk.end_time) == (date(2026, 10, 6), time(19, 0), time(20, 30))


def test_marked_text_numbers_dates_times_and_links() -> None:
    spans = marked_text(soup('plain_nl.html'), 'https://venue.be/agenda', TODAY, TZ)
    assert '[D1: 25 sep]' in spans.text and '[T1: 20u30]' in spans.text and '[T2: 14h]' in spans.text
    assert '[D3 to D4: 12 - 20 oktober]' in spans.text
    assert spans.dates[1] == (date(2026, 9, 25), None)
    assert (spans.dates[3][0], spans.dates[4][0]) == (date(2026, 10, 12), date(2026, 10, 20))
    exact = next(n for n, value in spans.dates.items() if value == (date(2026, 10, 2), time(19, 0)))
    assert f'[D{exact}: vendredi 2 octobre]' in spans.text
    assert '12.50' in spans.text and 'T3' not in spans.text, 'a price must not become a time'
    until = next(n for n, (d, _) in spans.dates.items() if d == date(2027, 3, 7))
    assert spans.until == {until}, "'Nu → 7 mrt.'27' marks an end date"
    assert 'https://venue.be/agenda/jazz' in spans.links.values()


def test_text_events_builds_dates_from_span_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    spans = marked_text(soup('plain_nl.html'), 'https://venue.be/agenda', TODAY, TZ)
    jazz_link = next(n for n, url in spans.links.items() if url.endswith('/jazz'))
    until = next(iter(spans.until))
    reply = Extraction(events=[
        FoundEvent(title='Jazz op zolder', running_until=False, date_span=1, end_date_span=-1, time_span=1, end_time_span=-1,
                   location='Zaal Nova, Gent', description='', link_span=jazz_link, matches_filter=True, end_text=''),
        FoundEvent(title='Filmweek', running_until=False, date_span=3, end_date_span=4, time_span=-1, end_time_span=-1,
                   location='', description='', link_span=-1, matches_filter=False, end_text=''),
        FoundEvent(title='Expo', running_until=True, date_span=2, end_date_span=-1, time_span=-1, end_time_span=-1,
                   location='', description='', link_span=-1, matches_filter=True, end_text=''),
        FoundEvent(title='Tentoonstelling', running_until=False, date_span=until, end_date_span=until, time_span=-1,
                   end_time_span=-1, location='', description='', link_span=-1, matches_filter=True, end_text=''),
        FoundEvent(title='Summer show', running_until=False, date_span=1, end_date_span=-1, time_span=-1, end_time_span=-1,
                   location='', description='', link_span=-1, matches_filter=True, end_text='Summer 2027'),
        FoundEvent(title='Ghost', running_until=False, date_span=99, end_date_span=-1, time_span=-1, end_time_span=-1,
                   location='', description='', link_span=-1, matches_filter=True, end_text=''),
    ])
    seen: dict = {}
    monkeypatch.setattr(web_page, 'ask', lambda system, user, schema, **_: seen.setdefault('user', user) and reply)
    jazz, film, expo, marked, summer = text_events(spans, 'only music', 'https://venue.be/agenda', TODAY)
    assert 'only music' in seen['user']
    assert (jazz.start, jazz.start_time, jazz.url) == (date(2026, 9, 25), time(20, 30), 'https://venue.be/agenda/jazz')
    assert (film.start, film.end, film.matches_filter, film.url) == (date(2026, 10, 12), date(2026, 10, 20), False,
                                                                     'https://venue.be/agenda')
    assert (expo.start, expo.end) == (TODAY, date(2026, 9, 26)), "'until' events run from today to the marked date"
    assert (marked.start, marked.end) == (TODAY, date(2027, 3, 7)), 'code-detected end date wins over the model'
    assert (summer.end, summer.end_text) == (None, 'Summer 2027')


def test_next_page_follows_same_site_only() -> None:
    assert next_page(soup('plain_nl.html'), 'https://venue.be/agenda') == 'https://venue.be/agenda?page=2'
    other = BeautifulSoup('<a href="https://elsewhere.org/p2">next</a>', 'lxml')
    assert next_page(other, 'https://venue.be/agenda') is None


def test_read_events_prefers_ics_over_text(monkeypatch: pytest.MonkeyPatch) -> None:
    page = '<html><body><p>12 okt concert</p><a href="/agenda.ics">iCal</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve the agenda page and its .ics file."""
        if request.url.path.endswith('.ics'):
            return httpx.Response(200, content=(FIXTURES / 'cal.ics').read_bytes())
        return httpx.Response(200, html=page)

    monkeypatch.setattr(web_page, 'ask', lambda *a, **k: pytest.fail('the model must not be asked'))
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        events, notes, _ = read_events('https://venue.be/agenda', TODAY, TZ, '', 1, http)
    assert [e.title for e in events] == ['Open studio', 'Talk']
    assert 'iCalendar' in notes[0]


AGENDA = '<html><body><h2>Expo</h2><p>12 okt 2026</p><a href="/cal?p=2">next</a></body></html>'
EVENT = Extraction(events=[FoundEvent(title='Expo', running_until=False, date_span=1, end_date_span=-1, time_span=-1,
                                      end_time_span=-1, location='', description='', link_span=-1, matches_filter=True,
                                      end_text='')])


def browser_stub(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the user's browser with one that serves AGENDA; returns the URLs it read."""
    loaded: list[str] = []

    def read_in_browser(url: str) -> tuple[Captured, int]:
        """Stand-in for capture.read_in_browser."""
        loaded.append(url)
        return Captured(url, AGENDA, asked_you=False), 5

    monkeypatch.setattr(web_page, 'read_in_browser', read_in_browser)
    monkeypatch.setattr(web_page, 'ask', lambda *a, **k: EVENT)
    return loaded


def test_bot_check_falls_back_to_the_browser_and_stays_there(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = browser_stub(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        """A Cloudflare challenge for every plain download."""
        return httpx.Response(403, headers={'cf-mitigated': 'challenge'}, html='<html>Just a moment...</html>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        events, notes, _ = read_events('https://museum.org/cal', TODAY, TZ, '', 2, http)
    assert loaded == ['https://museum.org/cal', 'https://museum.org/cal?p=2'], 'page 2 goes straight to the browser'
    assert [e.start for e in events] == [date(2026, 10, 12)] * 2
    assert 'read in your browser' in notes[0]


def test_page_without_dates_is_loaded_again_in_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = browser_stub(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        """An empty shell that JavaScript would fill."""
        return httpx.Response(200, html='<html><body><div id="app"></div></body></html>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        events, _, _ = read_events('https://museum.org/cal', TODAY, TZ, '', 1, http)
    assert loaded == ['https://museum.org/cal'] and len(events) == 1


def test_a_normal_page_never_opens_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = browser_stub(monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, html=AGENDA))) as http:
        read_events('https://museum.org/cal', TODAY, TZ, '', 1, http)
    assert loaded == []


def test_event_page_gives_full_description_place_and_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / 'event_page.html').read_text()
    monkeypatch.setattr(web_page, 'fetch', lambda url, http, use_browser=False: web_page.Page(url, html, 'browser'))
    page = web_page.read_event_page('https://museum.org/exhibitions/5926', None, True, TZ)
    assert page.description.split('\n\n') == [
        'We swim in an ocean of data. Every click is tracked and fed into systems.',
        'While the practice of mapping information is ancient, computers upended it.',
        'Organized by Paola Antonelli.',
    ], 'all paragraphs of the block, not the summary with its date prefix'
    assert page.location == 'The Museum of Modern Art, 1 South, 11 West 53 Street, New York, NY 10019'
    assert (page.structured.start, page.structured.end) == (date(2026, 9, 27), date(2027, 6, 13))


def test_dates_are_tied_to_the_link_around_them() -> None:
    html = ('<a href="/ex/5920"><h3>Songs from Life</h3><p>Through Feb 21, 2027</p></a>'
            '<a href="/ex/5926"><h3>Full Disclosure</h3><p>Sep 27, 2026\u2013Jun 13, 2027</p></a>')
    spans = marked_text(BeautifulSoup(html, 'lxml'), 'https://museum.org/cal', TODAY, TZ)
    linked = {spans.dates[d][0]: spans.links[link] for d, link in spans.date_links.items()}
    assert linked[date(2026, 9, 27)] == 'https://museum.org/ex/5926'
    assert linked[date(2027, 2, 21)] == 'https://museum.org/ex/5920'
    assert '\x01' not in spans.text and '\x02' not in spans.text and '[L2] ' in spans.text


def test_event_pages_complete_events_and_are_recorded_on_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    html = (FIXTURES / 'event_page.html').read_text()
    read: list[str] = []

    def fetch(url: str, http: object, use_browser: bool = False) -> web_page.Page:
        """Record the read the way the real fetch does, from a worker thread."""
        read.append(url)
        web_page.record_fetch(url, 200, len(html), 1, via='download')
        return web_page.Page(url, html, 'download')

    monkeypatch.setattr(web_page, 'fetch', fetch)
    listed = [web_page.WebEvent('Full Disclosure', date(2026, 9, 27), None, None, None, '', '', 'https://museum.org/exhibitions/5926'),
              web_page.WebEvent('Old', date(2026, 1, 1), None, None, None, '', '', 'https://museum.org/exhibitions/1'),
              web_page.WebEvent('No link', date(2026, 10, 1), None, None, None, '', '', 'https://museum.org/cal')]
    with jobs.run(jobs.Job(group='calendar', sentence='s')) as job:
        done, notes = web_page.complete_from_event_pages(listed, {'https://museum.org/cal'}, TODAY, TZ, None, False)
    assert read == ['https://museum.org/exhibitions/5926'], 'past events and the list page itself are not read'
    assert done[0].end == date(2027, 6, 13) and done[0].location.startswith('The Museum of Modern Art')
    assert done[0].description.startswith('We swim') and done[2] == listed[2]
    assert [f.url for f in job.fetches] == ['https://museum.org/exhibitions/5926'], 'reads from worker threads count'
    assert 'Read 1 event page(s)' in notes[0]


def test_keyword_summary_is_not_taken_for_a_description() -> None:
    html = ('<html><head><meta name="description" content="kunst, gender, stereotypen, vrouwbeeld, manbeeld, gelijkheid, '
            'feminisme, collectie"></head><body><div class="intro"><p>Met een nieuwe opstelling richten de musea de '
            'aandacht op gender in de kunst.</p><p>De tentoonstelling toont werken uit vijf eeuwen, van schilderijen tot '
            'foto\'s, en stelt de vraag hoe beelden ons denken over mannen en vrouwen hebben gevormd.</p></div></body></html>')
    soup = BeautifulSoup(html, 'lxml')
    seed = soup.find('meta')['content']
    assert web_page.full_description(soup, seed).startswith('Met een nieuwe opstelling'), 'largest paragraph block'


def test_place_copied_by_the_model_must_be_on_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page = web_page.EventPage('', '', None, text='Location Circuit Ravenstein · Bozar rue Ravenstein,23 1000 Brussels Tickets')
    monkeypatch.setattr(web_page, 'ask', lambda *a, **k: web_page.PlaceFound(place='Bozar, rue Ravenstein,23 1000 Brussels'))
    assert web_page.find_place(page) == 'Bozar, rue Ravenstein,23 1000 Brussels'
    monkeypatch.setattr(web_page, 'ask', lambda *a, **k: web_page.PlaceFound(place='Bozar, Rue Ravenstein 23, Brussels, Belgium'))
    assert web_page.find_place(page) == '', 'an invented part (Belgium, reformatted address) is thrown away'


def test_fuller_place_wins_and_longer_description_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    html = ('<html><head><script type="application/ld+json">{"@type": "Event", "name": "Cezanne", "startDate": "2026-09-23", '
            '"endDate": "2027-01-17", "location": {"@type": "Place", "address": {"addressLocality": "Paris"}}}</script></head>'
            '<body><main><p>L\u2019exposition offre un dialogue riche entre l\u2019oeuvre de Cezanne et celle des artistes qui '
            'l\u2019ont admir\u00e9, sur plus d\u2019un si\u00e8cle, avec des pr\u00eats exceptionnels.</p>'
            '<p>Un second paragraphe tout aussi long pour que ce bloc soit clairement le texte principal de la page.</p>'
            '</main></body></html>')
    monkeypatch.setattr(web_page, 'fetch', lambda url, http, use_browser=False: web_page.Page(url, html, 'download'))
    listed = [web_page.WebEvent('Cezanne', date(2026, 9, 23), None, None, None, 'Grand Palais, Paris', 'Exposition',
                                'https://pompidou.fr/ev/1')]
    done, _ = web_page.complete_from_event_pages(listed, {'https://pompidou.fr/agenda'}, TODAY, TZ, None, False)
    assert done[0].location == 'Grand Palais, Paris', "'Paris' from the event data says less than the list"
    assert done[0].description.startswith('L\u2019exposition') and done[0].end == date(2027, 1, 17)


@pytest.mark.parametrize(('text', 'marked', 'days'), [
    ('De BiAF vindt plaats op zaterdag 10 en zondag 11 oktober 2026 in Antwerpen', '[D1, D2: 10 en zondag 11 oktober 2026]',
     [date(2026, 10, 10), date(2026, 10, 11)]),
    ('samedi 10 et dimanche 11 octobre', '[D1, D2: 10 et dimanche 11 octobre]', [date(2026, 10, 10), date(2026, 10, 11)]),
    ('October 10 and 11, 2026', '[D1, D2: October 10 and 11, 2026]', [date(2026, 10, 10), date(2026, 10, 11)]),
    ('3, 10 en 17 okt', '[D1, D2, D3: 3, 10 en 17 okt]', [date(2026, 10, 3), date(2026, 10, 10), date(2026, 10, 17)]),
    ('vr 9 t/m zo 11 okt', '[D1 to D2: 9 t/m zo 11 okt]', [date(2026, 10, 9), date(2026, 10, 11)]),
    ('oktober 12, 20:00', '[D1: oktober 12]', [date(2026, 10, 12)]),
])
def test_several_days_sharing_one_month(text: str, marked: str, days: list[date]) -> None:
    spans = marked_text(BeautifulSoup(f'<p>{text}</p>', 'lxml'), 'https://x.be', TODAY, TZ)
    assert marked in spans.text
    assert [spans.dates[n][0] for n in sorted(spans.dates)] == days


def test_a_time_written_with_a_trailing_u_is_read() -> None:
    spans = marked_text(BeautifulSoup('<p>ZA 3/10/2026 9:30U Athena-lezing</p>', 'lxml'), 'https://x.be', TODAY, TZ)
    assert '[T1: 9:30U]' in spans.text and spans.times[1] == time(9, 30)
