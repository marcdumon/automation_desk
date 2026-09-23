"""Standard task: mark tasks as completed."""

from llm_automation.dates import label
from llm_automation.groups.base import Context, Preview, Row, StandardTask
from llm_automation.groups.tasks.client import due_date
from llm_automation.groups.tasks.select import SelectionArgs, describe, select


class CompleteTasks(StandardTask):
    """Tick off one task, or many."""

    id = 'complete_tasks'
    name = 'Complete tasks'
    description = 'Marks the tasks you describe as done: one task by a word of its title, a whole list, overdue ones, ...'
    example = 'mark the Zalando task as done'
    Args = SelectionArgs

    def resolve(self, args: SelectionArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching open tasks."""
        found = [i for i in select(args, ctx) if i.task.get('status') != 'completed']
        rows = [Row(id=i.task['id'], cells={'Task': i.task.get('title') or '(untitled)', 'List': i.list_title,
                                            'Due': label(d) if (d := due_date(i.task)) else '—'}) for i in found]
        targets = {i.task['id']: {'list_id': i.list_id, 'etag': i.task.get('etag'), 'title': i.task.get('title', '')}
                   for i in found}
        return Preview(summary=f'Complete {len(rows)} task(s). {describe(args)}', columns=['Task', 'List', 'Due'],
                       rows=rows), {'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Set each selected task to completed, skipping any that changed since the preview."""
        svc = ctx.google('tasks', 'v1')
        results = []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            current = svc.tasks().get(tasklist=target['list_id'], task=task_id).execute()
            if current.get('etag') != target['etag']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            svc.tasks().patch(tasklist=target['list_id'], task=task_id, body={'status': 'completed'}).execute()
            results.append(f'Completed "{target["title"]}".')
        return results
