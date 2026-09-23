"""The Tasks group's standard tasks against a fake Tasks API, all sharing one selection."""

import pytest

from automation_desk.groups.base import UserError
from automation_desk.groups.tasks.select import SelectionArgs
from automation_desk.groups.tasks.tasks.add_task import AddTask, AddTaskArgs
from automation_desk.groups.tasks.tasks.change_dates import ChangeDates, ChangeDatesArgs
from automation_desk.groups.tasks.tasks.complete_tasks import CompleteTasks
from automation_desk.groups.tasks.tasks.delete_tasks import DeleteTasks
from automation_desk.groups.tasks.tasks.move_tasks import MoveTasks, MoveTasksArgs

from .conftest import FakeGoogle

LISTS = [{'id': 'L1', 'title': 'Today'}, {'id': 'L2', 'title': 'Tomorrow'}, {'id': 'L3', 'title': 'Completed'}]


def tasks_api(tasks: dict[str, list[dict]]) -> FakeGoogle:
    """A fake with the task lists above and the given tasks per list id."""
    def list_tasks(tasklist: str, showCompleted: bool, **_: object) -> dict:
        """tasks.list honouring showCompleted."""
        items = tasks.get(tasklist, [])
        return {'items': [t for t in items if showCompleted or t.get('status') != 'completed']}

    def get(tasklist: str, task: str) -> dict:
        """tasks.get."""
        return next(t for t in tasks[tasklist] if t['id'] == task)

    return FakeGoogle({
        'tasklists.list': lambda **_: {'items': LISTS},
        'tasks.list': list_tasks,
        'tasks.get': get,
        'tasks.patch': lambda **kwargs: {},
        'tasks.move': lambda **kwargs: {},
        'tasks.delete': lambda **kwargs: '',
        'tasks.insert': lambda **kwargs: {},
    })


def selection(**kwargs: str) -> dict:
    """Selection fields: open tasks in all lists unless overridden."""
    return {'status': 'ok', 'message': '', 'list_name': 'all', 'which': 'open', 'title_contains': [], 'due_period': '', **kwargs}


TODAY_LIST = [
    {'id': 'z', 'title': 'Zalando: get measured', 'due': '2026-09-23T00:00:00.000Z', 'etag': 'e1'},
    {'id': 'p', 'title': 'Prepare CS329Z', 'due': '2026-09-20T00:00:00.000Z', 'etag': 'e2'},
    {'id': 'd', 'title': 'Old Zalando return', 'status': 'completed', 'completed': '2026-09-01T10:00:00.000Z', 'etag': 'e3'},
]


def test_one_task_by_a_word_of_its_title(make_ctx) -> None:
    fake = tasks_api({'L1': TODAY_LIST})
    ctx = make_ctx(fake, 'change the date of the Zalando task in list Today to tomorrow')
    args = ChangeDatesArgs(**selection(list_name='Today', title_contains=['zalando']), new_due='tomorrow')
    preview, payload = ChangeDates().resolve(args, ctx)
    assert [r.cells['Task'] for r in preview.rows] == ['Zalando: get measured'], 'the completed Zalando task is not open'
    assert preview.rows[0].cells['New date'] == 'Wed 23 Sep 2026' and not preview.rows[0].selectable, 'already due then'

    args = ChangeDatesArgs(**selection(list_name='Today', title_contains=['zalando']), new_due='today+2')
    preview, payload = ChangeDates().resolve(args, ctx)
    ChangeDates().execute(payload, {'z'}, ctx)
    assert [kw['body'] for name, kw in fake.calls if name == 'tasks.patch'] == [{'due': '2026-09-24T00:00:00.000Z'}]


def test_overdue_and_due_period_and_all_lists(make_ctx) -> None:
    fake = tasks_api({'L1': TODAY_LIST, 'L2': [{'id': 'm', 'title': 'Mail Anna', 'due': '2026-09-19T00:00:00.000Z', 'etag': 'e'}]})
    ctx = make_ctx(fake, 'x')
    preview, _ = CompleteTasks().resolve(SelectionArgs(**selection(which='overdue')), ctx)
    assert {(r.cells['Task'], r.cells['List']) for r in preview.rows} == {('Prepare CS329Z', 'Today'), ('Mail Anna', 'Tomorrow')}
    preview, _ = CompleteTasks().resolve(SelectionArgs(**selection(due_period='this week')), ctx)
    assert [r.cells['Task'] for r in preview.rows] == ['Zalando: get measured'], 'due in this Monday-Sunday week (21-27 Sep)'


def test_nothing_matching_says_what_was_looked_for(make_ctx) -> None:
    with pytest.raises(UserError, match="Looked for open tasks in list 'Today' with 'dentist' in the title"):
        ctx = make_ctx(tasks_api({'L1': TODAY_LIST}), '')
        CompleteTasks().resolve(SelectionArgs(**selection(list_name='Today', title_contains=['dentist'])), ctx)


def test_move_completed_to_a_list_subtasks_first_never_open_parents(make_ctx) -> None:
    fake = tasks_api({
        'L1': [{'id': 'p', 'title': 'Parent', 'status': 'completed', 'etag': '1'},
               {'id': 'c', 'title': 'Child', 'parent': 'p', 'status': 'completed', 'etag': '2'},
               {'id': 'q', 'title': 'Busy parent', 'status': 'completed', 'etag': '4'},
               {'id': 'r', 'title': 'Open child', 'parent': 'q', 'etag': '5'}],
        'L3': [{'id': 'z', 'title': 'Already there', 'status': 'completed', 'etag': '6'}],
    })
    ctx = make_ctx(fake, 'move all completed tasks to list Completed')
    preview, payload = MoveTasks().resolve(MoveTasksArgs(**selection(which='completed'), to_list='Completed'), ctx)
    assert [(r.cells['Task'], r.selectable) for r in preview.rows] == [('Child', True), ('Parent', True), ('Busy parent', False)]
    MoveTasks().execute(payload, {'c', 'p', 'q'}, ctx)
    assert [(kw['task'], kw['destinationTasklist']) for name, kw in fake.calls if name == 'tasks.move'] == [('c', 'L3'), ('p', 'L3')]


def test_delete_notes_subtasks_and_add_resolves_list_and_date(make_ctx) -> None:
    fake = tasks_api({'L1': [{'id': 'p', 'title': 'Parent', 'etag': '1'}, {'id': 'c', 'title': 'Child', 'parent': 'p', 'etag': '2'}]})
    ctx = make_ctx(fake, 'add a task call the plumber to list Tomorrow due friday')
    preview, _ = DeleteTasks().resolve(SelectionArgs(**selection(title_contains=['parent'])), ctx)
    assert preview.rows[0].note == 'also deletes its 1 subtask(s)'

    args = AddTaskArgs(status='ok', message='', title='call the plumber', list_name='tomorrow', due='friday', notes='')
    preview, payload = AddTask().resolve(args, ctx)
    AddTask().execute(payload, {'new'}, ctx)
    assert [kw for name, kw in fake.calls if name == 'tasks.insert'] == [
        {'tasklist': 'L2', 'body': {'title': 'call the plumber', 'due': '2026-09-25T00:00:00.000Z'}}]


def test_several_tasks_named_in_one_sentence(make_ctx) -> None:
    tasks = [*TODAY_LIST, {'id': 'r', 'title': 'Ramen poetsen (clean windows)', 'due': '2026-09-23T00:00:00.000Z', 'etag': 'e4'}]
    ctx = make_ctx(tasks_api({'L1': tasks}), 'change the date of the Zalando and clean tasks in list Today to tomorrow')
    args = ChangeDatesArgs(**selection(list_name='Today', title_contains=['Zalando', 'clean']), new_due='today+2')
    preview, _ = ChangeDates().resolve(args, ctx)
    assert [r.cells['Task'] for r in preview.rows] == ['Zalando: get measured', 'Ramen poetsen (clean windows)']
    assert "with 'Zalando' or 'clean' in the title" in preview.summary
