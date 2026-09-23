"""Tasks task group."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.tasks.tasks.add_task import AddTask
from automation_desk.groups.tasks.tasks.change_dates import ChangeDates
from automation_desk.groups.tasks.tasks.complete_tasks import CompleteTasks
from automation_desk.groups.tasks.tasks.delete_tasks import DeleteTasks
from automation_desk.groups.tasks.tasks.move_tasks import MoveTasks

GROUP = TaskGroup(id='tasks', name='Tasks', description='Google Tasks',
                  tasks=[ChangeDates(), MoveTasks(), CompleteTasks(), DeleteTasks(), AddTask()])
