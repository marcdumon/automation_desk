"""The Tasks group against a stand-in Todoist: tasks planned with a this_ label leave their project's Someday section."""

from automation_desk.groups.tasks.tasks.someday import PromoteArgs, PromotePlanned

from .conftest import FakeTodoist

PROJECTS = [{'id': 'P0', 'name': 'Inbox', 'inbox_project': True}, {'id': 'P1', 'name': '🏠 Home'}, {'id': 'P2', 'name': '💻 Tech'}]
SECTIONS = {'S1': 'Someday', 'S2': 'Someday', 'S3': 'Bel209'}


def task(task_id: str, content: str, project: str, section: str | None, labels: list[str], **extra: object) -> dict:
    """An open task as Todoist gives it."""
    return {'id': task_id, 'content': content, 'description': '', 'project_id': project, 'section_id': section,
            'parent_id': None, 'labels': labels, 'due': None, 'checked': False, **extra}


def planned() -> FakeTodoist:
    """Home and Tech, each with a Someday section; some tasks there have a horizon label."""
    fake = FakeTodoist(PROJECTS, [
        task('a', 'Ramen poetsen', 'P1', 'S1', ['this_week']),
        task('b', 'Keukenkasten renoveren', 'P1', 'S1', []),
        task('c', 'Gordijnen laten maken', 'P1', 'S1', ['next_year']),
        task('d', 'Migrate Hyprland', 'P2', 'S2', ['this_month', 'frog']),
        task('e', 'Sub of Hyprland', 'P2', 'S2', ['this_week'], parent_id='d'),
        task('f', 'Already up', 'P2', None, ['this_week']),
        task('g', 'In another section', 'P1', 'S3', ['this_year']),
    ])
    fake.section_names = SECTIONS
    return fake


def args() -> PromoteArgs:
    """No fields to fill."""
    return PromoteArgs(status='ok', message='')


def test_tasks_planned_with_a_this_label_leave_someday(make_ctx) -> None:
    fake = planned()
    ctx = make_ctx(fake, 'move my planned tasks out of someday')
    preview, payload = PromotePlanned().resolve(args(), ctx)
    assert [(r.cells['Task'], r.cells['Project'], r.cells['Horizon']) for r in preview.rows] == [
        ('Ramen poetsen', '🏠 Home', 'this_week'), ('Migrate Hyprland', '💻 Tech', 'this_month')]
    assert preview.summary == 'Move 2 planned task(s) out of Someday to the top of their project.'
    results = PromotePlanned().execute(payload, {'a', 'd'}, ctx)
    assert [c[1:] for c in fake.calls if c[0] == 'move'] == [('a', 'P1'), ('d', 'P2')], 'a subtask goes along with its parent'
    assert results == ['Moved "Ramen poetsen" up in 🏠 Home.', 'Moved "Migrate Hyprland" up in 💻 Tech.']


def test_a_task_changed_since_the_preview_stays(make_ctx) -> None:
    fake = planned()
    ctx = make_ctx(fake, 'x')
    _preview, payload = PromotePlanned().resolve(args(), ctx)
    fake.tasks[0]['labels'] = []
    results = PromotePlanned().execute(payload, {'a', 'd'}, ctx)
    assert results[0] == 'Skipped "Ramen poetsen": it changed since the preview.'


def test_nothing_to_move_is_a_plain_answer_not_an_error(make_ctx) -> None:
    """Running the tidy-up when everything is in its place is fine: a read-only preview says so."""
    from automation_desk.groups.tasks.tasks.someday import DemoteUnplanned

    fake = FakeTodoist(PROJECTS, [task('b', 'Keukenkasten renoveren', 'P1', 'S1', [])])
    fake.section_names = SECTIONS
    fake.section_projects = {'S1': 'P1', 'S2': 'P2', 'S3': 'P1'}
    preview, _ = PromotePlanned().resolve(args(), make_ctx(fake, 'x'))
    assert (preview.summary, preview.rows, preview.read_only) == (
        'Nothing to move: no task in a Someday section has a this_week, this_month or this_year label.', [], True)
    preview, _ = DemoteUnplanned().resolve(args(), make_ctx(fake, 'x'))
    assert (preview.summary, preview.read_only) == ('Nothing to move: every task at the top of a project has a this_ label or a date.',
                                                    True)


def test_list_names_match_without_emoji_and_with_and_for_ampersand() -> None:
    """Projects named with an emoji as icon: '🧑 Friends & Family' is 'friends and family' or 'family'."""
    from automation_desk.groups.base import match_name

    projects = [{'name': 'Inbox'}, {'name': '🏠 Home'}, {'name': '🧑 Friends & Family'}, {'name': '💶 Admin & Finance'}]
    assert [match_name(w, projects, 'name', 'list')['name'] for w in ('home', 'friends and family', 'Friends & Family',
                                                                        'family', 'admin and finance')] == [
        '🏠 Home', '🧑 Friends & Family', '🧑 Friends & Family', '🧑 Friends & Family', '💶 Admin & Finance']


def test_a_move_todoist_did_not_make_is_said(make_ctx) -> None:
    """The app checks that the task really left Someday before it says so."""
    fake = planned()
    fake.moves_nothing = True
    ctx = make_ctx(fake, 'x')
    _preview, payload = PromotePlanned().resolve(args(), ctx)
    assert PromotePlanned().execute(payload, {'a'}, ctx) == [
        'Todoist did not move "Ramen poetsen" out of Someday; drag it up in 🏠 Home.']


def test_unplanned_tasks_go_back_into_someday(make_ctx) -> None:
    """The opposite: at the top of a project without a this_ label and without a date means a wish again."""
    from automation_desk.groups.tasks.tasks.someday import DemoteUnplanned

    fake = FakeTodoist(PROJECTS, [
        task('a', 'Ramen poetsen', 'P1', None, []),
        task('b', 'Call plumber', 'P1', None, [], due={'date': '2026-09-25', 'is_recurring': False, 'string': 'fri'}),
        task('c', 'Keep planned', 'P1', None, ['this_month']),
        task('d', 'Gordijnen', 'P2', None, ['next_year']),
        task('e', 'Sub', 'P1', None, [], parent_id='a'),
        task('f', 'In Someday already', 'P1', 'S1', []),
        task('g', 'In my own section', 'P1', 'S3', []),
        task('h', 'Inbox thing', 'P0', None, []),
    ])
    fake.section_names = SECTIONS
    fake.section_projects = {'S1': 'P1', 'S2': 'P2', 'S3': 'P1'}
    ctx = make_ctx(fake, 'move unplanned tasks back to someday')
    preview, payload = DemoteUnplanned().resolve(args(), ctx)
    assert [(r.cells['Task'], r.cells['Project']) for r in preview.rows] == [('Ramen poetsen', '🏠 Home'), ('Gordijnen', '💻 Tech')]
    assert preview.summary == 'Move 2 unplanned task(s) into the Someday section of their project.'
    results = DemoteUnplanned().execute(payload, {'a', 'd'}, ctx)
    assert [c[1:] for c in fake.calls if c[0] == 'move_to_section'] == [('a', 'S1'), ('d', 'S2')]
    assert results == ['Moved "Ramen poetsen" into Someday in 🏠 Home.', 'Moved "Gordijnen" into Someday in 💻 Tech.']


def test_titles_get_the_verb_colon_form_the_user_can_edit(make_ctx, monkeypatch) -> None:
    """The model proposes; code skips titles already in form and Inbox links, keeps only answers of the form, and the user
    edits a title in the preview before it is applied."""
    from automation_desk.groups.tasks.tasks import titles as module
    from automation_desk.groups.tasks.tasks.titles import Proposal, Proposals, VerbTitles

    fake = FakeTodoist(PROJECTS, [
        task('a', 'Ramen poetsen', 'P1', None, []), task('b', 'Buy: Smoking', 'P1', None, []),
        task('c', '* **LINK**: [All Emojis](https://emojipedia.org/)', 'P0', None, []), task('d', 'Kitchen', 'P1', None, []),
        task('e', 'Afspraak Tandarts maken', 'P1', None, []),
    ])
    seen = {}

    def ask(system: str, user: str, schema: type, **_: object) -> Proposals:
        """Numbers the titles as sent; 'Kitchen' is a heading, left as it is."""
        seen['user'] = user
        return Proposals(titles=[Proposal(n=1, title='Clean: Ramen'), Proposal(n=2, title='Kitchen'),
                                 Proposal(n=3, title='Book Tandarts')])

    monkeypatch.setattr(module, 'ask', ask)
    ctx = make_ctx(fake, 'give my tasks a verb: title')
    preview, payload = VerbTitles().resolve(args(), ctx)
    assert '1. Ramen poetsen' in seen['user'] and 'Buy: Smoking' not in seen['user'] and 'emojipedia' not in seen['user']
    assert [(r.cells['Title'], r.inputs['New title']) for r in preview.rows] == [('Ramen poetsen', 'Clean: Ramen')], (
        "'Kitchen' unchanged, 'Book Tandarts' has no colon: neither is offered")
    preview, payload = VerbTitles().adjust(payload, {'New title:a': 'Clean: Ramen (binnen)'}, ctx)
    assert preview.rows[0].inputs['New title'] == 'Clean: Ramen (binnen)'
    assert VerbTitles().execute(payload, {'a'}, ctx) == ['Renamed "Ramen poetsen" to "Clean: Ramen (binnen)".']
    assert [c[1:] for c in fake.calls if c[0] == 'update'] == [('a', {'content': 'Clean: Ramen (binnen)'})]


def test_nothing_to_rename_asks_no_model(make_ctx, monkeypatch) -> None:
    from automation_desk.groups.tasks.tasks import titles as module
    from automation_desk.groups.tasks.tasks.titles import VerbTitles

    monkeypatch.setattr(module, 'ask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no model call')))
    fake = FakeTodoist(PROJECTS, [task('b', 'Buy: Smoking', 'P1', None, [])])
    preview, _ = VerbTitles().resolve(args(), make_ctx(fake, 'x'))
    assert (preview.summary, preview.read_only) == ('Nothing to rename: every task title already reads Verb: subject.', True)


def test_tasks_with_a_label_get_a_deadline(make_ctx) -> None:
    """'give all tasks with label this week a deadline friday' (Tuesday 22 Sep 2026): code finds the label and the date."""
    from automation_desk.groups.tasks.tasks.deadlines import DeadlineArgs, LabelDeadline

    fake = FakeTodoist(PROJECTS, [
        task('a', 'Clean: Ramen', 'P1', None, ['this_week']),
        task('b', 'Pay: Rekeningen', 'P2', None, ['this_week'], deadline={'date': '2026-09-30', 'lang': 'en'}),
        task('c', 'Book: Tandarts', 'P1', None, ['this_week'], deadline={'date': '2026-09-25', 'lang': 'en'}),
        task('d', 'Other', 'P1', None, ['this_month']),
    ])
    fake.label_names = ['frog', 'this_week', 'this_month']
    ctx = make_ctx(fake, 'give all tasks with label this week a deadline friday')
    preview, payload = LabelDeadline().resolve(DeadlineArgs(status='ok', message='', label='this week', deadline='friday'), ctx)
    assert [(r.cells['Task'], r.cells['Deadline now'], r.cells['New deadline'], r.selectable, r.note) for r in preview.rows] == [
        ('Clean: Ramen', '—', 'Fri 25 Sep 2026', True, ''),
        ('Pay: Rekeningen', 'Wed 30 Sep 2026', 'Fri 25 Sep 2026', True, 'replaces its deadline'),
        ('Book: Tandarts', 'Fri 25 Sep 2026', 'Fri 25 Sep 2026', False, 'already has that deadline')]
    assert preview.summary == "Set deadline Fri 25 Sep 2026 on 2 task(s) with label @this_week."
    LabelDeadline().execute(payload, {'a', 'b'}, ctx)
    assert [c[1:] for c in fake.calls if c[0] == 'update'] == [('a', {'deadline_date': '2026-09-25'}),
                                                               ('b', {'deadline_date': '2026-09-25'})]
