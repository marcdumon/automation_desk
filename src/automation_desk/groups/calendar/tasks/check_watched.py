"""Standard task: check the watched agenda sites and add the new events the user ticks.

Every site is read with the same import as 'Add events'; the preview shows what is not in the calendar and not in the past.
Events left unticked are remembered as declined: later checks list them again, but unticked.
"""

import re
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from googleapiclient.discovery import Resource

from automation_desk import ledger, stop
from automation_desk.capture import CaptureError, NeedsPerson
from automation_desk.groups.base import Context, Preview, PreviewGroup, StandardTask, TaskArgs, UserError, match_name
from automation_desk.groups.calendar import watch
from automation_desk.groups.calendar.client import all_events, writable_calendars
from automation_desk.groups.calendar.organisers import organiser_for
from automation_desk.groups.calendar.tasks.add_events_from_web import (
    ALL_DAY,
    DEFAULT_DURATION,
    EDITABLE,
    PAGE_TAG,
    URL,
    AddEventsFromWeb,
    page_tag,
    parse_duration,
)
from automation_desk.groups.calendar.web_page import BROWSER_MAY_ASK, download, read_events
from automation_desk.llm import LLMError

SOURCE_LINK = re.compile(r'Source: (https?://\S+)')
EVENT_LINK = re.compile(r'https?://[^\s"\'<>]+')
# CLAUDE> what agenda pages are called, in the languages of the user's sites
AGENDA_WORD = re.compile(r'agenda|calendar|kalender|calendrier|program|events?$|evenementen|activit|exhibitions|expositions'
                         r'|tentoonstellingen|whats-on', re.I)


def _parents(link: str) -> list[str]:
    """The event's own page and the pages above it, nearest first, with and without www, as https and http."""
    parts = urlsplit(link)
    host = parts.netloc.removeprefix('www.')
    segments = [s for s in parts.path.split('/') if s]
    return [f'{scheme}://{h}/{"/".join(segments[:n])}'.rstrip('/')
            for n in range(len(segments), -1, -1)
            for scheme in dict.fromkeys((parts.scheme, 'https', 'http')) for h in (host, f'www.{host}')]


def sites_from_events(svc: Resource, http: httpx.Client | None = None) -> tuple[list[tuple[str, str]], int]:
    """The agenda pages the events came from, with the calendar they are in, and how many imports have no web page.

    Every import stores its page's fingerprint. The page is found among the addresses of earlier import commands (exact,
    also for pages that are no parent of their events: MuHKA's /en/programma/ lists /en/exhibitions/...), else among the
    event's own link and its parents. PDFs, mails and typed events have no page. Events added by hand only link to their
    own page: with `http`, the nearest parent page that links to one of them is their agenda.
    """
    stop.begin('watch-from-events')
    commanded = {page_tag(url): url for url in _commanded_pages()}
    event_calendars = {watch.default_calendar().casefold()} | {s.calendar.casefold() for s in watch.sites() if s.calendar}
    tags: dict[str, tuple[str, list[str]]] = {}
    by_hand: list[tuple[str, dict]] = []
    for calendar in writable_calendars(svc):
        for event in all_events(svc, calendar['id']):
            tag = event.get('extendedProperties', {}).get('private', {}).get(PAGE_TAG)
            if tag:
                link = SOURCE_LINK.search(event.get('description', ''))
                tags.setdefault(tag, (calendar['summary'], []))[1].extend([link.group(1)] if link else [])
                event_calendars.add(calendar['summary'].casefold())
            else:
                by_hand.append((calendar['summary'], event))
    found, unmatched = [], 0
    for tag, (calendar, links) in tags.items():
        page = commanded.get(tag) or next((p for link in links for p in _parents(link) if page_tag(p) == tag), None)
        # CLAUDE> a PDF in a mail links to the mail, which is no agenda to watch
        if page is None or urlsplit(page).netloc == 'mail.google.com':
            unmatched += 1
        elif page not in (f for f, _ in found):
            found.append((page, calendar))
    if http is not None:
        # CLAUDE> only calendars that hold events: the main calendar's meeting links and shared documents are no agendas
        hosts = _hand_added_links([(c, e) for c, e in by_hand if c.casefold() in event_calendars])
        for host, (calendar, links) in hosts.items():
            # CLAUDE> the user pressed Stop: the sites found so far are kept
            if stop.requested('watch-from-events'):
                break
            if any(_host(f) == host for f, _ in found):
                continue
            if page := _agenda_page(links, http):
                found.append((page, calendar))
    return found, unmatched


def _host(url: str) -> str:
    """A site's host without www."""
    return urlsplit(url).netloc.casefold().removeprefix('www.')


def _same_page(url: str) -> str:
    """A page address for comparing: host without www and path without trailing slash."""
    return f'{_host(url)}{urlsplit(url).path.rstrip("/")}'


def _hand_added_links(events: list[tuple[str, dict]]) -> dict[str, tuple[str, list[str]]]:
    """The web pages of events added by hand, by site, with the calendar of the site's first event."""
    hosts: dict[str, tuple[str, list[str]]] = {}
    for calendar, event in events:
        text = f'{event.get("description", "")} {event.get("location", "")}'
        for link in dict.fromkeys(m.group(0).rstrip('.,;:!?)') for m in EVENT_LINK.finditer(text)):
            host = _host(link)
            if host and not host.endswith(('google.com', 'zoom.us')):
                links = hosts.setdefault(host, (calendar, []))[1]
                if link not in links:
                    links.append(link)
    return hosts


def _agenda_page(links: list[str], http: httpx.Client) -> str | None:
    """The page listing these events: the nearest page above them that links to one of them, or, when that page (or the
    only link) is a home page rather than an agenda, the agenda it links to. None when there is none."""
    pages = [link for link in links if urlsplit(link).path.strip('/')]
    homes = [link.rstrip('/') for link in links if not urlsplit(link).path.strip('/')]
    wanted = {_same_page(link) for link in pages}
    read: dict[str, list[str]] = {}
    found = next((page for page in _parents_of(pages) if _same_page(page) not in wanted
                  and any(_same_page(a) in wanted for a in _anchors(page, http, read))), None) or next(iter(homes), None)
    if found is None or AGENDA_WORD.search(urlsplit(found).path.rsplit('/', 1)[-1]):
        return found
    anchors = _anchors(found, http, read)
    agendas = [a for a in dict.fromkeys(a.split('#')[0].rstrip('/') for a in anchors)
               if _host(a) == _host(found) and AGENDA_WORD.search(urlsplit(a).path.rstrip('/').rsplit('/', 1)[-1])]
    # CLAUDE> of several agendas, the one holding the events and shows the page links to
    best = max(agendas, key=lambda a: sum(_same_page(x).startswith(f'{_same_page(a)}/') for x in anchors), default=None)
    return best or (found if pages else None)


def _parents_of(links: list[str]) -> list[str]:
    """The pages above these links, deepest first, each once."""
    parents: dict[str, None] = {}
    for link in links:
        parts = urlsplit(link)
        segments = [s for s in parts.path.split('/') if s]
        for n in range(len(segments) - 1, -1, -1):
            parents[f'{parts.scheme}://{parts.netloc}/{"/".join(segments[:n])}'.rstrip('/')] = None
    return sorted(parents, key=lambda page: -urlsplit(page).path.count('/'))


def _anchors(page: str, http: httpx.Client, read: dict[str, list[str]]) -> list[str]:
    """The addresses a page links to (none if it cannot be read), each page read once."""
    if page not in read:
        try:
            response = download(page, http)
            html = response.text if response.status_code == 200 else ''
        except httpx.HTTPError:
            html = ''
        read[page] = [urljoin(f'{page}/', a['href']) for a in BeautifulSoup(html, 'html.parser').find_all('a', href=True)]
    return read[page]


def _commanded_pages() -> list[str]:
    """The web addresses the user imported events from, taken from their Calendar commands in the job history."""
    pages = []
    for job in ledger.summaries('calendar'):
        if found := URL.search(job['sentence']):
            url = found.group(0).rstrip('.,;:!?)')
            pages.append((url if url.lower().startswith('http') else f'https://{url}').rstrip('/'))
    return pages


def _title_keys(title: str) -> set[str]:
    """A title as letters and digits only, whole and after the organiser ('MHKA: The Situation is Fluid')."""
    keys = {re.sub(r'[\W_]+', '', part).casefold() for part in (title, title.partition(':')[2])}
    return {key for key in keys if len(key) >= 6}


def _page(url: str) -> str:
    """An address for comparing: host without www, path without trailing slash, no fragment."""
    parts = urlsplit(url.casefold())
    return f'{parts.netloc.removeprefix("www.")}{parts.path.rstrip("/")}'


def _hand_added(svc: Resource, calendar_id: str) -> list[dict]:
    """The events the user put in a calendar without importing them: their first day, title and text."""
    return [{'day': (event.get('start', {}).get('date') or event.get('start', {}).get('dateTime', ''))[:10],
             'titles': sorted(_title_keys(event.get('summary', ''))),
             'text': f'{event.get("description", "")} {event.get("location", "")}'.casefold()}
            for event in all_events(svc, calendar_id)
            if PAGE_TAG not in event.get('extendedProperties', {}).get('private', {})]


def _already_there(body: dict, link: str, page_url: str, known: list[dict]) -> bool:
    """Whether the user typed this event in already: the same first day and title, or a link to its own page."""
    day = (body['start'].get('date') or body['start'].get('dateTime', ''))[:10]
    titles = _title_keys(body['summary'])
    own = _page(link) if link.startswith('http') and _page(link) != _page(page_url) else ''
    return any((k['day'] == day and any(a in b or b in a for a in titles for b in k['titles'])) or (own and own in k['text'])
               for k in known)


class WatchArgs(TaskArgs):
    """Nothing to fill: every watched site is checked."""


def _label(site: str) -> str:
    """A site as people write it."""
    return site.removeprefix('https://').removeprefix('http://').removeprefix('www.')


class CheckWatchedSites(StandardTask):
    """Check the watched agenda sites for new events."""

    id = 'check_watched_sites'
    name = 'Check watched agenda sites'
    description = ('Reads the agenda sites you watch (listed on the Calendar page) and shows the events not in your calendar, to '
                   'add the ones you tick; the ones you leave unticked come back unticked at the next check.')
    example = 'check my agenda sites'
    Args = WatchArgs
    declines_unticked = True

    def resolve(self, args: WatchArgs, ctx: Context) -> tuple[Preview, dict]:
        """Check every watched site."""
        return self.check(ctx)

    def check(self, ctx: Context, via_browser: bool = False, only: set[int] | None = None) -> tuple[Preview, dict]:
        """Read the sites (all, or `only` these) and compose the new events. Pages that need the browser are read in
        background tabs; only with `via_browser` may a site's human check come forward, else the site is listed on the
        Calendar page for Read via browser."""
        chosen = [s for s in watch.sites() if only is None or s.id in only]
        if not chosen:
            raise UserError('Add agenda sites to watch first, in Watched agenda sites on the Calendar page.')
        svc = ctx.google('calendar', 'v3')
        calendars = writable_calendars(svc)
        found, report = [], []
        stop.begin('watch-check')
        token = BROWSER_MAY_ASK.set(via_browser)
        in_browser = watch.browser_sites()
        watch.start_progress([_label(s.site) for s in chosen])
        try:
            with httpx.Client(timeout=60.0) as http:
                for site in chosen:
                    label = _label(site.site)
                    report.append({'name': label, 'calendar': watch.calendar_for(site), 'note': ''})
                    # CLAUDE> the user pressed Stop: the preview shows the sites read so far
                    if stop.requested('watch-check'):
                        report[-1]['note'] = 'not checked: you stopped the check'
                        watch.site_progress(label, 'waiting', 'not checked')
                        continue
                    watch.site_progress(label, 'reading')
                    try:
                        calendar = match_name(watch.calendar_for(site), calendars, 'summary', 'calendar')
                        # CLAUDE> a site read through the browser before shows its agenda only there; its download misleads
                        events, notes, html = read_events(site.site, ctx.today, ctx.tz, '', 1, http,
                                                          via_browser or site.id in in_browser)
                        organiser = organiser_for(site.site, html, http)
                    except NeedsPerson:
                        watch.set_result(site.id, watch.NEEDS_BROWSER)
                        watch.site_progress(label, 'browser', 'needs your browser')
                        report[-1]['note'] = 'needs your browser: press Read via browser above'
                        continue
                    except (UserError, httpx.HTTPError, CaptureError) as error:
                        watch.set_result(site.id, str(error))
                        watch.site_progress(label, 'failed', 'could not be read')
                        report[-1]['note'] = f'could not be read: {error}'
                        continue
                    except LLMError:
                        watch.set_result(site.id, 'the language model gave no usable answer')
                        watch.site_progress(label, 'failed', 'could not be read')
                        report[-1]['note'] = 'could not be read: the language model gave no usable answer; check again later'
                        continue
                    if via_browser or any('read in your browser' in note for note in notes):
                        watch.mark_browser(site.id)
                    watch.site_progress(label, 'done', f'{len(events)} event{"" if len(events) == 1 else "s"} on the page')
                    report[-1]['calendar'] = calendar['summary']
                    found.append((site.id, {'url': site.site, 'label': label, 'calendar_id': calendar['id'],
                                            'calendar': calendar['summary'], 'page_tag': page_tag(site.site), 'events': events,
                                            'notes': [], 'excluded': set(), 'period': None, 'organiser': organiser}))
        finally:
            BROWSER_MAY_ASK.reset(token)
            watch.end_progress()
        known = {calendar_id: _hand_added(svc, calendar_id) for calendar_id in {p['calendar_id'] for _, p in found}}
        preview, payload = self.compose({'sites': [p for _, p in found], 'report': report, 'known': known, 'notes': []}, ctx)
        for (site_id, _), count in zip(found, payload['counts'], strict=True):
            watch.set_result(site_id, f'{count} new' if count else 'nothing new')
        return preview, payload

    def compose(self, payload: dict, ctx: Context) -> tuple[Preview, dict]:
        """One preview of the new events, in a section per site, each site composed by the import."""
        importer, skipped = AddEventsFromWeb(), watch.skipped()
        shared = {'all_day': payload.get('all_day', False), 'default_duration': payload.get('default_duration', DEFAULT_DURATION),
                  'edits': payload.get('edits', {})}
        rows, composed, counts, options, notes = [], [], [], [], {}
        for site in payload['sites']:
            preview, site_payload = importer.compose({**site, **shared}, ctx)
            known = payload['known'].get(site['calendar_id'], [])
            # CLAUDE> not in the calendar (imported unchanged: not selectable; typed in by the user: matched here), not past;
            # declined at an earlier check: listed unticked
            # CLAUDE> an earlier import the page now words differently is in the calendar too: a check offers new events only
            offered = [r for r in preview.rows if r.selectable and r.note != 'in the past' and r.id not in site_payload['updates']
                       and not _already_there(site_payload['bodies'][r.id], r.cells['Source'], site['url'], known)]
            for row in offered:
                row.group = site['label']
                if row.id in skipped:
                    row.selected, row.note = False, 'declined before'
            new = sum(1 for r in offered if r.id not in skipped)
            declined = len(offered) - new
            notes[site['label']] = (f'{new} new' if new else 'nothing new') + (f' · {declined} declined before' if declined else '')
            rows += offered
            counts.append(new)
            composed.append(site_payload)
            options = [o for o in preview.options if o.name in ('times', 'default_duration')]
        groups = [PreviewGroup(name=r['name'], calendar=r['calendar'], note=r['note'] or notes.get(r['name'], ''))
                  for r in payload['report']]
        total = sum(counts)
        summary = f'{total} new event{"" if total == 1 else "s"}' if total else 'No new events'
        columns = ['Date', 'Time', 'Duration', 'Title', 'Place', 'Info', 'Source']
        preview = Preview(summary=summary, columns=columns, rows=rows, notes=payload['notes'], options=options, groups=groups)
        return preview, {**payload, **shared, 'sites': composed, 'counts': counts, 'offered': [r.id for r in rows]}

    def adjust(self, payload: dict, options: dict[str, str], ctx: Context) -> tuple[Preview, dict]:
        """The times choice, the default duration and edits per event, as in the import."""
        all_day = options['times'] == ALL_DAY if options.get('times') else payload.get('all_day', False)
        default = parse_duration(options['default_duration']) if options.get('default_duration', '').strip() \
            else payload.get('default_duration', DEFAULT_DURATION)
        edits = {key: dict(fields) for key, fields in payload.get('edits', {}).items()}
        for name, value in options.items():
            column, _, key = name.partition(':')
            if key and column in EDITABLE:
                edits.setdefault(key, {})[column] = value
        return self.compose({**payload, 'all_day': all_day, 'default_duration': default, 'edits': edits}, ctx)

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Add the ticked events; the ones left unticked are shown unticked from now on."""
        importer, results = AddEventsFromWeb(), []
        for site in payload['sites']:
            results += importer.execute(site, selected & set(site['bodies']), ctx)
        watch.skip(set(payload['offered']) - selected)
        return results or ['Nothing added.']

    def execute_group(self, payload: dict, selected: set[str], group: str, ctx: Context) -> tuple[list[str], set[str]]:
        """Add the ticked events of one site only; its unticked ones count as declined. The site leaves the payload, so
        adding the rest later does not touch it. Returns the results and the site's row ids."""
        site = next(s for s in payload['sites'] if s['label'] == group)
        rows = set(site['bodies'])
        results = AddEventsFromWeb().execute(site, selected & rows, ctx)
        watch.skip({r for r in payload['offered'] if r in rows} - selected)
        payload['sites'] = [s for s in payload['sites'] if s is not site]
        payload['offered'] = [r for r in payload['offered'] if r not in rows]
        return results or ['Nothing added.'], rows
