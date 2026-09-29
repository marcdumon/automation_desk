"""The Tasks group's standard tasks against a stand-in Todoist, all sharing one selection. Today is Tuesday 22 Sep 2026."""

import pytest

from automation_desk.groups.base import UserError
from automation_desk.groups.tasks.select import SelectionArgs
from automation_desk.groups.tasks.tasks.add_task import AddTask, AddTaskArgs
from automation_desk.groups.tasks.tasks.change_dates import ChangeDates, ChangeDatesArgs
from automation_desk.groups.tasks.tasks.complete_tasks import CompleteTasks
from automation_desk.groups.tasks.tasks.delete_tasks import DeleteTasks
from automation_desk.groups.tasks.tasks.move_tasks import MoveTasks, MoveTasksArgs

from .conftest import FakeTodoist

PROJECTS = [{'id': 'P0', 'name': 'Inbox'}, {'id': 'P1', 'name': 'Today'}, {'id': 'P2', 'name': 'This week'}]


def task(task_id: str, content: str, project: str = 'P1', day: str | None = None, **extra: object) -> dict:
    """An open task as Todoist gives it; `day` as 'YYYY-MM-DD' or with a time 'YYYY-MM-DDTHH:MM:SS'."""
    due = {'date': day, 'is_recurring': False, 'string': day} if day else None
    return {'id': task_id, 'content': content, 'description': '', 'project_id': project, 'parent_id': None, 'due': due,
            'checked': False, **extra}


def weekly(task_id: str, content: str, day: str, project: str = 'P1') -> dict:
    """A repeating task."""
    return task(task_id, content, project, due={'date': day, 'is_recurring': True, 'string': 'every saturday'})


def selection(**kwargs: object) -> dict:
    """Selection fields: open tasks in all lists unless overridden."""
    return {'status': 'ok', 'message': '', 'list_names': [], 'which': 'open', 'title_contains': [], 'due_period': '', **kwargs}


def calls(fake: FakeTodoist, name: str) -> list[tuple]:
    """The changes of one kind the tasks made."""
    return [c[1:] for c in fake.calls if c[0] == name]


def test_change_dates_of_tasks_due_later_than_today(make_ctx) -> None:
    """'change the date of the tasks in Today due later than today to friday'."""
    fake = FakeTodoist(PROJECTS, [task('a', 'Zalando', day='2026-09-25'), task('b', 'Old', day='2026-09-20'),
                                  task('c', 'Call', day='2026-09-24T10:30:00'), task('d', 'Elsewhere', 'P2', '2026-09-25')])
    ctx = make_ctx(fake, 'x')
    args = ChangeDatesArgs(**selection(list_names=['today'], due_period='later than today'), new_due='friday')
    preview, payload = ChangeDates().resolve(args, ctx)
    assert {r.cells['Task']: r.cells['New date'] for r in preview.rows} == {'Zalando': 'Fri 25 Sep 2026',
                                                                            'Call': 'Fri 25 Sep 2026 10:30'}
    assert next(r for r in preview.rows if r.cells['Task'] == 'Zalando').note == 'already on that date'
    ChangeDates().execute(payload, {'a', 'c'}, ctx)
    assert calls(fake, 'update') == [('c', {'due_datetime': '2026-09-25T08:30:00Z'})], 'a timed task keeps its time'


def test_a_repeating_task_comes_unticked_where_a_change_would_end_its_repeat(make_ctx) -> None:
    fake = FakeTodoist(PROJECTS, [weekly('q', 'Quick Clean', '2026-09-26'), task('z', 'Zalando', day='2026-09-26')])
    ctx = make_ctx(fake, 'x')
    preview, _ = ChangeDates().resolve(ChangeDatesArgs(**selection(), new_due='friday'), ctx)
    assert {r.cells['Task']: (r.selected, r.note) for r in preview.rows} == {
        'Quick Clean': (False, 'repeats every saturday: a new date here would end the repeat'), 'Zalando': (True, '')}
    preview, _ = DeleteTasks().resolve(SelectionArgs(**selection()), ctx)
    assert {r.cells['Task']: r.selected for r in preview.rows} == {'Quick Clean': False, 'Zalando': True}
    assert next(r.note for r in preview.rows if r.cells['Task'] == 'Quick Clean') == 'repeats every saturday: deleting ends it'


def test_move_tasks_several_of_one_list_and_repeating_ones_too(make_ctx) -> None:
    """Todoist moves repeating tasks and subtasks along with their parent; a subtask of a moved parent is not moved twice."""
    fake = FakeTodoist(PROJECTS, [task('a', 'A', day='2026-09-23'), weekly('q', 'Quick Clean', '2026-09-26'),
                                  task('c', 'Sub of A', parent_id='a'), task('x', 'Other list', 'P2')])
    ctx = make_ctx(fake, 'x')
    preview, payload = MoveTasks().resolve(MoveTasksArgs(**selection(list_names=['today']), to_list='this week'), ctx)
    assert {r.cells['Task']: (r.selected, r.note) for r in preview.rows} == {
        'A': (True, 'its 1 subtask(s) move along'), 'Quick Clean': (True, ''), 'Sub of A': (False, 'moves along with A')}
    assert preview.summary.startswith("Move 2 task(s) to 'This week'.")
    MoveTasks().execute(payload, {'a', 'q'}, ctx)
    assert calls(fake, 'move') == [('a', 'P2'), ('q', 'P2')]


def test_a_task_changed_since_the_preview_is_skipped(make_ctx) -> None:
    tasks = [task('a', 'A'), task('b', 'B')]
    fake = FakeTodoist(PROJECTS, tasks)
    ctx = make_ctx(fake, 'x')
    _preview, payload = MoveTasks().resolve(MoveTasksArgs(**selection(), to_list='this week'), ctx)
    tasks[1]['content'] = 'B, renamed'
    results = MoveTasks().execute(payload, {'a', 'b'}, ctx)
    assert results == ['Moved "A" to This week.', 'Skipped "B": it changed since the preview.']


def test_complete_open_tasks_and_delete_with_subtasks(make_ctx) -> None:
    fake = FakeTodoist(PROJECTS, [task('p', 'Parent'), task('c', 'Child', parent_id='p')])
    ctx = make_ctx(fake, 'x')
    preview, payload = CompleteTasks().resolve(SelectionArgs(**selection(title_contains=['parent'])), ctx)
    CompleteTasks().execute(payload, {'p'}, ctx)
    assert calls(fake, 'close') == [('p',)]
    preview, _ = DeleteTasks().resolve(SelectionArgs(**selection(title_contains=['parent'])), ctx)
    assert preview.rows[0].note == 'also deletes its 1 subtask(s)'


def test_completed_tasks_come_from_the_last_year(make_ctx) -> None:
    done = [{**task('d', 'Done thing'), 'checked': True, 'completed_at': '2026-09-01T10:00:00Z'}]
    fake = FakeTodoist(PROJECTS, [task('a', 'Open thing')], done)
    ctx = make_ctx(fake, 'x')
    preview, payload = DeleteTasks().resolve(SelectionArgs(**selection(which='completed')), ctx)
    assert [r.cells['Task'] for r in preview.rows] == ['Done thing']
    (_, since, until), = [c for c in fake.calls if c[0] == 'completed']
    assert (since.date().isoformat(), until.date().isoformat()) == ('2025-09-22', '2026-09-22')
    DeleteTasks().execute(payload, {'d'}, ctx)
    assert calls(fake, 'delete') == [('d',)]


def test_overdue_and_list_names_and_nothing_found(make_ctx) -> None:
    fake = FakeTodoist(PROJECTS, [task('a', 'Late', day='2026-09-20'), task('b', 'Soon', day='2026-09-23'), task('c', 'Undated')])
    ctx = make_ctx(fake, 'x')
    preview, _ = CompleteTasks().resolve(SelectionArgs(**selection(which='overdue')), ctx)
    assert [r.cells['Task'] for r in preview.rows] == ['Late']
    with pytest.raises(UserError, match=r"No task matches that\. Looked for open tasks in list 'this week'\."):
        CompleteTasks().resolve(SelectionArgs(**selection(list_names=['this week'])), ctx)
    with pytest.raises(UserError, match='list'):
        CompleteTasks().resolve(SelectionArgs(**selection(list_names=['holidays'])), ctx)


def add_args(**kwargs: str) -> AddTaskArgs:
    """AddTaskArgs with nothing but a title unless overridden."""
    return AddTaskArgs(**{'status': 'ok', 'message': '', 'title': 'call the plumber', 'list_name': '', 'due': '', 'time': '',
                          'repeat': '', 'notes': '', **kwargs})


def test_add_a_task_with_date_time_or_repeat(make_ctx) -> None:
    fake = FakeTodoist(PROJECTS, [])
    ctx = make_ctx(fake, 'add call the plumber to Today friday at 10')
    preview, payload = AddTask().resolve(add_args(list_name='today', due='friday', time='at 10', notes='leak'), ctx)
    assert preview.rows[0].cells == {'Task': 'call the plumber', 'List': 'Today', 'When': 'Fri 25 Sep 2026 10:00', 'Notes': 'leak'}
    AddTask().execute(payload, {'new'}, ctx)
    _preview, payload = AddTask().resolve(add_args(due='monday'), ctx)
    AddTask().execute(payload, {'new'}, ctx)
    preview, payload = AddTask().resolve(add_args(title='Quick Clean', repeat='every saturday'), ctx)
    assert preview.rows[0].cells['When'] == 'every saturday'
    AddTask().execute(payload, {'new'}, ctx)
    assert calls(fake, 'add') == [
        ({'content': 'call the plumber', 'project_id': 'P1', 'description': 'leak', 'due_datetime': '2026-09-25T08:00:00Z'},),
        ({'content': 'call the plumber', 'project_id': 'P0', 'due_date': '2026-09-28'},),
        ({'content': 'Quick Clean', 'project_id': 'P0', 'due_string': 'every saturday'},),
    ], 'no list named: Inbox'


def test_list_names_match_without_emoji_and_with_and_for_ampersand() -> None:
    """Projects named with an emoji as icon: '👨‍👩‍👧 Friends & Family' is 'friends and family' or 'family'."""
    from automation_desk.groups.base import match_name

    projects = [{'name': 'Inbox'}, {'name': '🏠 Home'}, {'name': '👨‍👩‍👧 Friends & Family'}, {'name': '💶 Admin & Finance'}]
    assert [match_name(w, projects, 'name', 'list')['name'] for w in ('home', 'friends and family', 'Friends & Family',
                                                                        'family', 'admin and finance')] == [
        '🏠 Home', '👨‍👩‍👧 Friends & Family', '👨‍👩‍👧 Friends & Family', '👨‍👩‍👧 Friends & Family', '💶 Admin & Finance']
