"""Standard task: delete tasks."""

from automation_desk.groups.base import Context, Preview, Row, StandardTask
from automation_desk.groups.tasks.client import repeat_rule, task_state, when
from automation_desk.groups.tasks.select import SelectionArgs, describe, select


class DeleteTasks(StandardTask):
    """Remove one task, or many."""

    id = 'delete_tasks'
    name = 'Delete tasks'
    description = 'Deletes the tasks you describe: one task by a word of its title, completed ones in a list, ...'
    example = 'delete the completed tasks in list Inbox'
    Args = SelectionArgs

    def resolve(self, args: SelectionArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching tasks, noting subtasks a delete takes along; a repeating task comes unticked."""
        found = select(args, ctx)
        children = {}
        for task in ctx.todoist().open_tasks():
            if task.get('parent_id'):
                children[task['parent_id']] = children.get(task['parent_id'], 0) + 1
        rows = []
        for item in found:
            task, repeats = item.task, repeat_rule(item.task) and not item.task.get('checked')
            note = (f"also deletes its {children[task['id']]} subtask(s)" if children.get(task['id'])
                    else f'repeats {repeat_rule(task)}: deleting ends it' if repeats else '')
            rows.append(Row(id=task['id'], selected=not repeats, note=note,
                            cells={'Task': task.get('content') or '(untitled)', 'List': item.list_title,
                                   'State': 'done' if task.get('checked') else 'open', 'Date': when(task)}))
        targets = {i.task['id']: {'state': task_state(i.task), 'title': i.task.get('content', ''), 'done': bool(i.task.get('checked'))}
                   for i in found}
        return Preview(summary=f'Delete {sum(r.selected for r in rows)} task(s). {describe(args)}',
                       columns=['Task', 'List', 'State', 'Date'], rows=rows,
                       notes=['Deleted tasks cannot be brought back from Todoist.']), {'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Delete each selected task, skipping an open one that changed since the preview."""
        todoist, results = ctx.todoist(), []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            # CLAUDE> a completed task cannot change any more; only open ones are checked again
            if not target['done'] and task_state(todoist.task(task_id)) != target['state']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            todoist.delete(task_id)
            results.append(f'Deleted "{target["title"]}".')
        return results
