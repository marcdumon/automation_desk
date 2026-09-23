"""All task groups, in sidebar order. Add a group here; add a standard task to its TaskGroup in the group package."""

from llm_automation.groups.base import TaskGroup
from llm_automation.groups.calendar import GROUP as CALENDAR
from llm_automation.groups.gmail import GROUP as GMAIL
from llm_automation.groups.tasks import GROUP as TASKS

GROUPS: dict[str, TaskGroup] = {group.id: group for group in (CALENDAR, TASKS, GMAIL)}
