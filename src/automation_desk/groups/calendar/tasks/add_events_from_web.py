"""Standard task: read an agenda web page and add its events to one of your calendars."""

import hashlib
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from email.utils import parseaddr

import httpx
from pydantic import Field

from automation_desk.config import config
from automation_desk.dates import label, resolve_range, weekday_index
from automation_desk.groups.base import Context, Preview, PreviewOption, Row, StandardTask, TaskArgs, UserError, match_name
from automation_desk.groups.calendar.client import events_tagged, writable_calendars
from automation_desk.groups.calendar.organisers import Organiser, organiser_for, placed, save, titled
from automation_desk.groups.calendar.pdf import PdfError, is_pdf
from automation_desk.groups.calendar.web_page import WebEvent, pdf_as_html, pdf_events, read_events
from automation_desk.groups.gmail.client import gmail_link, mail_pdfs, message_ids, metadata, received, sender
from automation_desk.groups.gmail.select import any_of

URL = re.compile(r'(?:https?://|www\.)[^\s<>"\']+', re.IGNORECASE)
DEFAULT_DURATION = timedelta(hours=2)
PAGE_TAG = 'automation_desk_page'
EVENT_TAG = 'automation_desk_key'


class AddEventsArgs(TaskArgs):
    """Where the events go and which ones to leave out. The URL is taken from the sentence by code."""

    calendar_name: str = Field(description="The target calendar's name as the user wrote it, e.g. 'Exhibitions'.")
    exclude_weekdays: list[str] = Field(description="Weekdays whose events must be skipped, lowercase English "
                                                    "('friday'), from phrases like 'except the ones on fridays'. Else [].")
    date_range: str = Field(description="Only events in this period, as the user said it: 'this month', 'next week', "
                                        "'today to next friday'. Empty when not limited. Never a numeric date.")
    text_filter: str = Field(description="Any other condition on the events in the user's words, e.g. 'only concerts', "
                                         "'no workshops for children'. Empty when none.")
    follow_pages: bool = Field(description='True when the user asks to follow further/next pages of the agenda.')
    pdf_mail_from: list[str] = Field(description="Only when the user says the agenda PDF is in a mail: its senders as named, "
                                                 "e.g. ['Kanal'] for 'the PDF in the mail from Kanal'. Else [].")
    pdf_mail_subject: list[str] = Field(description='Only when the agenda PDF is in a mail: words of that mail\'s subject '
                                                    'the user gave. Else [].')


def source_key(page_url: str, event: WebEvent) -> str:
    """Stable id of one imported event, used to skip it when the page is imported again.

    An event's own detail link is preferred over its title, because on plain-text pages the title is the model's copy
    and may be worded slightly differently on the next import.
    """
    own_link = event.url.startswith('http') and event.url.split('#')[0] != page_url.split('#')[0]
    identity = event.url if own_link else ' '.join(event.title.casefold().split())
    return hashlib.sha1(f'{page_url}|{identity}|{event.start.isoformat()}'.encode()).hexdigest()[:20]


def page_tag(url: str) -> str:
    """Stable id of the source page."""
    return hashlib.sha1(url.split('#')[0].rstrip('/').encode()).hexdigest()[:20]


def event_body(event: WebEvent, tz_name: str, key: str, tag: str) -> dict:
    """The Calendar API body for one web event: timed when the page gives a start time, all-day otherwise."""
    last_day = event.end or event.start
    body: dict = {
        'summary': event.title,
        'location': event.location,
        'description': '\n\n'.join(part for part in (event.description, f'Source: {event.url}') if part),
        'extendedProperties': {'private': {PAGE_TAG: tag, EVENT_TAG: key}},
    }
    multi_day = last_day != event.start and event.end_time is None
    if event.start_time is None or multi_day:
        body['start'] = {'date': event.start.isoformat()}
        body['end'] = {'date': (last_day + timedelta(days=1)).isoformat()}
        return body
    start = datetime.combine(event.start, event.start_time)
    end = datetime.combine(last_day, event.end_time) if event.end_time else start + DEFAULT_DURATION
    if end <= start:
        end = start + DEFAULT_DURATION
    body['start'] = {'dateTime': start.isoformat(), 'timeZone': tz_name}
    body['end'] = {'dateTime': end.isoformat(), 'timeZone': tz_name}
    return body


class EarlierImports:
    """Events imported from this page before, found again even when their key changed (an earlier import may have
    had a wrong link or dates): same key, else same title and start day, else a title unique on both sides."""

    def __init__(self, earlier: list[dict], found: list[WebEvent]) -> None:
        """Index the earlier imports."""
        self.by_key = {e.get('extendedProperties', {}).get('private', {}).get(EVENT_TAG): e for e in earlier}
        self.by_title_day = {(_norm(e.get('summary', '')), _start_day(e)): e for e in earlier}
        titles = [_norm(e.get('summary', '')) for e in earlier]
        found_titles = [_norm(e.title) for e in found]
        self.by_unique_title = {t: e for t, e in zip(titles, earlier, strict=True)
                                if titles.count(t) == 1 and found_titles.count(t) == 1}

    def match(self, key: str, event: WebEvent) -> dict | None:
        """The earlier import of this event, if any."""
        title = _norm(event.title)
        return (self.by_key.get(key) or self.by_title_day.get((title, event.start.isoformat()))
                or self.by_unique_title.get(title))


def _norm(text: str) -> str:
    """Case- and space-insensitive form for matching titles."""
    return ' '.join(text.casefold().split())


def _start_day(event: dict) -> str:
    """The start date of a Calendar API event as YYYY-MM-DD."""
    start = event.get('start', {})
    return start.get('date') or start.get('dateTime', '')[:10]


def _moment(value: dict) -> str:
    """A Calendar API start or end reduced to what matters for comparing: 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM'."""
    return value.get('date') or value.get('dateTime', '')[:16]


def differences(earlier: dict, body: dict) -> list[str]:
    """What an update would change in an earlier import, in the user's words."""
    checks = {
        'title': (earlier.get('summary', ''), body['summary']),
        'place': (earlier.get('location', ''), body['location']),
        'info and source': (earlier.get('description', ''), body['description']),
        'dates': ((_moment(earlier.get('start', {})), _moment(earlier.get('end', {}))),
                  (_moment(body['start']), _moment(body['end']))),
    }
    return [label for label, (old, new) in checks.items() if old != new]


def when(event: WebEvent) -> tuple[str, str]:
    """Date and time columns of the preview."""
    day = label(event.start) + (f' → {label(event.end)}' if event.end and event.end != event.start else '')
    if event.start_time is None:
        return day, 'all day'
    clock = event.start_time.strftime('%H:%M')
    if event.end_time:
        return day, f"{clock}-{event.end_time.strftime('%H:%M')}"
    return day, f'{clock} (2h assumed)'


class AddEventsFromWeb(StandardTask):
    """Scrape an agenda page and add its events to a named calendar."""

    id = 'add_events_from_web'
    name = 'Add events from a web page or PDF'
    description = ('Reads an agenda (a web page, a PDF link, a PDF you attach, or a PDF in a mail: date, place, info) '
                   'and adds the events to one of your calendars, with '
                   'exclusions such as weekdays, a period or a free-text rule. Importing the same page twice adds nothing twice.')
    example = 'add all events from https://example.org/agenda to calendar Exhibitions except the ones on fridays'
    Args = AddEventsArgs
    guidance = ('The URL and any attached PDF are handled by the app; do not repeat them. Put weekday exclusions in '
                'exclude_weekdays, a period in date_range and any other condition in text_filter. Only when the user says '
                'the agenda PDF is in a mail, fill pdf_mail_from and/or pdf_mail_subject.')

    def resolve(self, args: AddEventsArgs, ctx: Context) -> tuple[Preview, dict]:
        """Read the agenda (an attached PDF, a PDF in a mail, or a web page or PDF link) and who made it, then compose."""
        svc = ctx.google('calendar', 'v3')
        calendar = match_name(args.calendar_name, writable_calendars(svc), 'summary', 'calendar')
        period = resolve_range(args.date_range, ctx.today, ctx.sentence) if args.date_range.strip() else None

        with httpx.Client(timeout=60.0) as http:
            try:
                if ctx.files:
                    url, site, events, notes, html_for_organiser = self._from_files(args, ctx, http)
                elif args.pdf_mail_from or args.pdf_mail_subject:
                    url, site, events, notes, html_for_organiser = self._from_mail(args, ctx, http)
                else:
                    url, site = self._url(ctx), None
                    events, notes, html_for_organiser = read_events(url, ctx.today, ctx.tz, args.text_filter,
                                                                    config().max_pages if args.follow_pages else 1, http)
            except PdfError as error:
                raise UserError(str(error)) from error
            except httpx.HTTPStatusError as error:
                raise UserError(f'{error.request.url} answered {error.response.status_code} '
                                f'{error.response.reason_phrase}.') from error
            except httpx.HTTPError as error:
                raise UserError(f'Could not read the agenda: {error}') from error
            if not events:
                raise UserError('No events found in the agenda, also not after reading it in your browser.')
            organiser = organiser_for(url, html_for_organiser, http, site=site)

        payload = {'url': url, 'calendar_id': calendar['id'], 'calendar': calendar['summary'], 'page_tag': page_tag(url),
                   'events': events, 'notes': notes, 'excluded': {weekday_index(d) for d in args.exclude_weekdays},
                   'period': period, 'organiser': organiser}
        return self.compose(payload, ctx)

    @staticmethod
    def _url(ctx: Context) -> str:
        """The agenda address in the sentence."""
        found = URL.search(ctx.sentence)
        if not found:
            raise UserError('Put the address of the agenda page or PDF (https://...) in your sentence, attach a PDF, '
                            'or say which mail holds the PDF.')
        url = found.group(0).rstrip('.,;:!?)')
        return url if url.lower().startswith('http') else f'https://{url}'

    @staticmethod
    def _from_files(args: AddEventsArgs, ctx: Context, http: httpx.Client) -> tuple[str, str, list, list[str], str]:
        """Events of the PDFs the user attached."""
        events, notes, texts = [], [], []
        digest = hashlib.sha1(b''.join(content for _, content in ctx.files)).hexdigest()[:16]
        for name, content in ctx.files:
            if not is_pdf(content):
                raise UserError(f'{name} is not a PDF.')
            found, text = pdf_events(content, f'{name} (attached file)', ctx.today, ctx.tz, args.text_filter, http)
            events += found
            texts.append(text)
            notes.append(f'{name}: {len(found)} event(s) from its text (dates found by code, events listed by the model).')
        names = ', '.join(name for name, _ in ctx.files)
        return f'file:{digest}', f'file {names}', events, notes, pdf_as_html('\n'.join(texts), names)

    @staticmethod
    def _from_mail(args: AddEventsArgs, ctx: Context, http: httpx.Client) -> tuple[str, str, list, list[str], str]:
        """Events of the PDFs attached to the newest mail that matches the senders and subject words."""
        gmail = ctx.google('gmail', 'v1')
        query = ' '.join(filter(None, ['has:attachment filename:pdf', any_of('from', args.pdf_mail_from),
                                       any_of('subject', args.pdf_mail_subject)]))
        found, _ = message_ids(gmail, query, [], 1)
        if not found:
            raise UserError(f'No mail with a PDF matches that. Gmail search: {query}')
        meta = metadata(gmail, found[0]['id'])
        link = gmail_link(meta['thread_id'])
        events, notes, texts = [], [], []
        for name, content in mail_pdfs(gmail, meta['id']):
            got, text = pdf_events(content, link, ctx.today, ctx.tz, args.text_filter, http)
            events += got
            texts.append(text)
            notes.append(f"{name} from the mail '{meta['subject']}' of {sender(meta)} ({received(meta, ctx.tz)}): "
                         f'{len(got)} event(s).')
        if not notes:
            raise UserError(f"The mail '{meta['subject']}' has no PDF attached after all.")
        domain = parseaddr(meta['from'])[1].rpartition('@')[2].lower()
        return link, domain or sender(meta), events, notes, pdf_as_html('\n'.join(texts), meta['subject'])

    def adjust(self, payload: dict, options: dict[str, str], ctx: Context) -> tuple[Preview, dict]:
        """Use the organiser name and address the user set, remember them for the site, and compose again."""
        organiser = Organiser(site=payload['organiser'].site, name=' '.join(options.get('organiser', '').split()),
                              address=' '.join(options.get('organiser_address', '').split()))
        save(organiser)
        return self.compose({**payload, 'organiser': organiser}, ctx)

    def compose(self, payload: dict, ctx: Context) -> tuple[Preview, dict]:
        """Titles, places, exclusions and what is already in the calendar, from events already read."""
        url, organiser = payload['url'], payload['organiser']
        svc = ctx.google('calendar', 'v3')
        # CLAUDE> keys and matching use the title as the page gives it, so renaming the organiser never breaks the link
        pairs = [(e, replace(e, title=titled(e.title, organiser), location=placed(e.location, organiser)))
                 for e in payload['events']]
        find = EarlierImports(events_tagged(svc, payload['calendar_id'], PAGE_TAG, payload['page_tag']), [e for e, _ in pairs])
        rows, bodies, updates, seen = [], {}, {}, set()
        for raw, event in sorted(pairs, key=lambda p: (p[0].start, p[0].start_time or datetime.min.time())):
            key = source_key(url, raw)
            if key in seen:
                continue
            seen.add(key)
            reason = self._excluded(event, payload['excluded'], payload['period'], ctx.now.replace(tzinfo=None))
            body = event_body(event, str(ctx.tz), key, payload['page_tag'])
            earlier = find.match(key, raw) or find.match(key, event)
            changed = differences(earlier, body) if earlier else []
            unchanged = earlier is not None and not changed
            if earlier and changed:
                updates[key] = earlier['id']
                note = f"in the calendar; will be updated: {', '.join(changed)}" + (f' ({reason})' if reason else '')
            else:
                note = 'already in the calendar, unchanged' if unchanged else reason
            day, clock = when(event)
            info = (event.description[:140] + '…') if len(event.description) > 140 else event.description
            rows.append(Row(id=key, selectable=not unchanged, selected=not (unchanged or reason), note=note,
                            cells={'Date': day, 'Time': clock, 'Title': event.title, 'Place': event.location or '—',
                                   'Info': info or '—', 'Source': event.url}))
            bodies[key] = body

        chosen = sum(r.selected for r in rows)
        updating = sum(r.selected and r.id in updates for r in rows)
        notes = [*payload['notes'],
                 'Weekday exclusions apply to single-day events; multi-day events (exhibitions) are kept unless unticked.',
                 'Events with a start time but no end time get 2 hours.']
        summary = f"Add {chosen - updating} and update {updating} of {len(rows)} event(s) in calendar '{payload['calendar']}'"
        options = [
            PreviewOption(name='organiser', label='Organiser', value=organiser.name,
                          help=f'Every title starts with it. Remembered for {organiser.site}.'),
            PreviewOption(name='organiser_address', label='Organiser address', value=organiser.address,
                          help='Added to places that are missing or only name the organiser.'),
        ]
        preview = Preview(summary=summary, columns=['Date', 'Time', 'Title', 'Place', 'Info', 'Source'], rows=rows,
                          notes=notes, options=options)
        return preview, {**payload, 'bodies': bodies, 'updates': updates}

    @staticmethod
    def _excluded(event: WebEvent, weekdays: set[int], period: tuple[date, date] | None, now: datetime) -> str:
        """Why an event is left unticked by default, or '' when it is included. `now` is local wall-clock time."""
        last_day = event.end or event.start
        if event.end is None and event.end_text:
            return f'no end date on the page (ends: {event.end_text})'
        if last_day < now.date():
            return 'in the past'
        if event.start_time and last_day == now.date():
            finish = datetime.combine(last_day, event.end_time) if event.end_time else (
                datetime.combine(event.start, event.start_time) + DEFAULT_DURATION)
            if finish < now:
                return 'in the past'
        if not event.matches_filter:
            return 'does not match your filter'
        if period and (last_day < period[0] or event.start > period[1]):
            return 'outside the period'
        if last_day == event.start and event.start.weekday() in weekdays:
            return f"excluded: {event.start.strftime('%A').lower()}"
        return ''

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Update earlier imports and insert new events, re-checking for duplicates added since the preview."""
        svc = ctx.google('calendar', 'v3')
        existing = {e.get('extendedProperties', {}).get('private', {}).get(EVENT_TAG)
                    for e in events_tagged(svc, payload['calendar_id'], PAGE_TAG, payload['page_tag'])}
        results = []
        for key, body in payload['bodies'].items():
            if key not in selected:
                continue
            if event_id := payload.get('updates', {}).get(key):
                svc.events().patch(calendarId=payload['calendar_id'], eventId=event_id, body=body).execute()
                results.append(f'Updated "{body["summary"]}" in {payload["calendar"]}.')
                continue
            if key in existing:
                results.append(f'Skipped "{body["summary"]}": already in the calendar.')
                continue
            svc.events().insert(calendarId=payload['calendar_id'], body=body).execute()
            day = body['start'].get('date') or body['start']['dateTime'][:16].replace('T', ' ')
            results.append(f'Added "{body["summary"]}" on {day} to {payload["calendar"]}.')
        return results
