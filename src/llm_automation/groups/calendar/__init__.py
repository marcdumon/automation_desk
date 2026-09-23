"""Calendar task group."""

from llm_automation.groups.base import TaskGroup
from llm_automation.groups.calendar.tasks.add_events_from_web import AddEventsFromWeb
from llm_automation.groups.calendar.tasks.delete_events import DeleteEvents

GROUP = TaskGroup(id='calendar', name='Calendar', description='Google Calendar', tasks=[AddEventsFromWeb(), DeleteEvents()])
