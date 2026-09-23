"""Standard task: change the due date of tasks."""

from pydantic import Field

from llm_automation.dates import label, resolve_day
from llm_automation.groups.base import Context, Preview, Row, StandardTask
from llm_automation.groups.tasks.client import due_date, due_value
from llm_automation.groups.tasks.select import SelectionArgs, describe, select


class ChangeDatesArgs(SelectionArgs):
    """Which tasks, and their new date as a relative expression."""

    new_due: str = Field(description="The NEW date as the user expressed it: 'tomorrow', 'today+2', 'friday', "
                                     "'next monday', 'in 3 days'. Never a numeric or ISO date.")


class ChangeDates(StandardTask):
    """Give one task, or many, a new due date."""

    id = 'change_dates'
    name = 'Change the date of tasks'
    description = ('Sets a new due date on the tasks you describe: one task by a word of its title, all tasks of a list, '
                   'overdue ones, or those due in a period.')
    example = 'change the date of the Zalando task in list Today to tomorrow'
    Args = ChangeDatesArgs
    guidance = 'The target date goes in new_due; a date that says which tasks (their current due date) goes in due_period.'

    def resolve(self, args: ChangeDatesArgs, ctx: Context) -> tuple[Preview, dict]:
        """Find the tasks and resolve the new date once."""
        new_due = resolve_day(args.new_due, ctx.today, ctx.sentence)
        rows, targets = [], {}
        for item in select(args, ctx):
            current = due_date(item.task)
            same = current == new_due
            rows.append(Row(id=item.task['id'], selectable=not same, selected=not same,
                            note='already on that date' if same else '',
                            cells={'Task': item.task.get('title') or '(untitled)', 'List': item.list_title,
                                   'Current date': label(current) if current else '—', 'New date': label(new_due)}))
            targets[item.task['id']] = {'list_id': item.list_id, 'etag': item.task.get('etag'), 'title': item.task.get('title', '')}
        preview = Preview(summary=f'Set {sum(r.selectable for r in rows)} task(s) to {label(new_due)}. {describe(args)}',
                          columns=['Task', 'List', 'Current date', 'New date'], rows=rows,
                          notes=['Google Tasks stores only the date of a due date, never a time.'])
        return preview, {'due': due_value(new_due), 'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Patch `due` on each selected task, skipping any that changed since the preview."""
        svc = ctx.google('tasks', 'v1')
        results = []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            current = svc.tasks().get(tasklist=target['list_id'], task=task_id).execute()
            if current.get('etag') != target['etag']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            svc.tasks().patch(tasklist=target['list_id'], task=task_id, body={'due': payload['due']}).execute()
            results.append(f'Moved "{target["title"]}" to {payload["due"][:10]}.')
        return results
