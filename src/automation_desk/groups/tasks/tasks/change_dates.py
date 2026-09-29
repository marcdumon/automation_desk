"""Standard task: change the date of tasks."""

from pydantic import Field

from automation_desk.dates import label, resolve_day
from automation_desk.groups.base import Context, Preview, Row, StandardTask
from automation_desk.groups.tasks.client import repeat_rule, task_date, task_state, task_time, utc_moment, when
from automation_desk.groups.tasks.select import SelectionArgs, describe, select


class ChangeDatesArgs(SelectionArgs):
    """Which tasks, and their new date as a relative expression."""

    new_due: str = Field(description="The NEW date as the user expressed it: 'tomorrow', 'today+2', 'friday', "
                                     "'next monday', 'in 3 days'. Never a numeric or ISO date.")


class ChangeDates(StandardTask):
    """Give one task, or many, a new date."""

    id = 'change_dates'
    name = 'Change the date of tasks'
    description = ('Sets a new date on the tasks you describe: one task by a word of its title, all tasks of a list, '
                   'overdue ones, or those planned in a period. A task with a time keeps its time.')
    example = 'change the date of the Zalando task in list Today to tomorrow'
    Args = ChangeDatesArgs
    guidance = 'The target date goes in new_due; a date that says which tasks (their current date) goes in due_period.'

    def resolve(self, args: ChangeDatesArgs, ctx: Context) -> tuple[Preview, dict]:
        """Find the tasks and resolve the new date once; a task with a time keeps it."""
        new_day = resolve_day(args.new_due, ctx.today, ctx.sentence)
        rows, targets = [], {}
        for item in select(args, ctx):
            task, clock = item.task, task_time(item.task)
            same = task_date(task) == new_day
            # CLAUDE> a new date replaces a repeating task's rule in Todoist, which ends the repeat
            repeats = repeat_rule(task)
            note = 'already on that date' if same else f'repeats {repeats}: a new date here would end the repeat' if repeats else ''
            rows.append(Row(id=task['id'], selectable=not same, selected=not same and not repeats, note=note,
                            cells={'Task': task.get('content') or '(untitled)', 'List': item.list_title, 'Current date': when(task),
                                   'New date': f'{label(new_day)} {clock:%H:%M}' if clock else label(new_day)}))
            if not same:
                fields = {'due_datetime': utc_moment(new_day, clock, ctx.tz)} if clock else {'due_date': new_day.isoformat()}
                targets[task['id']] = {'state': task_state(task), 'title': task.get('content', ''), 'fields': fields}
        preview = Preview(summary=f'Set {sum(r.selected for r in rows)} task(s) to {label(new_day)}. {describe(args)}',
                          columns=['Task', 'List', 'Current date', 'New date'], rows=rows)
        return preview, {'day': label(new_day), 'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Set the new date on each selected task, skipping any that changed since the preview."""
        todoist, results = ctx.todoist(), []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            if task_state(todoist.task(task_id)) != target['state']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            todoist.update(task_id, target['fields'])
            results.append(f'Moved "{target["title"]}" to {payload["day"]}.')
        return results
