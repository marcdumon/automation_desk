"""Standard task: give every task with a label a deadline ('give all tasks with label this_week a deadline friday').

The label is matched and the date resolved in code; the model only copies the user's words. A deadline in Todoist is its
own field, apart from the task's date, so the planned day stays as it is.
"""

from datetime import date

from pydantic import Field

from automation_desk.dates import label as day_label
from automation_desk.dates import resolve_day
from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, match_name


class DeadlineArgs(TaskArgs):
    """Which label, and the deadline as the user said it."""

    label: str = Field(description="The label as the user named it, e.g. 'this_week', 'this week', 'frog'.")
    deadline: str = Field(description="The deadline copied as the user said it: 'friday', 'next monday', 'in 3 days', "
                                      "'oct 30', '31-12-2026'. Copy a date exactly as the user wrote it, also with numbers; "
                                      'never turn words into a numeric date yourself.')


def _deadline(task: dict) -> str | None:
    """A task's deadline as 'YYYY-MM-DD', or None."""
    return (task.get('deadline') or {}).get('date')


class LabelDeadline(StandardTask):
    """Set one deadline on every task with a label."""

    id = 'label_deadline'
    name = 'Give tasks with a label a deadline'
    description = ("Sets the same deadline on every open task with the label you name, e.g. all @this_week tasks by friday. "
                   "The task's own date stays as it is.")
    example = 'give all tasks with label this_week a deadline friday'
    Args = DeadlineArgs

    def resolve(self, args: DeadlineArgs, ctx: Context) -> tuple[Preview, dict]:
        """Find the label and the day, then every open task with that label."""
        todoist = ctx.todoist()
        label = match_name(args.label, todoist.labels(), 'name', 'label')['name']
        day = resolve_day(args.deadline, ctx.today, ctx.sentence)
        projects = {p['id']: p['name'] for p in todoist.projects()}
        rows, targets = [], {}
        for task in (t for t in todoist.open_tasks() if label in t.get('labels', [])):
            now = _deadline(task)
            same = now == day.isoformat()
            rows.append(Row(id=task['id'], selectable=not same, selected=not same,
                            note='already has that deadline' if same else 'replaces its deadline' if now else '',
                            cells={'Task': task.get('content') or '(untitled)', 'Project': projects.get(task['project_id'], '?'),
                                   'Deadline now': day_label(date.fromisoformat(now)) if now else '—',
                                   'New deadline': day_label(day)}))
            if not same:
                targets[task['id']] = {'title': task.get('content', ''), 'deadline': now, 'labels': sorted(task.get('labels', []))}
        summary = (f'Set deadline {day_label(day)} on {len(targets)} task(s) with label @{label}.' if rows
                   else f'No open task has the label @{label}.')
        return Preview(summary=summary, columns=['Task', 'Project', 'Deadline now', 'New deadline'], rows=rows,
                       read_only=not rows), {'day': day.isoformat(), 'label': label, 'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Set the deadline on each selected task, skipping any whose deadline or labels changed since the preview."""
        todoist, results = ctx.todoist(), []
        shown = day_label(date.fromisoformat(payload['day']))
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            current = todoist.task(task_id)
            if _deadline(current) != target['deadline'] or sorted(current.get('labels', [])) != target['labels']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            todoist.update(task_id, {'deadline_date': payload['day']})
            results.append(f'Deadline {shown} set on "{target["title"]}".')
        return results
