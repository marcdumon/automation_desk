"""Standard task: mark tasks as completed."""

from automation_desk.groups.base import Context, Preview, Row, StandardTask
from automation_desk.groups.tasks.client import repeat_rule, task_state, when
from automation_desk.groups.tasks.select import SelectionArgs, describe, select


class CompleteTasks(StandardTask):
    """Tick off one task, or many."""

    id = 'complete_tasks'
    name = 'Complete tasks'
    description = 'Marks the tasks you describe as done: one task by a word of its title, a whole list, overdue ones, ...'
    example = 'mark the Zalando task as done'
    Args = SelectionArgs

    def resolve(self, args: SelectionArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching open tasks; a repeating one moves on to its next date."""
        found = [i for i in select(args, ctx) if not i.task.get('checked')]
        rows = [Row(id=i.task['id'], note=f'repeats {repeat_rule(i.task)}: moves on to its next date' if repeat_rule(i.task) else '',
                    cells={'Task': i.task.get('content') or '(untitled)', 'List': i.list_title, 'Date': when(i.task)})
                for i in found]
        targets = {i.task['id']: {'state': task_state(i.task), 'title': i.task.get('content', '')} for i in found}
        return Preview(summary=f'Complete {len(rows)} task(s). {describe(args)}', columns=['Task', 'List', 'Date'],
                       rows=rows), {'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Close each selected task, skipping any that changed since the preview."""
        todoist, results = ctx.todoist(), []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            if task_state(todoist.task(task_id)) != target['state']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            todoist.close(task_id)
            results.append(f'Completed "{target["title"]}".')
        return results
