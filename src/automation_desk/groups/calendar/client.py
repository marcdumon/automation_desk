"""The calendar a sentence names; the Calendar API itself is in google_api.calendar."""

import re

from automation_desk.groups.base import UserError, match_name

# words that name no calendar of their own: 'my calendar', 'the main agenda', 'mijn agenda' is the main calendar
MAIN_WORDS = {'my', 'the', 'main', 'primary', 'default', 'own', 'calendar', 'agenda', 'mijn', 'de', 'mon', 'ma', 'calendrier',
              'kalender'}


def pick_calendar(name: str, calendars: list[dict]) -> dict:
    """The calendar a sentence names. No name, or only words like 'my calendar', 'main' or 'primary', is the main calendar.
    Google shows the main calendar under the user's own name ('Jan Peters') while its name here is its address
    (jan.peters@gmail.com): a name whose every word is in that address is the main calendar too."""
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

