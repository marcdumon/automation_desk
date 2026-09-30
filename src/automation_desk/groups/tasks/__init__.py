"""Tasks task group: Todoist."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.tasks.tasks.someday import DemoteUnplanned, PromotePlanned

GROUP = TaskGroup(id='tasks', name='Tasks', description='Todoist', tasks=[PromotePlanned(), DemoteUnplanned()])
