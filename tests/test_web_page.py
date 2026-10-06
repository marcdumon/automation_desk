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

    def read_in_browser(url: str, may_ask: bool = True) -> tuple[Captured, int]:
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


def test_a_refused_download_is_retried_like_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401, text='no programs')))
    answer = httpx.Response(200, text='<p>Nieuws</p>', request=httpx.Request('GET', 'https://www.wsj.com/world'))
    monkeypatch.setattr(web_page, 'chrome_like', lambda url: answer)
    job = jobs.Job(group='news', sentence='test', task_id='t', task_name='t')
    with jobs.run(job):
        got = web_page.download('https://wsj.com/world', refused)
    assert (got.status_code, got.text) == (200, '<p>Nieuws</p>')
    assert [f.via for f in job.fetches] == ['download', 'Chrome-like download'], 'both tries are recorded on the job'


def test_a_retry_that_is_refused_too_keeps_the_first_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, text='no')))
    monkeypatch.setattr(web_page, 'chrome_like', lambda url: httpx.Response(403, request=httpx.Request('GET', url)))
    assert web_page.download('https://bloomberg.com/europe', refused).status_code == 403


def test_a_chrome_like_answer_is_not_unpacked_twice() -> None:
    """The Chrome-like client hands over the page already unpacked, with the header that says it was packed."""
    got = web_page.as_httpx(200, {'Content-Encoding': 'gzip', 'Content-Length': '99', 'Content-Type': 'text/html'},
                            b'<p>Nieuws</p>', 'https://www.nytimes.com/')
    assert (got.text, str(got.url), got.headers['content-type']) == ('<p>Nieuws</p>', 'https://www.nytimes.com/', 'text/html')


DATADOME = ("<html><head><title>economist.com</title></head><body><p>Please enable JS and disable any ad blocker</p>"
            "<script>var dd={'rt':'i','host':'geo.captcha-delivery.com'}</script>"
            "<script src='https://ct.captcha-delivery.com/c.js'></script></body></html>")


@pytest.mark.parametrize(('html', 'check'), [
    (DATADOME, True),
    ("<html><title>Just a moment...</title><script src='https://challenges.cloudflare.com/x.js'></script></html>", True),
    ('<html><title>News</title><p>' + 'Nieuws ' * 20000 + "</p><script src='https://www.google.com/recaptcha/api.js'></script></html>",
     False),
    ('<html><title>Blog</title><p>Kort bericht.</p></html>', False),
], ids=['datadome', 'cloudflare', 'news page with a recaptcha form', 'small page'])
def test_a_check_page_is_recognised_by_what_it_loads(html: str, check: bool) -> None:
    """The Economist's DataDome check has no telling title: a small page loading a check service is a check."""
    assert web_page.looks_like_check(html) is check


def test_a_connection_that_fails_once_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """34 sites 'did not answer' at the same moment, and all answered a minute later: one retry rides out a hiccup."""
    tries = []

    def flaky(request: httpx.Request) -> httpx.Response:
        """Fails the first time, answers the second."""
        tries.append(1)
        if len(tries) == 1:
            raise httpx.ConnectError('no route', request=request)
        return httpx.Response(200, text='ok')

    monkeypatch.setattr(web_page, 'RETRY_AFTER_S', 0)
    assert web_page.download('https://a.be', httpx.Client(transport=httpx.MockTransport(flaky))).text == 'ok'
    assert len(tries) == 2


def test_day_and_month_without_a_year() -> None:
    """'Zaterdag 26/9- Zondag 27/9' is two dates; '/2026/09/25/' in an address is not a date."""
    assert web_page.read_dates('Zaterdag 26/9- Zondag 27/9', date(2026, 9, 23)) == [date(2026, 9, 26), date(2026, 9, 27)]
    assert web_page.read_dates('zie https://site.be/2026/09/25/expo en /09/25/', date(2026, 9, 23)) == []


def test_a_page_too_long_to_answer_at_once_is_asked_in_halves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Axel Vervoordt lists 130 exhibitions on one page: the model's list got cut off, so each half is asked on its own."""
    from automation_desk.groups.calendar.web_page import Spans
    from automation_desk.llm import CutOff

    lines = [f'Show {n} [D{n}: {n} okt]\n' for n in range(1, 9)]
    spans = Spans(dates={n: (date(2026, 10, n), None) for n in range(1, 9)}, times={}, links={}, text=''.join(lines))
    asked = []

    def answer(system: str, user: str, schema: type, **_: object) -> Extraction:
        """Cuts off when asked about more than four shows."""
        shown = [n for n in range(1, 9) if f'[D{n}:' in user]
        asked.append(len(shown))
        if len(shown) > 4:
            raise CutOff('The answer was longer than the output limit and got cut off.')
        return Extraction(events=[FoundEvent(title=f'Show {n}', date_span=n, running_until=False, end_date_span=-1, time_span=-1,
                                             end_time_span=-1, location='', description='', end_text='', link_span=-1,
                                             matches_filter=True) for n in shown])

    monkeypatch.setattr(web_page, 'ask', answer)
    events = text_events(spans, '', 'https://x.be/agenda', TODAY)
    assert [e.title for e in events] == [f'Show {n}' for n in range(1, 9)]
    assert asked == [8, 4, 4]


def test_dates_written_with_dots_as_smak_does() -> None:
    """S.M.A.K.: 'tot 10.Jan.27', '31.Okt.26 tot 21.Feb.27'; a time after 'okt.' stays a time."""
    from automation_desk.groups.calendar.web_page import read_dates

    assert read_dates('tot 10.Jan.27 · 31.Okt.26 tot 21.Feb.27', date(2026, 9, 26)) == [
        date(2027, 1, 10), date(2026, 10, 31), date(2027, 2, 21)]
    assert read_dates('12 okt. 20:00', date(2026, 9, 26)) == [date(2026, 10, 12)]


def test_a_downloaded_page_without_events_is_loaded_again_in_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kanal: JavaScript fills the agenda; the downloaded shell holds one stray date ('Discover Kanal from 28 November')."""
    loaded = browser_stub(monkeypatch)
    answers = iter([Extraction(events=[]), EVENT])
    monkeypatch.setattr(web_page, 'ask', lambda *a, **k: next(answers))
    shell = '<html><body><p>Discover Kanal from 28 November 2026.</p><div id="agenda"></div></body></html>'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, html=shell))) as http:
        events, _, _ = read_events('https://kanal.brussels/en/calendar', TODAY, TZ, '', 1, http)
    assert loaded == ['https://kanal.brussels/en/calendar'] and [e.title for e in events] == ['Expo']


def test_a_year_written_on_the_page_is_kept_even_long_past() -> None:
    """Patrick Derom's archive: 'TEFAF Maastricht 2018 · 10 - 18 Mar 2018' is 2018, not next March."""
    from automation_desk.groups.calendar.web_page import read_dates

    assert read_dates('10 - 18 Mar 2018', date(2026, 9, 26)) == [date(2018, 3, 10), date(2018, 3, 18)]
    assert read_dates('12 okt', date(2026, 9, 26)) == [date(2026, 10, 12)], 'no year: the next one'


def test_a_range_with_its_year_only_at_the_end_is_in_that_year() -> None:
    """Patrick Derom: 'Brafa 2026 · 25 Jan - 1 Feb 2026' is January 2026, not 2027; '20 Dec - 5 Jan 2027' starts in 2026."""
    from automation_desk.groups.calendar.web_page import read_dates

    today = date(2026, 9, 26)
    assert read_dates('25 Jan - 1 Feb 2026', today) == [date(2026, 1, 25), date(2026, 2, 1)]
    assert read_dates('20 Dec - 5 Jan 2027', today) == [date(2026, 12, 20), date(2027, 1, 5)]
    assert read_dates('12 okt - 3 nov', today) == [date(2026, 10, 12), date(2026, 11, 3)], 'no year at all: as before'


def test_an_empty_link_covering_a_card_links_the_card_to_its_page() -> None:
    """Bozar: <div class="card"><a class="card-link" href="/nl/kalender/..."></a> ... dates, title ...</div>."""
    html = ('<div class="card"><a class="card-link" href="/nl/kalender/jean-brusselmans"></a><p>2 Okt.\'26 → 31 Jan.\'27</p>'
            '<h3>Jean Brusselmans</h3></div>'
            '<div class="card"><a class="card-link" href="/nl/kalender/rachel-whiteread"></a><p>12 Mar.\'27</p><h3>Rachel</h3></div>'
            '<div><a href="/nl/nieuws"></a><a href="/nl/tickets"></a><p>Nieuws 3 okt 2026</p></div>')
    spans = marked_text(BeautifulSoup(html, 'lxml'), 'https://www.bozar.be/nl/tentoonstellingen', TODAY, TZ)
    linked = {spans.dates[d][0]: spans.links[spans.date_links[d]] for d in spans.date_links}
    assert linked == {date(2026, 10, 2): 'https://www.bozar.be/nl/kalender/jean-brusselmans',
                      date(2027, 1, 31): 'https://www.bozar.be/nl/kalender/jean-brusselmans',
                      date(2027, 3, 12): 'https://www.bozar.be/nl/kalender/rachel-whiteread'}, 'a block with two links is no card'


def test_a_two_digit_year_is_kept_as_written() -> None:
    """Bozar's archive: "14 Nov.'24" is 2024, not next November."""
    from automation_desk.groups.calendar.web_page import read_dates

    assert read_dates("14 Nov.'24", date(2026, 9, 26)) == [date(2024, 11, 14)]


def test_a_card_with_a_title_link_links_its_dates_too() -> None:
    """Axel Vervoordt: <li class="c-card"><p><a href=...>Title</a></p> From September 12 → November 21, 2026</li>."""
    html = ('<ul><li class="c-card"><p class="head"><a href="/gallery/exhibitions/jef">Jef Verheyen</a></p>'
            '<p>From September 12 → November 21, 2026</p></li>'
            '<li class="c-card"><p class="head"><a href="/gallery/exhibitions/kim">Kim Lim</a></p>'
            '<p>From October 3, 2026</p></li></ul>')
    spans = marked_text(BeautifulSoup(html, 'lxml'), 'https://www.axel-vervoordt.com/gallery/exhibitions', TODAY, TZ)
    linked = {spans.dates[d][0]: spans.links[spans.date_links[d]].rsplit('/', 1)[1] for d in spans.date_links}
    assert linked == {date(2026, 9, 12): 'jef', date(2026, 11, 21): 'jef', date(2026, 10, 3): 'kim'}


def test_past_exhibitions_are_left_out_of_what_the_model_reads() -> None:
    """Axel Vervoordt lists its whole archive; only cards with a date still to come (or 'ongoing') go to the model."""
    html = ('<ul><li><p><a href="/ex/old">Old show</a></p><p>From March 1 → April 30, 2019</p></li>'
            '<li><p><a href="/ex/now">Current show</a></p><p>From April 11 → November 14, 2026</p></li>'
            '<li><p><a href="/ex/collection">Collection</a></p><p>Since June 1, 2020, ongoing</p></li></ul>')
    spans = marked_text(BeautifulSoup(html, 'lxml'), 'https://www.axel-vervoordt.com/gallery/exhibitions', TODAY, TZ)
    assert 'Old show' not in spans.text and 'Current show' in spans.text and 'Collection' in spans.text
    assert len(spans.date_links) == 5, 'the code still knows every date and its page'


def test_read_via_browser_skips_the_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """A site the user reads through the browser: its download would give a stray line, not the agenda."""
    loaded = browser_stub(monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, html=AGENDA))) as http:
        read_events('https://kanal.brussels/en/calendar', TODAY, TZ, '', 1, http, browser_first=True)
    assert loaded == ['https://kanal.brussels/en/calendar']


def test_a_browser_read_during_a_check_stays_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = []
    monkeypatch.setattr(web_page, 'read_in_browser',
                        lambda url, may_ask=True: asked.append(may_ask) or (Captured(url, AGENDA, False), 5))
    token = web_page.BROWSER_MAY_ASK.set(False)
    try:
        web_page.browser_page('https://kanal.brussels/en/calendar')
    finally:
        web_page.BROWSER_MAY_ASK.reset(token)
    web_page.browser_page('https://kanal.brussels/en/calendar')
    assert asked == [False, True]


def test_a_page_read_in_the_browser_is_kept_to_look_at(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """What the user's browser handed over is saved (the latest per site), so a wrong result can be traced to it."""
    monkeypatch.setattr(web_page, 'CAPTURES', tmp_path)
    monkeypatch.setattr(web_page, 'read_in_browser', lambda url, may_ask=True: (Captured(url, AGENDA, False), 5))
    web_page.browser_page('https://kanal.brussels/en/calendar?production_type=Exhibition')
    assert [p.name for p in tmp_path.iterdir()] == ['kanal.brussels.html'] and 'Expo' in (tmp_path / 'kanal.brussels.html').read_text()


def test_a_dotted_start_before_a_full_end_date_is_a_date() -> None:
    """Tick Tack: '09.10—19.12.2026' starts on 9 October 2026; '13.12—18.01.2025' on 13 December 2024. A lone '12.50' stays
    a price or a time."""
    from automation_desk.groups.calendar.web_page import read_dates

    today = date(2026, 9, 26)
    assert read_dates('09.10—19.12.2026', today) == [date(2026, 10, 9), date(2026, 12, 19)]
    assert read_dates('13.12—18.01.2025', today) == [date(2024, 12, 13), date(2025, 1, 18)]
    assert read_dates('Tickets 12.50 - 15.00', today) == []


def test_bozar_writes_maart_as_maa() -> None:
    """Bozar: 'Nu → 7 Maa.'27' (Kentridge), '12 Maa. → 13 Juni'27' (Kasuba): four exhibitions had no start and were dropped."""
    from automation_desk.groups.calendar.web_page import read_dates

    today = date(2026, 10, 4)
    assert read_dates("7 Maa.'27", today) == [date(2027, 3, 7)]
    assert read_dates("12 Maa. → 13 Juni'27", today) == [date(2027, 3, 12), date(2027, 6, 13)]


def test_a_start_with_a_dot_takes_the_year_of_the_end_on_the_next_line() -> None:
    """Bozar: '20 Feb. →' then '14 Juni'26' on the next line: the start became 20 Feb 2027, so a past exhibition looked
    current and Ho Tzu Nyen went into the calendar for 2027."""
    from automation_desk.groups.calendar.web_page import read_dates

    today = date(2026, 10, 4)
    assert read_dates("20 Feb. →\n14 Juni'26", today) == [date(2026, 2, 20), date(2026, 6, 14)]
    assert read_dates("17 Apr. →\n29 Aug.'27", today) == [date(2027, 4, 17), date(2027, 8, 29)]
