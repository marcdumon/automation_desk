"""Which calendar a sentence names: Google shows the main calendar under the user's own name, the app knows its address."""

import pytest

from automation_desk.groups.base import UserError
from automation_desk.groups.calendar.client import pick_calendar

CALENDARS = [{'id': 'test-id', 'summary': 'Test'}, {'id': 'dumon.marc@gmail.com', 'summary': 'dumon.marc@gmail.com', 'primary': True},
             {'id': 'events-id', 'summary': 'Events'}]


@pytest.mark.parametrize('name', ['Marc Dumon', 'marc', '', ' ', 'my calendar', 'the calendar', 'main calendar', 'primary',
                                  'mijn agenda'])
def test_the_user_s_own_name_or_no_name_is_the_main_calendar(name: str) -> None:
    """'into calendar Marc Dumon' and 'into the calendar' failed on 28 September."""
    assert pick_calendar(name, CALENDARS)['id'] == 'dumon.marc@gmail.com'


def test_other_names_match_as_before() -> None:
    assert pick_calendar('events', CALENDARS)['id'] == 'events-id'
    with pytest.raises(UserError, match="No calendar named 'Holidays'"):
        pick_calendar('Holidays', CALENDARS)
