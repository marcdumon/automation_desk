"""Calendar task group."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.calendar.tasks.add_events_from_web import AddEventsFromWeb
from automation_desk.groups.calendar.tasks.delete_events import DeleteEvents

GROUP = TaskGroup(id='calendar', name='Calendar', description='Google Calendar', tasks=[AddEventsFromWeb(), DeleteEvents()],
                  accepts_files=True)
