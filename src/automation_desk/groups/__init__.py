"""All task groups, in sidebar order. Add a group here; add a standard task to its TaskGroup in the group package."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.calendar import GROUP as CALENDAR
from automation_desk.groups.gmail import GROUP as GMAIL
from automation_desk.groups.tasks import GROUP as TASKS

GROUPS: dict[str, TaskGroup] = {group.id: group for group in (CALENDAR, TASKS, GMAIL)}
