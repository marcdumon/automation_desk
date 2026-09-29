"""Standard task: add a task."""

from pydantic import Field

from automation_desk.dates import label, resolve_day, resolve_time
from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, match_name
from automation_desk.groups.tasks.client import utc_moment


class AddTaskArgs(TaskArgs):
    """What the task is, where it goes, and when."""

    title: str = Field(description='The task itself, copied in the language the user wrote it, without the list or date.')
    list_name: str = Field(description="The list it goes in, as the user named it, e.g. 'Today'. Empty when not said.")
    due: str = Field(description="The day it is for as the user said it: 'tomorrow', 'friday', 'in 3 days'. Empty when not "
                                 'said or when the task repeats without a start day. Never a numeric date.')
    time: str = Field(description="The time of day as the user said it: 'at 10', '15:30', '9am'. Empty when not said.")
    repeat: str = Field(description="How it repeats, copied as the user said it: 'every saturday', 'every 2 weeks', 'every "
                                    "workday', 'elke maandag'. Empty when it does not repeat.")
    notes: str = Field(description='Extra details the user gave for the task, copied. Empty when none.')


class AddTask(StandardTask):
    """Create one task."""

    id = 'add_task'
    name = 'Add a task'
    description = 'Adds a task to one of your lists, with an optional date, time, repeat and notes.'
    example = 'add a task call the plumber to list Today friday at 10'
    Args = AddTaskArgs

    def resolve(self, args: AddTaskArgs, ctx: Context) -> tuple[Preview, dict]:
        """Resolve the list, the date and the time; a repeat goes to Todoist in the user's words, which Todoist reads."""
        projects = ctx.todoist().projects()
        inbox = next((p for p in projects if p.get('inbox_project')), projects[0])
        project = match_name(args.list_name, projects, 'name', 'list') if args.list_name.strip() else inbox
        day = resolve_day(args.due, ctx.today, ctx.sentence) if args.due.strip() else None
        clock = resolve_time(args.time) if args.time.strip() else None
        fields = {'content': args.title.strip(), 'project_id': project['id'],
                  **({'description': args.notes.strip()} if args.notes.strip() else {})}
        if args.repeat.strip():
            # CLAUDE> Todoist reads the repeat, as it does in its own app ('every saturday at 9 starting 3 oct')
            fields['due_string'] = ' '.join(filter(None, [args.repeat.strip(), f'at {clock:%H:%M}' if clock else '',
                                                          f'starting {day.isoformat()}' if day else '']))
            shown = fields['due_string']
        elif day and clock:
            fields['due_datetime'] = utc_moment(day, clock, ctx.tz)
            shown = f'{label(day)} {clock:%H:%M}'
        elif day:
            fields['due_date'] = day.isoformat()
            shown = label(day)
        else:
            shown = '—'
        preview = Preview(summary=f"Add a task to '{project['name']}'", columns=['Task', 'List', 'When', 'Notes'],
                          rows=[Row(id='new', cells={'Task': fields['content'], 'List': project['name'], 'When': shown,
                                                     'Notes': fields.get('description', '—')})],
                          notes=[] if args.list_name.strip() else [f"No list named: using '{project['name']}'."])
        return preview, {'fields': fields, 'list': project['name']}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Create the task."""
        if 'new' not in selected:
            return ['Nothing added.']
        ctx.todoist().add(payload['fields'])
        return [f'Added "{payload["fields"]["content"]}" to {payload["list"]}.']
