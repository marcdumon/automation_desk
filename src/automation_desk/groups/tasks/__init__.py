"""Tasks task group: Todoist."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.tasks.tasks.deadlines import LabelDeadline
from automation_desk.groups.tasks.tasks.someday import DemoteUnplanned, PromotePlanned
from automation_desk.groups.tasks.tasks.titles import VerbTitles

GROUP = TaskGroup(id='tasks', name='Tasks', description='Todoist',
                  tasks=[PromotePlanned(), DemoteUnplanned(), VerbTitles(), LabelDeadline()])
