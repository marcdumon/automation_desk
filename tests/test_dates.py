"""The date grammar: the only way a date enters the app from a sentence."""

from datetime import date, time

import pytest

from llm_automation.dates import DateExprError, resolve_day, resolve_range, resolve_time, weekday_index

TUESDAY = date(2026, 9, 22)
THURSDAY = date(2026, 9, 24)


@pytest.mark.parametrize(('expr', 'expected'), [
    ('today', date(2026, 9, 22)),
    ('Tomorrow', date(2026, 9, 23)),
    ('day after tomorrow', date(2026, 9, 24)),
    ('yesterday', date(2026, 9, 21)),
    ('today+2', date(2026, 9, 24)),
    ('today + 2', date(2026, 9, 24)),
    ('tomorrow-1', date(2026, 9, 22)),
    ('friday+7', date(2026, 10, 2)),
    ('today+1w', date(2026, 9, 29)),
    ('in 3 days', date(2026, 9, 25)),
    ('in 2 weeks', date(2026, 10, 6)),
    ('2 days ago', date(2026, 9, 20)),
    ('thursday', date(2026, 9, 24)),
    ('tuesday', date(2026, 9, 29)),
    ('this monday', date(2026, 9, 21)),
    ('next thursday', date(2026, 10, 1)),
    ('on friday', date(2026, 9, 25)),
])
def test_day_expressions(expr: str, expected: date) -> None:
    assert resolve_day(expr, TUESDAY) == expected


def test_bare_weekday_on_that_weekday_is_a_week_out() -> None:
    assert resolve_day('thursday', THURSDAY) == date(2026, 10, 1)
    assert resolve_day('this thursday', THURSDAY) == THURSDAY


@pytest.mark.parametrize('expr', ['2026-09-24', '24/09', '24/09/2026', '24.9', '2026'])
def test_numeric_dates_are_rejected(expr: str) -> None:
    with pytest.raises(DateExprError):
        resolve_day(expr, TUESDAY, sentence=f'move it to {expr}')


def test_explicit_date_only_when_typed_by_the_user() -> None:
    assert resolve_day('october 3', TUESDAY, 'put it on october 3 please') == date(2026, 10, 3)
    assert resolve_day('3 oct', TUESDAY, 'on 3 Oct') == date(2026, 10, 3)
    assert resolve_day('march 1', TUESDAY, 'on march 1') == date(2027, 3, 1)
    with pytest.raises(DateExprError, match='not in your sentence'):
        resolve_day('october 3', TUESDAY, 'put it next week')


def test_ranges() -> None:
    assert resolve_range('this week', TUESDAY) == (date(2026, 9, 21), date(2026, 9, 27))
    assert resolve_range('next week', TUESDAY) == (date(2026, 9, 28), date(2026, 10, 4))
    assert resolve_range('this weekend', TUESDAY) == (date(2026, 9, 26), date(2026, 9, 27))
    assert resolve_range('next month', TUESDAY) == (date(2026, 10, 1), date(2026, 10, 31))
    assert resolve_range('next month', date(2026, 12, 5)) == (date(2027, 1, 1), date(2027, 1, 31))
    assert resolve_range('today to next friday', TUESDAY) == (TUESDAY, date(2026, 10, 2))
    assert resolve_range('tomorrow', TUESDAY) == (date(2026, 9, 23), date(2026, 9, 23))


@pytest.mark.parametrize(('expr', 'expected'), [
    ('3pm', time(15)), ('15:00', time(15)), ('20h30', time(20, 30)), ('noon', time(12)), ('12am', time(0)), ('at 9', time(9)),
])
def test_times(expr: str, expected: time) -> None:
    assert resolve_time(expr) == expected


def test_weekday_index_accepts_plurals() -> None:
    assert weekday_index('Fridays') == 4
    with pytest.raises(DateExprError):
        weekday_index('fri-day')
