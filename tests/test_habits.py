"""Habits: the user's own habit tracker, outside Todoist. Wednesday 30 Sep 2026 is 'today' unless a test says otherwise."""

from datetime import date, datetime, timedelta

import pytest

from automation_desk import habits

from .conftest import TZ

WED = date(2026, 9, 30)


def test_which_habits_are_due_on_a_day() -> None:
    assert habits.is_due('daily', WED) and habits.is_due('weekdays', WED)
    assert not habits.is_due('weekdays', date(2026, 10, 3)), 'Saturday'
    assert habits.is_due('days:mon,wed', WED) and not habits.is_due('days:mon,wed', date(2026, 10, 1))
    assert habits.is_due('weekly:2', WED, done_this_week=1) and not habits.is_due('weekly:2', WED, done_this_week=2)


def test_today_shows_only_the_habits_due_today_with_their_tick() -> None:
    weight = habits.add('Measure: Weight', 'daily', created=WED - timedelta(days=10))
    habits.add('Goto: Jimms', 'weekdays', created=WED - timedelta(days=10))
    habits.check(weight, WED, True)
    sat = habits.overview(date(2026, 10, 3))
    assert [h['name'] for h in sat['today']] == ['Measure: Weight'], 'no Jimms on Saturday'
    wed = habits.overview(WED)
    assert [(h['name'], h['done']) for h in wed['today']] == [('Measure: Weight', True), ('Goto: Jimms', False)]


def test_streaks_count_due_days_and_today_is_not_yet_a_miss() -> None:
    """Jimms on weekdays: done Mon 21 to Fri 25 and Mon 28, Tue 29; the weekend does not break it; today still open."""
    jimms = habits.add('Goto: Jimms', 'weekdays', created=date(2026, 9, 14))
    for day in (21, 22, 23, 24, 25, 28, 29):
        habits.check(jimms, date(2026, 9, day), True)
    habits.check(jimms, date(2026, 9, 17), True)
    row = next(h for h in habits.overview(WED)['habits'] if h['id'] == jimms)
    assert (row['streak'], row['best']) == (7, 7)
    habits.check(jimms, WED, True)
    assert next(h for h in habits.overview(WED)['habits'] if h['id'] == jimms)['streak'] == 8


def test_the_month_grid_and_share() -> None:
    weight = habits.add('Measure: Weight', 'daily', created=date(2026, 9, 1))
    for day in range(1, 31, 2):
        habits.check(weight, date(2026, 9, day), True)
    row = next(h for h in habits.overview(WED)['habits'] if h['id'] == weight)
    states = {d['day']: d['state'] for d in row['days']}
    assert (states['2026-09-01'], states['2026-09-02'], states['2026-09-29']) == ('done', 'missed', 'done')
    assert states['2026-09-30'] == 'open', 'today, not ticked yet: still open'
    assert row['month_pct'] == 52, '15 of 29 past days (today not counted while open)'


def test_x_times_a_week_counts_weeks() -> None:
    sport = habits.add('Sport', 'weekly:2', created=date(2026, 9, 14))
    for day in (15, 17, 22, 24, 29):
        habits.check(sport, date(2026, 9, day), True)
    row = next(h for h in habits.overview(WED)['habits'] if h['id'] == sport)
    assert row['streak'] == 2, 'two full weeks met; this week (1 of 2) is still running'
    assert [h['name'] for h in habits.overview(WED)['today']] == ['Sport'], 'shown until the week is met'


def test_editing_pausing_and_deleting() -> None:
    weight = habits.add('Measure: Weight', 'daily', created=WED)
    habits.update(weight, name='Weigh: Morning', schedule='days:mon,thu')
    assert habits.overview(WED)['habits'][0]['name'] == 'Weigh: Morning' and habits.overview(WED)['today'] == []
    habits.update(weight, schedule='daily', paused=True)
    assert habits.overview(WED)['today'] == []
    habits.delete(weight)
    assert habits.overview(WED)['habits'] == []
    with pytest.raises(ValueError, match='every day, weekdays'):
        habits.add('x', 'monthly')


def test_the_reminder_fires_once_after_its_time_when_habits_are_open() -> None:
    weight = habits.add('Measure: Weight', 'daily', created=WED)
    at = datetime(2026, 9, 30, 21, 5, tzinfo=TZ)
    assert habits.reminder_text(at) is None, 'no reminder set'
    habits.set_reminder('21:00')
    assert habits.reminder_text(at.replace(hour=20)) is None
    assert habits.reminder_text(at) == 'Habits: 1 still open today (Measure: Weight)'
    habits.mark_reminded(at.date())
    assert habits.reminder_text(at) is None, 'once a day'
    habits.check(weight, date(2026, 10, 1), True)
    assert habits.reminder_text(at + timedelta(days=1)) is None, 'all done'
