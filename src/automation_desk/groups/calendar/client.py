"""Google Calendar API helpers shared by the Calendar group's standard tasks."""

import re

from googleapiclient.discovery import Resource

from automation_desk.groups.base import UserError, match_name

# CLAUDE> words that name no calendar of their own: 'my calendar', 'the main agenda', 'mijn agenda' is the main calendar
MAIN_WORDS = {'my', 'the', 'main', 'primary', 'default', 'own', 'calendar', 'agenda', 'mijn', 'de', 'mon', 'ma', 'calendrier',
              'kalender'}


def writable_calendars(svc: Resource) -> list[dict]:
    """Calendars the user can add events to."""
    items, token = [], None
    while True:
        page = svc.calendarList().list(pageToken=token, minAccessRole='writer').execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items


def pick_calendar(name: str, calendars: list[dict]) -> dict:
    """The calendar a sentence names. No name, or only words like 'my calendar', 'main' or 'primary', is the main calendar.
    Google shows the main calendar under the user's own name ('Marc Dumon') while its name here is its address
    (dumon.marc@gmail.com): a name whose every word is in that address is the main calendar too."""
    main = next((c for c in calendars if c.get('primary')), None)
    words = re.findall(r'\w+', name.casefold())
    if main and all(word in MAIN_WORDS for word in words):
        return main
    try:
        return match_name(name, calendars, 'summary', 'calendar')
    except UserError:
        address = f'{main.get("id", "")} {main.get("summary", "")}'.casefold() if main else ''
        if main and all(word in address for word in words if word not in MAIN_WORDS):
            return main
        raise


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


def all_events(svc: Resource, calendar_id: str) -> list[dict]:
    """Every event of a calendar, repeating ones once."""
    items, token = [], None
    while True:
        page = svc.events().list(calendarId=calendar_id, maxResults=2500, pageToken=token).execute()
        items += page.get('items', [])
        if not (token := page.get('nextPageToken')):
            return items
