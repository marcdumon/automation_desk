"""Standard task: add a task."""

from pydantic import Field

from automation_desk.dates import label, resolve_day
from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, match_name
from automation_desk.groups.tasks.client import due_value, tasklists


class AddTaskArgs(TaskArgs):
    """What the task is, where it goes and when it is due."""

    title: str = Field(description='The task itself, copied in the language the user wrote it, without the list or date.')
    list_name: str = Field(description="The list it goes in, as the user named it, e.g. 'Today'. Empty when not said.")
    due: str = Field(description="When it is due as the user said it: 'tomorrow', 'friday', 'in 3 days'. Empty when not "
                                 'said. Never a numeric date.')
    notes: str = Field(description='Extra details the user gave for the task, copied. Empty when none.')


class AddTask(StandardTask):
    """Create one task."""

    id = 'add_task'
    name = 'Add a task'
    description = 'Adds a task to one of your lists, with an optional due date and notes.'
    example = 'add a task call the plumber to list Today due tomorrow'
    Args = AddTaskArgs

    def resolve(self, args: AddTaskArgs, ctx: Context) -> tuple[Preview, dict]:
        """Resolve the list and the due date."""
        lists = tasklists(ctx.google('tasks', 'v1'))
        tasklist = match_name(args.list_name, lists, 'title', 'task list') if args.list_name.strip() else lists[0]
        due = resolve_day(args.due, ctx.today, ctx.sentence) if args.due.strip() else None
        body = {'title': args.title.strip(), **({'notes': args.notes.strip()} if args.notes.strip() else {}),
                **({'due': due_value(due)} if due else {})}
        preview = Preview(summary=f"Add a task to '{tasklist['title']}'", columns=['Task', 'List', 'Due', 'Notes'],
                          rows=[Row(id='new', cells={'Task': body['title'], 'List': tasklist['title'],
                                                     'Due': label(due) if due else '—', 'Notes': body.get('notes', '—')})],
                          notes=[] if args.list_name.strip() else [f"No list named: using your first list, '{tasklist['title']}'."])
        return preview, {'list_id': tasklist['id'], 'list': tasklist['title'], 'body': body}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Insert the task."""
        if 'new' not in selected:
            return ['Nothing added.']
        ctx.google('tasks', 'v1').tasks().insert(tasklist=payload['list_id'], body=payload['body']).execute()
        return [f'Added "{payload["body"]["title"]}" to {payload["list"]}.']
