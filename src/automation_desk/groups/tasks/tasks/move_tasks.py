"""Standard task: move tasks to another list."""

from pydantic import Field

from automation_desk.groups.base import Context, Preview, Row, StandardTask, match_name
from automation_desk.groups.tasks.client import task_state, when
from automation_desk.groups.tasks.select import SelectionArgs, describe, select
from automation_desk.todoist import TodoistError


class MoveTasksArgs(SelectionArgs):
    """Which tasks, and the list they go to."""

    to_list: str = Field(description='The list the tasks go to, as the user named it.')


class MoveTasks(StandardTask):
    """Move one task, or many, into another list."""

    id = 'move_tasks'
    name = 'Move tasks to another list'
    description = ('Moves the tasks you describe into another list: one task by a word of its title, everything planned this '
                   'week, and so on. Repeating tasks move with their repeat; subtasks move with their parent.')
    example = 'move all tasks with date later than today from list Today to list This week'
    Args = MoveTasksArgs

    def resolve(self, args: MoveTasksArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching tasks outside the target list; a subtask whose parent moves too goes along with it."""
        todoist = ctx.todoist()
        target = match_name(args.to_list, todoist.projects(), 'name', 'list')
        found = select(args, ctx, exclude_list_id=target['id'])
        chosen = {item.task['id']: item.task for item in found}
        children = {}
        for task in todoist.open_tasks():
            if task.get('parent_id'):
                children[task['parent_id']] = children.get(task['parent_id'], 0) + 1
        rows, moves = [], []
        for item in found:
            task = item.task
            parent = chosen.get(task.get('parent_id') or '')
            note = (f'moves along with {parent.get("content", "its parent")}' if parent
                    else f'its {children[task["id"]]} subtask(s) move along' if children.get(task['id']) else '')
            rows.append(Row(id=task['id'], selected=not parent, note=note,
                            cells={'Task': task.get('content') or '(untitled)', 'From list': item.list_title, 'Date': when(task)}))
            if not parent:
                moves.append({'id': task['id'], 'state': task_state(task), 'title': task.get('content', '')})
        preview = Preview(summary=f"Move {sum(r.selected for r in rows)} task(s) to '{target['name']}'. {describe(args)}",
                          columns=['Task', 'From list', 'Date'], rows=rows)
        return preview, {'target_id': target['id'], 'target': target['name'], 'moves': moves}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Move each selected task to the target list, skipping any that changed since the preview."""
        todoist, results = ctx.todoist(), []
        for move in [m for m in payload['moves'] if m['id'] in selected]:
            if task_state(todoist.task(move['id'])) != move['state']:
                results.append(f'Skipped "{move["title"]}": it changed since the preview.')
                continue
            try:
                todoist.move(move['id'], payload['target_id'])
            except TodoistError as error:
                results.append(f'Could not move "{move["title"]}": {error}')
                continue
            results.append(f'Moved "{move["title"]}" to {payload["target"]}.')
        return results
