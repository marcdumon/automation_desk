"""Standard task: move tasks to another list."""

from datetime import date

from googleapiclient.errors import HttpError
from pydantic import Field

from automation_desk.dates import label
from automation_desk.groups.base import Context, Preview, Row, StandardTask, match_name
from automation_desk.groups.tasks.client import tasklists, tasks_in
from automation_desk.groups.tasks.select import SelectionArgs, describe, select


class MoveTasksArgs(SelectionArgs):
    """Which tasks, and the list they go to."""

    to_list: str = Field(description='The list the tasks go to, as the user named it.')


class MoveTasks(StandardTask):
    """Move one task, or many, into another list."""

    id = 'move_tasks'
    name = 'Move tasks to another list'
    description = ('Moves the tasks you describe into another list: completed ones, one task by a word of its title, '
                   'everything due this week, and so on.')
    example = 'move all completed tasks to list Completed'
    Args = MoveTasksArgs

    def resolve(self, args: MoveTasksArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching tasks outside the target list, subtasks first."""
        svc = ctx.google('tasks', 'v1')
        target = match_name(args.to_list, tasklists(svc), 'title', 'task list')
        found = select(args, ctx, exclude_list_id=target['id'])
        chosen = {item.task['id'] for item in found}
        children: dict[str, list[dict]] = {}
        for list_id in {item.list_id for item in found}:
            for task in tasks_in(svc, list_id, with_completed=True):
                if task.get('parent'):
                    children.setdefault(task['parent'], []).append(task)

        rows, moves = [], []
        # CLAUDE> subtasks first, each to the top level of the target, so a parent never drags others along
        for item in sorted(found, key=lambda i: not i.task.get('parent')):
            task = item.task
            left_behind = [c for c in children.get(task['id'], []) if c['id'] not in chosen]
            done_on = task.get('completed')
            rows.append(Row(id=task['id'], selectable=not left_behind, selected=not left_behind,
                            note=f'has {len(left_behind)} subtask(s) that would move along' if left_behind else '',
                            cells={'Task': task.get('title') or '(untitled)', 'From list': item.list_title,
                                   'Completed': label(date.fromisoformat(done_on[:10])) if done_on else '—'}))
            if not left_behind:
                moves.append({'id': task['id'], 'list_id': item.list_id, 'etag': task.get('etag'), 'title': task.get('title', '')})
        preview = Preview(summary=f"Move {len(moves)} task(s) to '{target['title']}'. {describe(args)}",
                          columns=['Task', 'From list', 'Completed'], rows=rows,
                          notes=['Subtasks arrive as top-level tasks. Repeating tasks cannot be moved between lists.'])
        return preview, {'target_id': target['id'], 'target': target['title'], 'moves': moves}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Move each selected task to the target list, skipping any that changed since the preview."""
        svc = ctx.google('tasks', 'v1')
        results = []
        for move in [m for m in payload['moves'] if m['id'] in selected]:
            current = svc.tasks().get(tasklist=move['list_id'], task=move['id']).execute()
            if current.get('etag') != move['etag']:
                results.append(f'Skipped "{move["title"]}": it changed since the preview.')
                continue
            try:
                svc.tasks().move(tasklist=move['list_id'], task=move['id'], destinationTasklist=payload['target_id']).execute()
            except HttpError as error:
                results.append(f'Could not move "{move["title"]}": {error.reason or error}')
                continue
            results.append(f'Moved "{move["title"]}" to {payload["target"]}.')
        return results
