"""Daily task statistics: a morning snapshot of what is open, what got done and added each day, and what that says."""

from datetime import date, datetime, timedelta

from automation_desk.groups.tasks import stats

from .conftest import TZ, FakeTodoist

PROJECTS = [{'id': 'P0', 'name': 'Inbox', 'inbox_project': True}, {'id': 'P1', 'name': '🏠 Home'},
            {'id': 'P2', 'name': '🧹 Big Cleanup'}]
MORNING = datetime(2026, 9, 30, 7, 30, tzinfo=TZ)


def task(task_id: str, project: str, labels: list[str] | None = None, day: str | None = None, section: str | None = None,
         added: str = '2026-09-29T09:00:00Z') -> dict:
    """An open task."""
    return {'id': task_id, 'content': task_id, 'project_id': project, 'section_id': section, 'parent_id': None,
            'labels': labels or [], 'due': {'date': day, 'is_recurring': False} if day else None, 'added_at': added}


def done(task_id: str, when: str, labels: list[str] | None = None, added: str = '2026-09-28T09:00:00Z') -> dict:
    """A completed task."""
    return {**task(task_id, 'P1', labels, added=added), 'checked': True, 'completed_at': when}


def todoist() -> FakeTodoist:
    """Four open tasks (one overdue, one in Someday, labels) and three completed ones, one of them a frog."""
    fake = FakeTodoist(PROJECTS, [
        task('a', 'P1', ['this_week'], '2026-09-28'), task('b', 'P1', ['this_month']), task('c', 'P2', section='S1'),
        task('d', 'P2', ['next_year'], added='2026-09-30T06:00:00Z'),
    ], [done('x', '2026-09-29T20:00:00Z', ['frog']), done('y', '2026-09-29T21:30:00Z'), done('z', '2026-09-30T06:10:00Z')])
    fake.section_names = {'S1': 'Someday'}
    return fake


def test_the_morning_snapshot_counts_what_is_open_once_a_day() -> None:
    fake = todoist()
    assert stats.take_snapshot(fake, MORNING) is True
    day = stats.days(date(2026, 9, 30), date(2026, 9, 30))[0]
    assert (day['open'], day['overdue'], day['someday']) == (4, 1, 1)
    assert (day['this_week'], day['this_month'], day['this_year'], day['next_year']) == (1, 1, 0, 1)
    assert stats.projects_on(date(2026, 9, 30)) == {'🏠 Home': 2, '🧹 Big Cleanup': 2}
    fake.tasks.pop()
    assert stats.take_snapshot(fake, MORNING + timedelta(hours=3)) is False, 'the morning count stays the one of the morning'
    assert stats.days(date(2026, 9, 30), date(2026, 9, 30))[0]['open'] == 4


def test_no_snapshot_before_seven() -> None:
    assert stats.take_snapshot(todoist(), MORNING.replace(hour=6, minute=59)) is False
    assert stats.days(date(2026, 9, 30), date(2026, 9, 30)) == []


def test_done_and_added_per_local_day_with_the_frog() -> None:
    """Completed times are UTC; 21:30Z on 29 Sep is 23:30 in Brussels, still the 29th, and 06:10Z on the 30th is the 30th."""
    stats.refresh_activity(todoist(), MORNING, days=3)
    got = {d['day']: (d['completed'], d['added'], d['frog']) for d in stats.activity(date(2026, 9, 28), date(2026, 9, 30))}
    assert got == {'2026-09-28': (0, 3, False), '2026-09-29': (2, 3, True), '2026-09-30': (1, 1, False)}


def test_the_overview_the_page_shows() -> None:
    """Big Cleanup went from 6 to 2 in four days: one a day, so empty in two more days."""
    for offset, (open_, cleanup) in enumerate([(10, 6), (9, 5), (8, 4), (7, 3), (6, 2)]):
        day = date(2026, 9, 26) + timedelta(days=offset)
        stats.save_snapshot(day, {'open': open_, 'overdue': 0, 'someday': 0, 'this_week': 0, 'this_month': 0, 'this_year': 0,
                                  'next_year': 0}, {'🧹 Big Cleanup': cleanup, '🏠 Home': open_ - cleanup})
    stats.refresh_activity(todoist(), MORNING, days=5)
    view = stats.overview(date(2026, 9, 30), period=30)
    assert view['kpis']['open'] == {'now': 6, 'week_ago': None, 'first': 10}
    assert view['kpis']['done_7d'] == 3 and view['kpis']['added_7d'] == 7
    assert view['cleanup'] == {'project': '🧹 Big Cleanup', 'open': 2, 'per_day': 1.0, 'empty_on': '2026-10-02'}
    assert [p['project'] for p in view['projects']] == ['🏠 Home', '🧹 Big Cleanup']
    assert len(view['weekdays']) == 7 and view['weekdays'][1]['label'] == 'Tue'
    # CLAUDE> the charts start at the first morning count: days before it have nothing to show
    assert [d['day'] for d in view['series']][:2] == ['2026-09-26', '2026-09-27'] and view['series'][-1]['open'] == 6


def test_the_tasks_set_up_before_the_first_morning_count_are_not_added() -> None:
    """The user put 118 tasks in Todoist on 29 September; the first morning count was the 30th. They are where the
    statistics start, not 118 tasks added in a week ('more added than done'), and no bar of 118."""
    stats.save_snapshot(date(2026, 9, 30), {'open': 119, 'overdue': 0, 'someday': 0, 'this_week': 0, 'this_month': 0,
                                            'this_year': 0, 'next_year': 0}, {'🏠 Home': 119})
    stats.refresh_activity(todoist(), MORNING, days=3)
    view = stats.overview(date(2026, 9, 30), period=30)
    assert [d['day'] for d in view['series']] == ['2026-09-30'] and view['kpis']['added_7d'] == 1
    weekdays = {w['label']: w['average'] for w in view['weekdays']}
    assert weekdays['Wed'] == 1.0 and weekdays['Mon'] is None, 'a weekday without a day counted yet has no average'


def test_the_share_of_the_day_s_planned_tasks_that_got_done() -> None:
    """Planned = dated today in the morning count (not the overdue ones); done = those very tasks completed that day. A
    repeating task keeps its id when done, so it counts too; one done a day late does not count for its planned day."""
    fake = FakeTodoist(PROJECTS, [task('p1', 'P1', day='2026-09-30'), task('p2', 'P1', day='2026-09-30'),
                                  task('p3', 'P1', day='2026-09-30T18:00:00'), task('late', 'P1', day='2026-09-28'),
                                  task('later', 'P1', day='2026-10-02')])
    stats.take_snapshot(fake, MORNING)
    assert stats.days(date(2026, 9, 30), date(2026, 9, 30))[0]['planned'] == 3
    fake.done = [done('p1', '2026-09-30T10:00:00Z'), {**done('p2', '2026-09-30T15:00:00Z'), 'due': {'date': '2026-10-07'}},
                 done('late', '2026-09-30T11:00:00Z'), done('later', '2026-09-30T12:00:00Z')]
    stats.refresh_activity(fake, MORNING.replace(hour=22), days=1)
    view = stats.overview(date(2026, 9, 30), period=30)
    today = view['series'][-1]
    assert (today['planned'], today['planned_done']) == (3, 2) and today['completed'] == 4
    assert view['kpis']['plan_7d'] == {'planned': 3, 'done': 2}
    assert view['weekdays'][2]['plan_pct'] == 67, 'Wednesday: 2 of 3'


def test_a_repeating_task_done_today_counts_as_done() -> None:
    """Clean: living repeats daily: when done, Todoist keeps it open with tomorrow's date and adds one to its completed_count;
    it never shows up among the completed tasks. A postponed task keeps its count, so it does not count as done."""
    living = {**task('living', 'P1', day='2026-09-30'), 'due': {'date': '2026-09-30', 'is_recurring': True}, 'completed_count': 4}
    moved = {**task('moved', 'P1', day='2026-09-30'), 'due': {'date': '2026-09-30', 'is_recurring': True}, 'completed_count': 2}
    fake = FakeTodoist(PROJECTS, [living, moved])
    stats.take_snapshot(fake, MORNING)
    living.update(due={'date': '2026-10-01', 'is_recurring': True}, completed_count=5)
    moved.update(due={'date': '2026-10-01', 'is_recurring': True})
    stats.refresh_activity(fake, MORNING.replace(hour=22), days=1)
    today = stats.overview(date(2026, 9, 30), period=30)['series'][-1]
    assert (today['planned'], today['planned_done']) == (2, 1)


def test_an_older_ledger_gets_the_new_column() -> None:
    """A ledger made before completed_count existed is upgraded on start-up, its rows kept."""
    import sqlite3

    from automation_desk import ledger

    with sqlite3.connect(ledger.DB) as db:
        db.execute('CREATE TABLE task_day_planned (day TEXT NOT NULL, task_id TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, '
                   'PRIMARY KEY (day, task_id))')
        db.execute("INSERT INTO task_day_planned (day, task_id) VALUES ('2026-09-29', 'x')")
    with ledger.connect() as db:
        columns = [r['name'] for r in db.execute('PRAGMA table_info(task_day_planned)')]
        kept = db.execute('SELECT COUNT(*) FROM task_day_planned').fetchone()[0]
    assert 'completed_count' in columns and kept == 1


def test_a_repeating_task_done_a_day_late_does_not_count_for_its_day() -> None:
    weekly = {**task('w', 'P1', day='2026-09-30'), 'due': {'date': '2026-09-30', 'is_recurring': True}, 'completed_count': 1}
    fake = FakeTodoist(PROJECTS, [weekly])
    stats.take_snapshot(fake, MORNING)
    stats.refresh_activity(fake, MORNING.replace(hour=22), days=1)
    weekly.update(due={'date': '2026-10-07', 'is_recurring': True}, completed_count=2)
    stats.refresh_activity(fake, MORNING + timedelta(days=1, hours=3), days=2)
    assert stats.days(date(2026, 9, 30), date(2026, 9, 30))[0]['planned_done'] == 0


def test_the_next_morning_settles_yesterday_s_repeating_tasks() -> None:
    """Done in the evening without opening the page: the next morning's count still credits the day it was planned for."""
    daily = {**task('d', 'P1', day='2026-09-30'), 'due': {'date': '2026-09-30', 'is_recurring': True}, 'completed_count': 7}
    fake = FakeTodoist(PROJECTS, [daily])
    stats.take_snapshot(fake, MORNING)
    daily.update(due={'date': '2026-10-01', 'is_recurring': True}, completed_count=8)
    stats.take_snapshot(fake, MORNING + timedelta(days=1))
    assert stats.days(date(2026, 9, 30), date(2026, 9, 30))[0]['planned_done'] == 1
