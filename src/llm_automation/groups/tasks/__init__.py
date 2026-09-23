"""Tasks task group."""

from llm_automation.groups.base import TaskGroup
from llm_automation.groups.tasks.tasks.add_task import AddTask
from llm_automation.groups.tasks.tasks.change_dates import ChangeDates
from llm_automation.groups.tasks.tasks.complete_tasks import CompleteTasks
from llm_automation.groups.tasks.tasks.delete_tasks import DeleteTasks
from llm_automation.groups.tasks.tasks.move_tasks import MoveTasks

GROUP = TaskGroup(id='tasks', name='Tasks', description='Google Tasks',
                  tasks=[ChangeDates(), MoveTasks(), CompleteTasks(), DeleteTasks(), AddTask()])
