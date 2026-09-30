"""Standard tasks that keep each Todoist project's Someday section right, in both directions.

A project keeps its wishes in a folded 'Someday' section. Once the user gives one a horizon label (this_week, this_month,
this_year) it is planned and moves up to the top of its project; a task at the top without such a label and without a
date is a wish again and moves back into Someday.
"""

from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs

SOMEDAY = 'someday'
# CLAUDE> the labels that make a Someday task planned; next_year stays a wish for now
HORIZONS = ('this_week', 'this_month', 'this_year')


class PromoteArgs(TaskArgs):
    """Nothing to fill: every planned task in a Someday section is found in code."""


def _state(task: dict) -> list:
    """What decides the move: the task, where it is and its labels."""
    return [task.get('content', ''), task.get('section_id'), sorted(task.get('labels', []))]


class PromotePlanned(StandardTask):
    """Move planned tasks out of Someday."""

    id = 'promote_planned'
    name = 'Move planned tasks out of Someday'
    description = ('Finds the tasks in a Someday section that have a this_week, this_month or this_year label and moves each to '
                   'the top of its project: they are planned now, not someday.')
    example = 'move my planned tasks out of someday'
    Args = PromoteArgs

    def resolve(self, args: PromoteArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the planned tasks still in a Someday section; a subtask goes along with its parent."""
        todoist = ctx.todoist()
        projects = {p['id']: p['name'] for p in todoist.projects()}
        someday = {s['id'] for s in todoist.sections() if s['name'].strip().casefold() == SOMEDAY}
        planned = [t for t in todoist.open_tasks() if t.get('section_id') in someday and not t.get('parent_id')
                   and any(h in t.get('labels', []) for h in HORIZONS)]
        if not planned:
            # CLAUDE> a tidy-up with nothing to tidy is a normal answer, not an error
            return Preview(summary=f"Nothing to move: no task in a Someday section has a {', '.join(HORIZONS[:-1])} or "
                                   f'{HORIZONS[-1]} label.', columns=[], rows=[], read_only=True), {'targets': {}}
        rows = [Row(id=t['id'], cells={'Task': t.get('content') or '(untitled)', 'Project': projects.get(t['project_id'], '?'),
                                       'Horizon': next(h for h in HORIZONS if h in t['labels'])}) for t in planned]
        targets = {t['id']: {'project_id': t['project_id'], 'project': projects.get(t['project_id'], '?'), 'state': _state(t),
                             'title': t.get('content', '')} for t in planned}
        preview = Preview(summary=f'Move {len(rows)} planned task(s) out of Someday to the top of their project.',
                          columns=['Task', 'Project', 'Horizon'], rows=rows)
        return preview, {'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Move each selected task to its project's top, skipping any that changed since the preview."""
        todoist, results = ctx.todoist(), []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            if _state(todoist.task(task_id)) != target['state']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            todoist.move(task_id, target['project_id'])
            # CLAUDE> said only once Todoist shows the task out of its section
            if todoist.task(task_id).get('section_id'):
                results.append(f'Todoist did not move "{target["title"]}" out of Someday; drag it up in {target["project"]}.')
                continue
            results.append(f'Moved "{target["title"]}" up in {target["project"]}.')
        return results


def _unplanned(task: dict) -> bool:
    """At the top of a project (no section, no parent), with no horizon label and no date: a wish."""
    return (not task.get('section_id') and not task.get('parent_id') and not task.get('due')
            and not any(h in task.get('labels', []) for h in HORIZONS))


class DemoteUnplanned(StandardTask):
    """Move unplanned tasks back into Someday."""

    id = 'demote_unplanned'
    name = 'Move unplanned tasks into Someday'
    description = ('Finds the tasks at the top of a project that have no this_week, this_month or this_year label and no date, '
                   "and moves each into its project's Someday section: they are wishes, not planned work.")
    example = 'move unplanned tasks back to someday'
    Args = PromoteArgs

    def resolve(self, args: PromoteArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the unplanned tasks of projects that have a Someday section; the Inbox has none and is left alone."""
        todoist = ctx.todoist()
        projects = {p['id']: p['name'] for p in todoist.projects()}
        someday = {s['project_id']: s['id'] for s in todoist.sections() if s['name'].strip().casefold() == SOMEDAY}
        wishes = [t for t in todoist.open_tasks() if t['project_id'] in someday and _unplanned(t)]
        if not wishes:
            return Preview(summary='Nothing to move: every task at the top of a project has a this_ label or a date.',
                           columns=[], rows=[], read_only=True), {'targets': {}}
        rows = [Row(id=t['id'], cells={'Task': t.get('content') or '(untitled)', 'Project': projects.get(t['project_id'], '?')})
                for t in wishes]
        targets = {t['id']: {'section_id': someday[t['project_id']], 'project': projects.get(t['project_id'], '?'),
                             'state': _state(t), 'title': t.get('content', '')} for t in wishes}
        preview = Preview(summary=f'Move {len(rows)} unplanned task(s) into the Someday section of their project.',
                          columns=['Task', 'Project'], rows=rows)
        return preview, {'targets': targets}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Move each selected task into its project's Someday, skipping any that changed since the preview."""
        todoist, results = ctx.todoist(), []
        for task_id, target in payload['targets'].items():
            if task_id not in selected:
                continue
            if _state(todoist.task(task_id)) != target['state']:
                results.append(f'Skipped "{target["title"]}": it changed since the preview.')
                continue
            todoist.move_to_section(task_id, target['section_id'])
            results.append(f'Moved "{target["title"]}" into Someday in {target["project"]}.')
        return results
