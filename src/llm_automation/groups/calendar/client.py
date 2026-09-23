"""Google Calendar API helpers shared by the Calendar group's standard tasks."""

from googleapiclient.discovery import Resource


def writable_calendars(svc: Resource) -> list[dict]:
    """Calendars the user can add events to."""
    items, token = [], None
    while True:
        page = svc.calendarList().list(pageToken=token, minAccessRole='writer').execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items


def timezone(svc: Resource) -> str:
    """The user's Calendar timezone, e.g. 'Europe/Brussels'."""
    return svc.settings().get(setting='timezone').execute()['value']


def events_tagged(svc: Resource, calendar_id: str, key: str, value: str) -> list[dict]:
    """Events carrying a private extended property, used to recognise earlier imports."""
    items, token = [], None
    while True:
        page = svc.events().list(calendarId=calendar_id, privateExtendedProperty=f'{key}={value}', maxResults=2500,
                                 pageToken=token).execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items


def events_between(svc: Resource, calendar_id: str, time_min: str | None, time_max: str | None) -> list[dict]:
    """Events of a calendar in a period (RFC 3339 bounds, None for open), repeating events as single occurrences."""
    items, token = [], None
    while True:
        page = svc.events().list(calendarId=calendar_id, timeMin=time_min, timeMax=time_max, singleEvents=True,
                                 orderBy='startTime', maxResults=2500, pageToken=token).execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items
