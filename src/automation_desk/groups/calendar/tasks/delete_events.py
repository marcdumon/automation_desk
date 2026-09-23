"""Standard task: delete events from one of your calendars.

Which events is decided in code from the calendar, a period, words in the title and the source site. The model only
reads the sentence; your event titles never go to it, so there is no free-text filter.
"""

import re
from datetime import datetime, time, timedelta

from googleapiclient.errors import HttpError
from pydantic import Field

from automation_desk.dates import label, resolve_range
from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, UserError, match_name
from automation_desk.groups.calendar.client import events_between, writable_calendars

SOURCE = re.compile(r'Source:\s*(\S+)')
# CLAUDE> words that mean 'no period' rather than a period
NO_LIMIT = {'', 'all', 'all events', 'everything', 'any', 'any time', 'anytime', 'always', 'ever'}


class DeleteEventsArgs(TaskArgs):
    """Which calendar and which of its events."""

    calendar_name: str = Field(description="The calendar's name as the user wrote it, e.g. 'Test'.")
    date_range: str = Field(description="Only events in this period, as the user said it: 'today', 'next week', "
                                        "'this month', 'today to next friday'. Empty when the user gives no period "
                                        "('all events' is not a period). "
                                        'Never a numeric date.')
    title_contains: list[str] = Field(description="One entry per event the user names by (part of) its title, copied from "
                                                  "the sentence: ['KMSKA'] for 'with KMSKA in the title', ['dentist', "
                                                  "'yoga'] for 'the dentist and yoga appointments'. An event matches if its "
                                                  'title contains any entry. Empty list when none.')
    source_contains: str = Field(description="A website the events came from, as the user wrote it, e.g. 'kmska.be' in "
                                             "'events from kmska.be' or 'imported from MoMA'. Empty when none.")


def _start(event: dict) -> tuple[str, str]:
    """Date and time columns for an event."""
    start = event.get('start', {})
    if 'date' in start:
        return label(datetime.fromisoformat(start['date']).date()), 'all day'
    moment = datetime.fromisoformat(start['dateTime'])
    return label(moment.date()), moment.strftime('%H:%M')


def _norm(text: str) -> str:
    """Case- and space-insensitive form."""
    return ' '.join(text.split()).casefold()


class DeleteEvents(StandardTask):
    """Remove the matching events of one calendar."""

    id = 'delete_events'
    name = 'Delete events from a calendar'
    description = ('Deletes events from one of your calendars, limited by a period, words in the title or the website '
                   'they were imported from. You tick which ones before anything is deleted.')
    example = 'delete the events of next week in calendar Test'
    Args = DeleteEventsArgs
    guidance = ("Put the period in date_range, words the title must contain in title_contains and a website or organiser "
                "the events came from in source_contains. 'all events' means no limit.")

    def resolve(self, args: DeleteEventsArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the calendar's events that pass every filter."""
        svc = ctx.google('calendar', 'v3')
        calendar = match_name(args.calendar_name, writable_calendars(svc), 'summary', 'calendar')
        no_period = _norm(args.date_range) in NO_LIMIT
        period = None if no_period else resolve_range(args.date_range, ctx.today, ctx.sentence)
        time_min = time_max = None
        if period:
            time_min = datetime.combine(period[0], time.min, ctx.tz).isoformat()
            time_max = datetime.combine(period[1] + timedelta(days=1), time.min, ctx.tz).isoformat()

        words, source = [w for w in (_norm(t) for t in args.title_contains) if w], _norm(args.source_contains)
        rows, targets = [], {}
        for event in events_between(svc, calendar['id'], time_min, time_max):
            title = event.get('summary', '(untitled)')
            link = (SOURCE.search(event.get('description', '')) or [None, ''])[1]
            if words and not any(w in _norm(title) for w in words):
                continue
            if source and source not in _norm(link) and source not in _norm(event.get('description', '')):
                continue
            day, clock = _start(event)
            repeating = 'recurringEventId' in event
            rows.append(Row(id=event['id'], note='repeating event: only this occurrence' if repeating else '',
                            cells={'Date': day, 'Time': clock, 'Title': title, 'Place': event.get('location') or '—',
                                   'Source': link or '—'}))
            targets[event['id']] = {'etag': event.get('etag'), 'title': title, 'day': day}
        if not rows:
            raise UserError(f"No events in '{calendar['summary']}' match that.")

        limits = [f'in {label(period[0])} to {label(period[1])}' if period else 'at any date']
        if words:
            limits.append('with ' + ' or '.join(f"'{t.strip()}'" for t in args.title_contains if t.strip()) + ' in the title')
        if source:
            limits.append(f"from '{args.source_contains}'")
        preview = Preview(summary=f"Delete {len(rows)} event(s) from calendar '{calendar['summary']}' ({', '.join(limits)})",
                          columns=['Date', 'Time', 'Title', 'Place', 'Source'], rows=rows,
                          notes=['Google keeps deleted events in the calendar bin for 30 days.'])
        return preview, {'calendar_id': calendar['id'], 'calendar': calendar['summary'], 'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Delete each selected event, skipping any that changed or disappeared since the preview."""
        svc = ctx.google('calendar', 'v3')
        results = []
        for event_id, target in payload['targets'].items():
            if event_id not in selected:
                continue
            name = f'"{target["title"]}" on {target["day"]}'
            try:
                current = svc.events().get(calendarId=payload['calendar_id'], eventId=event_id).execute()
            except HttpError as error:
                results.append(f'Skipped {name}: it could not be found any more ({error.status_code}).')
                continue
            if current.get('status') == 'cancelled':
                results.append(f'Skipped {name}: it was already deleted.')
                continue
            if current.get('etag') != target['etag']:
                results.append(f'Skipped {name}: it changed since the preview.')
                continue
            svc.events().delete(calendarId=payload['calendar_id'], eventId=event_id).execute()
            results.append(f'Deleted {name} from {payload["calendar"]}.')
        return results
