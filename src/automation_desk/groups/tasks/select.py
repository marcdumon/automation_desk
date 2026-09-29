"""Which tasks a sentence means: one shared selection for every standard task of the Tasks group.

The model copies the user's words into these fields; code does the matching against the Todoist projects (the lists) and
tasks. Task titles never go to the model.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import Field

from automation_desk.dates import resolve_range
from automation_desk.groups.base import Context, TaskArgs, UserError, match_name
from automation_desk.groups.tasks.client import task_date

# CLAUDE> list names that mean 'every list'
ALL_LISTS = {'', 'all', 'all lists', 'every list', 'any list', 'everywhere'}
NO_PERIOD = {'', 'all', 'any', 'any time', 'anytime', 'always', 'ever'}
# CLAUDE> how far back 'completed tasks' reach: Todoist hands them out three months at a time
COMPLETED_REACH = timedelta(days=365)


class SelectionArgs(TaskArgs):
    """Which tasks: list, state, words in the title and due period, all as the user said them."""

    list_names: list[str] = Field(description="One entry per task list the user names, as named, e.g. ['Today'] for 'in list "
                                              "Today', ['Today', 'This week'] for 'in the today and this week lists' (list NAMES, "
                                              "even when they look like dates). Empty when the user names no list or says all lists.")
    which: Literal['open', 'overdue', 'completed', 'all'] = Field(
        description="'open' = not completed (the default; also what 'all tasks' means); 'overdue' = open and due before "
                    "today; 'completed' = done tasks; 'all' only when the user explicitly includes both open and completed.")
    title_contains: list[str] = Field(description="One entry per task the user names by (part of) its title, copied from "
                                                  "the sentence: ['Zalando'] for 'the Zalando task', ['Zalando', 'clean'] "
                                                  "for 'the Zalando and clean tasks'. A task matches if its title contains "
                                                  'any entry. Empty list when the user means all tasks.')
    due_period: str = Field(description="Only tasks due in this period, as the user said it: 'today', 'this week', "
                                        "'tomorrow to friday', 'later than today', 'before friday'. Empty when not limited. "
                                        "This is the CURRENT due date, never "
                                        'a new one. Never a numeric date.')


@dataclass(frozen=True)
class Selected:
    """One task that matches, with the list it is in."""

    list_id: str
    list_title: str
    task: dict


def _norm(text: str) -> str:
    """Case- and space-insensitive form."""
    return ' '.join(text.split()).casefold()


def lists_for(projects: list[dict], names: list[str]) -> list[dict]:
    """The lists (Todoist projects) a selection covers: the named ones, in the user's order, or all of them."""
    named = [n for n in names if _norm(n) not in ALL_LISTS]
    if not named:
        return projects
    wanted = {match_name(n, projects, 'name', 'list')['id'] for n in named}
    return [project for project in projects if project['id'] in wanted]


def select(args: SelectionArgs, ctx: Context, exclude_list_id: str = '') -> list[Selected]:
    """Every task matching the selection, in list order; `exclude_list_id` skips a list (e.g. a move's target)."""
    todoist = ctx.todoist()
    period: tuple[date, date] | None = None
    if _norm(args.due_period) not in NO_PERIOD:
        period = resolve_range(args.due_period, ctx.today, ctx.sentence)
    words = [w for w in (_norm(t) for t in args.title_contains) if w]
    tasks = todoist.open_tasks() if args.which != 'completed' else []
    if args.which in ('completed', 'all'):
        tasks += todoist.completed(ctx.now - COMPLETED_REACH, ctx.now)

    found = []
    for project in lists_for(todoist.projects(), args.list_names):
        if project['id'] == exclude_list_id:
            continue
        for task in (t for t in tasks if t.get('project_id') == project['id']):
            due = task_date(task)
            if args.which == 'overdue' and (due is None or due >= ctx.today):
                continue
            if words and not any(w in _norm(task.get('content', '')) for w in words):
                continue
            if period and (due is None or not period[0] <= due <= period[1]):
                continue
            found.append(Selected(project['id'], project['name'], task))
    if not found:
        raise UserError('No task matches that. ' + describe(args))
    return found


def describe(args: SelectionArgs) -> str:
    """The selection in words, for summaries and messages."""
    parts = [{'open': 'open', 'overdue': 'overdue', 'completed': 'completed', 'all': 'open and completed'}[args.which]
             + ' tasks']
    named = [n.strip() for n in args.list_names if _norm(n) not in ALL_LISTS]
    lists = ' and '.join(f"'{n}'" for n in named)
    parts.append(f"in {'lists' if len(named) > 1 else 'list'} {lists}" if named else 'in all lists')
    names = [t.strip() for t in args.title_contains if t.strip()]
    if names:
        parts.append('with ' + ' or '.join(f"'{n}'" for n in names) + ' in the title')
    if _norm(args.due_period) not in NO_PERIOD:
        parts.append(f'due {args.due_period.strip()}')
    return 'Looked for ' + ' '.join(parts) + '.'
