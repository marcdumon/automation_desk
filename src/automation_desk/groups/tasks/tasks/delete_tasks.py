"""Standard task: delete tasks."""

from automation_desk.dates import label
from automation_desk.groups.base import Context, Preview, Row, StandardTask
from automation_desk.groups.tasks.client import due_date, tasks_in
from automation_desk.groups.tasks.select import SelectionArgs, describe, select


class DeleteTasks(StandardTask):
    """Remove one task, or many."""

    id = 'delete_tasks'
    name = 'Delete tasks'
    description = 'Deletes the tasks you describe: one task by a word of its title, completed ones in a list, ...'
    example = 'delete the completed tasks in list Completed'
    Args = SelectionArgs

    def resolve(self, args: SelectionArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching tasks, noting subtasks that a delete takes along."""
        svc = ctx.google('tasks', 'v1')
        found = select(args, ctx)
        children: dict[str, int] = {}
        for list_id in {i.list_id for i in found}:
            for task in tasks_in(svc, list_id, with_completed=True):
                if task.get('parent'):
                    children[task['parent']] = children.get(task['parent'], 0) + 1
        rows = [Row(id=i.task['id'],
                    note=f"also deletes its {children[i.task['id']]} subtask(s)" if children.get(i.task['id']) else '',
                    cells={'Task': i.task.get('title') or '(untitled)', 'List': i.list_title,
                           'State': 'done' if i.task.get('status') == 'completed' else 'open',
                           'Due': label(d) if (d := due_date(i.task)) else '—'}) for i in found]
        targets = {i.task['id']: {'list_id': i.list_id, 'etag': i.task.get('etag'), 'title': i.task.get('title', '')}
                   for i in found}
        return Preview(summary=f'Delete {len(rows)} task(s). {describe(args)}', columns=['Task', 'List', 'State', 'Due'],
                       rows=rows, notes=['Deleted tasks cannot be brought back from Google Tasks.']), {'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Delete each selected task, skipping any that changed since the preview."""
        svc = ctx.google('tasks', 'v1')
        results = []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            current = svc.tasks().get(tasklist=target['list_id'], task=task_id).execute()
            if current.get('etag') != target['etag']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            svc.tasks().delete(tasklist=target['list_id'], task=task_id).execute()
            results.append(f'Deleted "{target["title"]}".')
        return results
