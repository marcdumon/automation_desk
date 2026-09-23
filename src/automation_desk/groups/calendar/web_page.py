"""Read events from an agenda web page. Every date comes from code, never from the model.

Order of preference per page:
1. Structured data: schema.org Event JSON-LD and linked iCalendar (.ics) files, parsed entirely in code.
2. Plain agenda text: code finds every date and time on the page (Dutch, French, English), numbers them and marks them in
   the text as [D3: 12 okt] / [T5: 20u]. The model lists the events and refers to those numbers; code turns them into dates.
"""

import contextvars
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from time import monotonic
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag
from icalendar import Calendar
from pydantic import BaseModel, Field

from automation_desk.capture import read_in_browser
from automation_desk.jobs import record_fetch
from automation_desk.llm import ask

MAX_TEXT_CHARS = 200_000
# CLAUDE> event pages are read to fill in info and place; a few at a time, and not endlessly
DETAIL_WORKERS = 4
MAX_DETAIL_PAGES = 60
# CLAUDE> how much of an event page the model sees when it has to find the place
PLACE_TEXT_CHARS = 8_000
# CLAUDE> one model call per chunk keeps each reply well under the output limit on long agendas
CHUNK_CHARS = 20_000
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'

MONTHS = {
    # CLAUDE> English
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3, 'april': 4, 'apr': 4, 'may': 5, 'june': 6,
    'jun': 6, 'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9, 'october': 10, 'oct': 10,
    'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
    # CLAUDE> Dutch
    'januari': 1, 'februari': 2, 'maart': 3, 'mrt': 3, 'mei': 5, 'juni': 6, 'juli': 7, 'augustus': 8, 'oktober': 10,
    'okt': 10,
    # CLAUDE> French
    'janvier': 1, 'janv': 1, 'février': 2, 'fevrier': 2, 'févr': 2, 'fevr': 2, 'mars': 3, 'avril': 4, 'avr': 4, 'mai': 5,
    'juin': 6, 'juillet': 7, 'juil': 7, 'août': 8, 'aout': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11,
    'décembre': 12, 'decembre': 12, 'déc': 12,
}
_MONTH = '(' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + r')\.?'
_ORD = r'(?:st|nd|rd|th|er|e|ste|de)?'
# CLAUDE> '23 sep 26' has a two-digit year, but in '12 okt 20:00' or '12 okt 20 uur' the number is a time
_YEAR = r"(?:,?[ \t]+(\d{4}|'?\d{2}(?![\d:]|\.\d|[ \t]*(?:u|uur|h|am|pm)\b))|[ \t]*('\d{2})(?!\d))?"
_RANGE_WORD = r"(?:[-\u2013\u2014]|t/m|tot(?:[ \t]+en[ \t]+met)?|to|until|through|au|jusqu'au|jusqu\u2019au)"
_LIST_WORD = r'(?:,|&|\+|en|and|et)'
# CLAUDE> weekday names (nl, en, fr; full or short) that may stand before a day number inside a list or range
_WEEKDAY = (r'(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag|ma|di|wo|do|vr|za|zo|monday|tuesday|wednesday|'
            r'thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thurs|fri|sat|sun|lundi|mardi|mercredi|jeudi|vendredi|'
            r'samedi|dimanche|lun|mar|mer|jeu|ven|sam|dim)\.?')
# CLAUDE> a number followed by ':', '.', 'u', 'uur' or 'h' is a time ('oktober 12, 20:00'), never a further day
_NEXT_DAY = rf'[ \t]*(?:{_RANGE_WORD}|{_LIST_WORD})[ \t]*(?:{_WEEKDAY}[ \t]+)?\d{{1,2}}{_ORD}(?![:.]\d|[ \t]*(?:u|uur|h)\b)'
DATE_PATTERNS = [
    # CLAUDE> several days sharing one month: '12 - 20 oktober', 'zaterdag 10 en zondag 11 oktober 2026', '3, 10 en 17 okt'
    ('days_month', re.compile(rf'\b\d{{1,2}}{_ORD}(?:{_NEXT_DAY})+[ \t]+{_MONTH}{_YEAR}\b', re.IGNORECASE)),
    # CLAUDE> the same, month first: 'October 10 and 11, 2026'
    ('month_days', re.compile(rf'\b{_MONTH}[ \t]+\d{{1,2}}{_ORD}(?:{_NEXT_DAY})+{_YEAR}\b', re.IGNORECASE)),
    ('dmy', re.compile(rf'\b(\d{{1,2}}){_ORD}[ \t]+{_MONTH}{_YEAR}\b', re.IGNORECASE)),
    ('mdy', re.compile(rf'\b{_MONTH}[ \t]+(\d{{1,2}}){_ORD}{_YEAR}\b', re.IGNORECASE)),
    ('iso', re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')),
    ('num', re.compile(r'\b(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})\b')),
]
# CLAUDE> every pattern captures (hour, minute, suffix); '12.50' counts only with a suffix, so prices are not times
TIME_PATTERNS = [
    re.compile(r'\b(\d{1,2})[:hu](\d{2})[ \t]*(am|pm|uur)?\b', re.IGNORECASE),
    re.compile(r'\b(\d{1,2})\.(\d{2})[ \t]*(uur|u|h)\b', re.IGNORECASE),
    re.compile(r'\b(\d{1,2})()[ \t]*(am|pm|h|u|uur)\b', re.IGNORECASE),
]
# CLAUDE> a date right after these words is an END date of something already running ('Now → 7 Mar', 'tot 7 maart')
UNTIL_BEFORE = re.compile(r"(?:\bnow|\bnu|\bmaintenant|\buntil|\btill|\bthrough|\bthru|\bends|\bcloses|\btot|\bt/m|"
                          r"\bt\.e\.m\.?|jusqu.au)[^\w\d]{0,6}$",
                          re.IGNORECASE)
# CLAUDE> answers that mean 'not for programs' rather than 'not here': worth a try in the browser
BLOCKED_STATUS = frozenset({401, 403, 429, 503})
NEXT_WORDS = {'next', 'next page', 'volgende', 'volgende pagina', 'suivant', 'page suivante', '\u00bb', '\u203a', '>', '\u2192'}


@dataclass(frozen=True)
class WebEvent:
    """One event read from a page, with dates resolved in code."""

    title: str
    start: date
    start_time: time | None
    end: date | None
    end_time: time | None
    location: str
    description: str
    url: str
    matches_filter: bool = True
    end_text: str = ''


@dataclass(frozen=True)
class Page:
    """A fetched page."""

    url: str
    html: str
    via: str = 'download'


def fetch(url: str, http: httpx.Client, use_browser: bool = False) -> Page:
    """A page: downloaded when the site allows it, otherwise loaded in the browser."""
    if not use_browser:
        response = _get(url, http)
        blocked = response.headers.get('cf-mitigated') == 'challenge' or response.status_code in BLOCKED_STATUS
        if not blocked:
            response.raise_for_status()
            return Page(url=str(response.url), html=response.text)
    return browser_page(url)


def browser_page(url: str) -> Page:
    """Read a page through the user's own browser, recorded on the current job."""
    captured, elapsed = read_in_browser(url)
    via = 'your browser, after you completed the site check' if captured.asked_you else 'your browser'
    record_fetch(url, 0, len(captured.html.encode()), elapsed, via=via)
    return Page(url=captured.url, html=captured.html, via='browser')


def _get(url: str, http: httpx.Client) -> httpx.Response:
    """GET with a browser user agent, recorded on the current job."""
    started = monotonic()
    response = http.get(url, headers={'User-Agent': USER_AGENT}, follow_redirects=True)
    record_fetch(url, response.status_code, len(response.content), int((monotonic() - started) * 1000))
    return response


def infer_year(month: int, day: int, today: date) -> date:
    """A day and month without year: this year, or next year when that is more than half a year ago."""
    candidate = date(today.year, month, day)
    return date(today.year + 1, month, day) if candidate < today - timedelta(days=183) else candidate


def _make_date(year: str | None, month: int, day: int, today: date) -> date | None:
    """Build a date from parts, inferring a missing year; None when the parts are not a real date."""
    try:
        y = int(year.lstrip("'")) if year else 0
        y = y + 2000 if 0 < y < 100 else y
        if today.year - 1 <= y <= today.year + 3:
            return date(y, month, day)
        return infer_year(month, day, today)
    except ValueError:
        return None


# CLAUDE> ---------------------------------------------------------------- structured data


def _plain(value: object) -> str:
    """Text of a JSON-LD string that may hold HTML markup or entities."""
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', str(value or ''))).split())


def _parse_iso(value: str, tz: ZoneInfo) -> tuple[date, time | None]:
    """A schema.org date or datetime, converted to the user's timezone when it carries an offset."""
    if 'T' not in value:
        return date.fromisoformat(value[:10]), None
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz)
    return moment.date(), moment.time().replace(second=0, microsecond=0)


def _place(location: object) -> str:
    """A readable place from a schema.org location (string, Place or list)."""
    if isinstance(location, list):
        return ', '.join(filter(None, (_place(item) for item in location)))
    if isinstance(location, str):
        return location
    if not isinstance(location, dict):
        return ''
    address = location.get('address')
    if isinstance(address, dict):
        address = ', '.join(str(address[k]) for k in ('streetAddress', 'postalCode', 'addressLocality') if address.get(k))
    return ', '.join(str(part) for part in (location.get('name'), address) if part)


def _event_place(event: dict) -> str:
    """Where an event is: the organising venue when it has an address, then the room or place and its address."""
    place = _place(event.get('location'))
    organizer = event.get('organizer')
    if isinstance(organizer, dict) and organizer.get('address') and organizer.get('name'):
        name = _plain(organizer['name'])
        if name and name not in place:
            place = ', '.join(filter(None, (name, place)))
    return place


def _walk_jsonld(node: object) -> list[dict]:
    """Every object whose @type is an Event (or a subtype such as ExhibitionEvent), at any depth."""
    if isinstance(node, list):
        return [event for item in node for event in _walk_jsonld(item)]
    if not isinstance(node, dict):
        return []
    types = node.get('@type', [])
    types = [types] if isinstance(types, str) else types
    found = [node] if any(str(t).endswith('Event') for t in types) and node.get('startDate') else []
    return found + [event for key, value in node.items() if key != 'location' for event in _walk_jsonld(value)]


def jsonld_events(soup: BeautifulSoup, page_url: str, tz: ZoneInfo) -> list[WebEvent]:
    """schema.org Event objects embedded as JSON-LD."""
    events = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
        except json.JSONDecodeError:
            continue
        for item in _walk_jsonld(data):
            try:
                start, start_time = _parse_iso(str(item['startDate']), tz)
                end, end_time = _parse_iso(str(item['endDate']), tz) if item.get('endDate') else (None, None)
            except ValueError:
                continue
            events.append(WebEvent(
                title=_plain(item.get('name')) or '(untitled)',
                start=start, start_time=start_time, end=end, end_time=end_time, location=_event_place(item),
                description=_plain(item.get('description'))[:1000],
                url=urljoin(page_url, str(item.get('url') or page_url))))
    return events


def ics_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Links to iCalendar files on the page."""
    links = [a['href'] for a in soup.find_all('a', href=True) if re.search(r'\.ics(\?|$)|^webcal:', a['href'], re.I)]
    links += [el['href'] for el in soup.find_all('link', type='text/calendar', href=True)]
    return list(dict.fromkeys(urljoin(page_url, link.replace('webcal://', 'https://', 1)) for link in links))


def _ics_moment(component: object, key: str, tz: ZoneInfo) -> tuple[date | None, time | None]:
    """Date and optional time of a DTSTART/DTEND property, in the user's timezone."""
    prop = component.get(key)
    if prop is None:
        return None, None
    value = prop.dt
    if isinstance(value, datetime):
        value = value.astimezone(tz) if value.tzinfo else value
        return value.date(), value.time().replace(second=0, microsecond=0)
    return value, None


def ics_events(content: bytes, source_url: str, tz: ZoneInfo) -> list[WebEvent]:
    """VEVENTs of an iCalendar file; recurring events contribute their first occurrence."""
    events = []
    for component in Calendar.from_ical(content).walk('VEVENT'):
        start, start_time = _ics_moment(component, 'DTSTART', tz)
        if start is None:
            continue
        end, end_time = _ics_moment(component, 'DTEND', tz)
        if end is not None and end_time is None:
            # CLAUDE> iCalendar all-day DTEND is exclusive; WebEvent.end is the last day
            end -= timedelta(days=1)
        events.append(WebEvent(title=str(component.get('SUMMARY', '(untitled)')), start=start, start_time=start_time,
                               end=end, end_time=end_time, location=str(component.get('LOCATION', '')),
                               description=str(component.get('DESCRIPTION', ''))[:1000],
                               url=str(component.get('URL', source_url))))
    return events


# CLAUDE> ---------------------------------------------------------------- plain agenda text


@dataclass(frozen=True)
class Spans:
    """Dates, times and links found in a page's text, numbered as the model sees them."""

    dates: dict[int, tuple[date, time | None]]
    times: dict[int, time]
    links: dict[int, str]
    text: str
    until: frozenset[int] = frozenset()
    date_links: dict[int, int] = field(default_factory=dict)


def _date_matches(text: str, today: date) -> list[tuple[int, int, list[tuple[date, time | None]], str]]:
    """Non-overlapping date matches: (start, end, dates, style), earlier and longer patterns win.

    Style is 'single', 'range' (two days joined by a range word: 12 - 20 oktober) or 'list' (separate days).
    """
    found = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in DATE_PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            g = m.groups()
            style = 'single'
            # CLAUDE> the two trailing groups are the alternative year forms ('2026' / '26' vs '.'27')
            year = g[-2] or g[-1] if kind in ('days_month', 'month_days', 'dmy', 'mdy') else None
            if kind in ('days_month', 'month_days'):
                month_text = g[0]
                month_start = m.start(1)
                # CLAUDE> the day numbers are the digits outside the month and year; weekday words hold no digits
                numbers_part = text[m.start():month_start] if kind == 'days_month' else text[m.end(1):m.end()]
                if kind == 'month_days' and (g[-2] or g[-1]):
                    numbers_part = numbers_part[:numbers_part.rfind(g[-2] or g[-1])]
                numbers = [int(n) for n in re.findall(r'\d{1,2}', numbers_part)]
                days = [_make_date(year, MONTHS[month_text.lower()], n, today) for n in numbers]
                is_range = len(numbers) == 2 and re.search(_RANGE_WORD, re.sub(r'\d', ' ', numbers_part), re.IGNORECASE)
                style = 'range' if is_range else 'list'
            elif kind == 'dmy':
                days = [_make_date(year, MONTHS[g[1].lower()], int(g[0]), today)]
            elif kind == 'mdy':
                days = [_make_date(year, MONTHS[g[0].lower()], int(g[1]), today)]
            elif kind == 'iso':
                days = [_make_date(g[0], int(g[1]), int(g[2]), today)]
            else:
                days = [_make_date(g[2], int(g[1]), int(g[0]), today)]
            if days and all(days):
                taken.append((m.start(), m.end()))
                found.append((m.start(), m.end(), [(d, None) for d in days if d], style))
    return found


def _time_matches(text: str, busy: list[tuple[int, int]]) -> list[tuple[int, int, time]]:
    """Clock times outside the date matches."""
    found = []
    for pattern in TIME_PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in busy):
                continue
            hour_text, minute_text, suffix = m.groups()
            hour, minute, suffix = int(hour_text), int(minute_text or 0), (suffix or '').lower()
            if suffix == 'pm' and hour < 12:
                hour += 12
            if suffix == 'am' and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                continue
            busy.append((m.start(), m.end()))
            found.append((m.start(), m.end(), time(hour, minute)))
    return found


def marked_text(soup: BeautifulSoup, page_url: str, today: date, tz: ZoneInfo) -> Spans:
    """The page's visible text with every date, time and link numbered for the model."""
    for tag in soup(['script', 'style', 'noscript', 'svg', 'template', 'iframe']):
        tag.decompose()

    links: dict[int, str] = {}
    for a in soup.find_all('a', href=True):
        href = urljoin(page_url, a['href'])
        if href.startswith('http') and a.get_text(strip=True):
            links[len(links) + 1] = href
            # CLAUDE> start and end markers let code see which dates sit inside which link; 'L' keeps digits off word boundaries
            a.insert(0, f'\x01L{len(links)}\x01')
            a.append(f'\x02L{len(links)}\x02')

    # CLAUDE> <time datetime="..."> is exact; swap it for a placeholder so the regexes below skip its text
    exact: dict[str, tuple[date, time | None, str]] = {}
    for element in soup.find_all('time', attrs={'datetime': True}):
        try:
            day, clock = _parse_iso(element['datetime'], tz)
        except ValueError:
            continue
        placeholder = f'\x00{len(exact)}\x00'
        exact[placeholder] = (day, clock, element.get_text(' ', strip=True) or element['datetime'])
        element.replace_with(placeholder)

    text = re.sub(r'\n\s*\n+', '\n', soup.get_text('\n'))
    text = '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())

    date_hits = _date_matches(text, today)
    time_hits = _time_matches(text, [(s, e) for s, e, _, _ in date_hits])
    dates: dict[int, tuple[date, time | None]] = {}
    times: dict[int, time] = {}
    until: set[int] = set()
    pieces, cursor = [], 0
    marks = [(s, e, ('d', (v, style))) for s, e, v, style in date_hits] + [(s, e, ('t', v)) for s, e, v in time_hits]
    for start, end, value in sorted(marks, key=lambda mark: mark[0]):
        pieces.append(text[cursor:start])
        original = text[start:end]
        kind, payload = value
        if kind == 'd':
            payload, style = payload
            ids = []
            for day in payload:
                dates[len(dates) + 1] = day
                ids.append(f'D{len(dates)}')
            if len(payload) == 1 and UNTIL_BEFORE.search(text[max(0, start - 20):start]):
                until.add(len(dates))
            joined = f'{ids[0]} to {ids[1]}' if style == 'range' else ', '.join(ids)
            pieces.append(f'[{joined}: {original}]')
        else:
            times[len(times) + 1] = payload
            pieces.append(f'[T{len(times)}: {original}]')
        cursor = end
    pieces.append(text[cursor:])
    text = ''.join(pieces)

    for placeholder, (day, clock, original) in exact.items():
        dates[len(dates) + 1] = (day, clock)
        text = text.replace(placeholder, f'[D{len(dates)}: {original}]')
    text, date_links = _resolve_link_markers(text)
    return Spans(dates=dates, times=times, links=links, text=text[:MAX_TEXT_CHARS], until=frozenset(until),
                 date_links=date_links)


_LINK_MARKER = re.compile(r'\x01L(\d+)\x01|\x02L(\d+)\x02|\[D(\d+)')


def _resolve_link_markers(text: str) -> tuple[str, dict[int, int]]:
    """Which link each date sits inside, and the text with link starts shown as [Ln] and link ends removed."""
    date_links: dict[int, int] = {}
    open_links: list[int] = []
    for m in _LINK_MARKER.finditer(text):
        opened, closed, date_id = m.groups()
        if opened:
            open_links.append(int(opened))
        elif closed and int(closed) in open_links:
            open_links.remove(int(closed))
        elif date_id and open_links:
            date_links[int(date_id)] = open_links[-1]
    text = re.sub(r'\x01L(\d+)\x01', r'[L\1] ', text)
    return re.sub(r'\x02L\d+\x02', '', text), date_links


class FoundEvent(BaseModel):
    """One event as the model reports it: text copied from the page, dates only as span numbers."""

    title: str = Field(description='Event title, copied in the language of the page.')
    date_span: int = Field(description='Number n of the [Dn] marker holding the (first) day of the event, or its last day '
                                       'when running_until is true.')
    running_until: bool = Field(description="True when the page shows only the event's END date because it is already "
                                            "running: 'Now → 7 Mar', 'until 7 March', 'tot 7 maart', 't/m 7 maart', "
                                            "'jusqu'au 7 mars'. False otherwise.")
    end_date_span: int = Field(description='Number of the [Dn] marker of the last day for multi-day events, else -1.')
    time_span: int = Field(description='Number of the [Tn] marker of the start time, or -1 when none, '
                                       'unless the start time is inside the date marker itself.')
    end_time_span: int = Field(description='Number of the [Tn] marker of the end time, or -1.')
    location: str = Field(description='Venue and/or address as written on the page, or empty.')
    description: str = Field(description='One or two sentences of useful info from the page (price, artists...), '
                                         'in the language of the page. Empty when none.')
    end_text: str = Field(description="When the end is written only in words without a [Dn] marker ('Summer 2027', "
                                      "'ongoing', 'permanent'), copy those words. Else empty.")
    link_span: int = Field(description='Number of the [Ln] marker linking to the event details, or -1.')
    matches_filter: bool = Field(description='Whether the event satisfies the filter instruction; true when no filter.')


class Extraction(BaseModel):
    """All events on the page."""

    events: list[FoundEvent]


EXTRACT_SYSTEM = """You read the text of an agenda web page and list the events it announces.
Dates are marked [Dn: original text], a range as [Dn to Dm: text] and separate days sharing a month as
[Dn, Dm, Dk: text], times [Tn: text], links [Ln].
One event held on consecutive listed days ('zaterdag 10 en zondag 11 oktober') is ONE event: date_span = the first day,
end_date_span = the last. Separate dates of a series ('3, 10 en 17 oktober') are separate events.
Refer to dates and times ONLY by those numbers; never write a date or time yourself.
Skip navigation, opening hours of the venue, newsletter blocks and items that are not events.
List every event, also those that fail the filter instruction: mark those with matches_filter false, never leave them out.
Copy titles, locations and descriptions in the page's own language (Dutch, French or English)."""


def text_events(spans: Spans, text_filter: str, page_url: str, today: date,
                http: httpx.Client | None = None) -> list[WebEvent]:
    """Ask the model to list events by span number, then build them from the spans in code."""
    if not spans.dates:
        return []
    instruction = f'Filter instruction: {text_filter}' if text_filter else 'Filter instruction: none (all events match).'
    found = [item for chunk in chunks(spans.text, CHUNK_CHARS)
             for item in ask(EXTRACT_SYSTEM, f'{instruction}\n\nPage text:\n{chunk}', Extraction, http=http,
                             purpose='list events on page').events]
    events = []
    for item in found:
        if item.date_span not in spans.dates:
            continue
        start, start_time = spans.dates[item.date_span]
        end, end_time = spans.dates.get(item.end_date_span, (None, None))
        if (item.running_until or item.date_span in spans.until) and end in (None, start) and start >= today:
            # CLAUDE> an exhibition shown as 'until <date>' runs from today; 'now' is resolved here, not by the model
            start, start_time, end = today, None, start
        start_time = spans.times.get(item.time_span, start_time)
        end_time = spans.times.get(item.end_time_span, end_time)
        if end and end < start:
            start, end = fix_range(start, end, today)
        events.append(WebEvent(title=item.title.strip() or '(untitled)', start=start, start_time=start_time, end=end,
                               end_time=end_time, location=item.location.strip(), description=item.description.strip(),
                               url=spans.links.get(spans.date_links.get(item.date_span, item.link_span), page_url),
                               matches_filter=item.matches_filter,
                               end_text='' if end else item.end_text.strip()))
    return events


def chunks(text: str, size: int) -> list[str]:
    """Split text at line boundaries into pieces of about `size` characters. Span numbers stay page-wide."""
    pieces, current = [], ''
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > size:
            pieces.append(current)
            current = ''
        current += line
    return [*pieces, current] if current else pieces


def fix_range(start: date, end: date, today: date) -> tuple[date, date]:
    """A range ending before it starts had a year inferred wrongly on one side ('20 dec - 5 jan', '12 mei - 20 okt').

    Of the two repairs, prefer one that is running today, else the one starting soonest.
    """
    options = [(start, replace_year(end, end.year + 1)), (replace_year(start, start.year - 1), end)]
    return min(options, key=lambda o: (o[1] < today, max((o[0] - today).days, 0)))


def replace_year(day: date, year: int) -> date:
    """The same day and month in another year (29 Feb becomes 28 Feb)."""
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


class FilterVerdicts(BaseModel):
    """Which numbered events satisfy the filter."""

    matching: list[int] = Field(description='Numbers of the events that satisfy the filter instruction.')


def apply_text_filter(events: list[WebEvent], text_filter: str, http: httpx.Client | None = None) -> list[WebEvent]:
    """For structured events: let the model judge only the free-text filter, from titles and descriptions."""
    if not text_filter or not events:
        return events
    listing = '\n'.join(f'{n}. {e.title} | {e.location} | {e.description[:200]}' for n, e in enumerate(events, 1))
    verdict = ask('You decide which events satisfy a filter instruction. Answer with the numbers of the matching events.',
                  f'Filter instruction: {text_filter}\n\nEvents:\n{listing}', FilterVerdicts, http=http,
                  purpose='apply text filter')
    keep = set(verdict.matching)
    return [replace(e, matches_filter=n in keep) for n, e in enumerate(events, 1)]


def next_page(soup: BeautifulSoup, page_url: str) -> str | None:
    """The pagination link to the next page on the same site, if any."""
    host = urlparse(page_url).netloc
    candidates = [el for el in soup.find_all(['a', 'link'], href=True) if 'next' in (el.get('rel') or [])]
    candidates += [a for a in soup.find_all('a', href=True)
                   if isinstance(a, Tag) and (a.get_text(' ', strip=True).lower() in NEXT_WORDS
                                              or (a.get('aria-label') or '').lower() in NEXT_WORDS)]
    for element in candidates:
        href = urljoin(page_url, element['href'])
        if urlparse(href).netloc == host and href.split('#')[0] != page_url.split('#')[0]:
            return href
    return None


def read_events(url: str, today: date, tz: ZoneInfo, text_filter: str, max_pages: int,
                http: httpx.Client) -> tuple[list[WebEvent], list[str], str]:
    """Events from the page and, when max_pages > 1, its following pages, completed from each event's own page.

    Returns events, notes for the preview and the first page's HTML (to find who runs the site). A page that is
    downloaded but shows no events and no dates probably builds its agenda with JavaScript, so it is read again through
    the browser; once a site needed the browser, its other pages are read that way too.
    """
    events: list[WebEvent] = []
    notes: list[str] = []
    first_html = ''
    seen: set[str] = set()
    current: str | None = url
    use_browser = False
    while current and current not in seen and len(seen) < max_pages:
        seen.add(current)
        page = fetch(current, http, use_browser)
        found, source = _page_events(page, today, tz, text_filter, http)
        if found is None and page.via == 'download':
            page = browser_page(current)
            found, source = _page_events(page, today, tz, text_filter, http)
        use_browser = page.via == 'browser'
        first_html = first_html or page.html
        found = found or []
        how = 'read in your browser' if use_browser else 'downloaded'
        notes.append(f'{page.url} ({how}): {len(found)} event(s) from {source}.')
        events += found
        current = next_page(BeautifulSoup(page.html, 'lxml'), page.url) if max_pages > 1 else None
    events, detail_notes = complete_from_event_pages(events, seen, today, tz, http, use_browser)
    return events, notes + detail_notes, first_html


@dataclass(frozen=True)
class EventPage:
    """What an event's own page adds: full description, place and, from its event data, exact dates."""

    description: str
    location: str
    structured: WebEvent | None
    text: str = ''


def _norm(text: str) -> str:
    """Whitespace collapsed to single spaces."""
    return ' '.join(text.split())


def _is_prose(text: str) -> bool:
    """Whether a summary reads as sentences rather than a keyword list."""
    return len(text) >= 60 and bool(re.search(r'[.!?…](\s|$)', text)) and text.count(',') < len(text.split()) / 3


def full_description(soup: BeautifulSoup, seed: str) -> str:
    """The event's description as the page shows it, paragraph by paragraph.

    First choice: the paragraph block containing the start of the page's own summary (`seed`, from its event data or
    meta description), when that summary is prose. Next: the largest block of paragraphs in the page's main content.
    Last: the summary itself.
    """
    seed_text = _norm(seed) if _is_prose(_norm(seed)) else ''
    root = soup.find('main') or soup.find('article') or soup.body or soup
    paragraphs = root.find_all('p')
    if seed_text:
        for paragraph in paragraphs:
            text = _norm(paragraph.get_text(' '))
            if len(text) >= 40 and text[:60] in seed_text and paragraph.parent is not None:
                return '\n\n'.join(t for t in (_norm(p.get_text(' ')) for p in paragraph.parent.find_all('p')) if t)
    blocks: dict[int, list[str]] = {}
    for paragraph in paragraphs:
        if paragraph.parent is not None and (text := _norm(paragraph.get_text(' '))):
            blocks.setdefault(id(paragraph.parent), []).append(text)
    candidates = [b for b in blocks.values() if 'cookie' not in ' '.join(b).lower()]
    best = max(candidates, key=lambda texts: sum(map(len, texts)), default=[])
    if sum(map(len, best)) >= 200:
        return '\n\n'.join(best)
    return seed_text


def read_event_page(url: str, http: httpx.Client, use_browser: bool, tz: ZoneInfo) -> EventPage:
    """Read one event page: code takes the description, place and dates; no model is asked here."""
    page = fetch(url, http, use_browser)
    soup = BeautifulSoup(page.html, 'lxml')
    structured = next(iter(jsonld_events(soup, page.url, tz)), None)
    meta = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'})
    raw_jsonld = [e for s in soup.find_all('script', type='application/ld+json') for e in _walk_jsonld(_json(s.string))]
    seed = _plain(raw_jsonld[0].get('description')) if raw_jsonld else ''
    seed = seed if _is_prose(seed) else (meta.get('content', '') if meta else '')
    for tag in soup(['script', 'style', 'noscript', 'nav', 'header', 'footer', 'template', 'svg']):
        tag.decompose()
    root = soup.find('main') or soup.find('article') or soup.body or soup
    return EventPage(description=full_description(soup, seed), location=structured.location if structured else '',
                     structured=structured, text=_norm(root.get_text(' '))[:PLACE_TEXT_CHARS])


class PlaceFound(BaseModel):
    """Where an event takes place, copied from its page."""

    place: str = Field(description='The venue and address where the event takes place, copied exactly as written on the '
                                   "page, parts separated by ', '. Empty when the page does not say.")


def _on_page(place: str, text: str) -> bool:
    """Whether every part of `place` literally appears in the page text: the model may copy, never invent."""
    haystack = text.casefold()
    parts = [p.strip() for p in re.split(r'[,·|\n]', place) if p.strip()]
    return bool(parts) and all(_norm(p).casefold() in haystack for p in parts)


def find_place(page: EventPage, http: httpx.Client | None = None) -> str:
    """The place written on an event page without event data, found by the model and checked by code."""
    if not page.text:
        return ''
    found = ask('You find where an event takes place on its web page. Copy the venue and address exactly as written; '
                'do not translate, complete or reformat them. Leave empty when the page does not say.',
                f'Page text:\n{page.text}', PlaceFound, http=http, purpose='find place on event page')
    place = _norm(found.place)
    return place if _on_page(place, page.text) else ''


def _json(text: str | None) -> object:
    """Parsed JSON, or None when it is not valid."""
    try:
        return json.loads(text or '')
    except json.JSONDecodeError:
        return None


def complete_from_event_pages(events: list[WebEvent], list_pages: set[str], today: date, tz: ZoneInfo,
                              http: httpx.Client, use_browser: bool) -> tuple[list[WebEvent], list[str]]:
    """Complete events from their own pages: the full description (the longer one wins), the place (the fuller of event
    data and list page, else found on the event page and checked by code) and exact dates when the page has event data.

    Only upcoming or running events that link to their own page on the same site.
    """
    hosts = {urlparse(page).netloc for page in list_pages}
    bases = {page.split('#')[0] for page in list_pages}
    wanted = list(dict.fromkeys(
        e.url for e in events
        if e.url and e.url.split('#')[0] not in bases and urlparse(e.url).netloc in hosts
        and (e.end or e.start) >= today))
    if not wanted:
        return events, []
    notes = []
    if len(wanted) > MAX_DETAIL_PAGES:
        notes.append(f'Only the first {MAX_DETAIL_PAGES} of {len(wanted)} event pages were read.')
        wanted = wanted[:MAX_DETAIL_PAGES]

    listed_place = {e.url: e.location for e in events if e.location}

    def read(url: str) -> EventPage | Exception:
        try:
            page = read_event_page(url, http, use_browser, tz)
            if not page.location and not listed_place.get(url):
                page = replace(page, location=find_place(page))
            return page
        except Exception as error:
            return error

    # CLAUDE> each worker runs in a copy of this context, so its page reads are recorded on the current job
    with ThreadPoolExecutor(DETAIL_WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, read, url) for url in wanted]
        pages = dict(zip(wanted, (f.result() for f in futures), strict=True))
    failed = [url for url, page in pages.items() if isinstance(page, Exception)]
    how = 'through your browser' if use_browser else 'downloaded'
    notes.append(f'Read {len(pages) - len(failed)} event page(s) ({how}) for info and place.')
    if failed:
        notes.append(f'Could not read {len(failed)} event page(s): ' + ', '.join(failed[:5]))

    completed = []
    for event in events:
        page = pages.get(event.url)
        if not isinstance(page, EventPage):
            completed.append(event)
            continue
        dates = {}
        if page.structured:
            s = page.structured
            dates = {'start': s.start, 'start_time': s.start_time, 'end': s.end, 'end_time': s.end_time, 'end_text': ''}
        description = max(page.description, event.description, key=len)
        # CLAUDE> event data can be thinner than the list ('Paris' vs 'Grand Palais, Paris'): the fuller one wins
        location = max(page.structured.location if page.structured else '', event.location, key=len) or page.location
        completed.append(replace(event, description=description, location=location, **dates))
    return completed, notes


def _page_events(page: Page, today: date, tz: ZoneInfo, text_filter: str,
                 http: httpx.Client) -> tuple[list[WebEvent] | None, str]:
    """Events on one page and where they came from; None when the page has no events and no dates at all."""
    soup = BeautifulSoup(page.html, 'lxml')
    found = jsonld_events(soup, page.url, tz)
    if found:
        return apply_text_filter(found, text_filter, http), 'schema.org event data'
    for link in ics_links(soup, page.url)[:3]:
        response = _get(link, http)
        if response.status_code == 200 and b'BEGIN:VCALENDAR' in response.content[:2000]:
            found += ics_events(response.content, page.url, tz)
    if found:
        return apply_text_filter(found, text_filter, http), 'iCalendar file'
    spans = marked_text(BeautifulSoup(page.html, 'lxml'), page.url, today, tz)
    if not spans.dates:
        return None, 'nothing: no dates on the page'
    return text_events(spans, text_filter, page.url, today, http), 'page text (dates found by code, events listed by the model)'
