"""When a digest is due: 07:00, catching up at start, never twice the same morning."""

from datetime import datetime

import pytest

from automation_desk.groups.news.schedule import due

from .conftest import TZ


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A local time in September 2026."""
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize(('now', 'last', 'expected'), [
    (at(24, 6, 59), at(23, 7), False),
    (at(24, 7, 0), at(23, 7), True),
    (at(24, 7, 1), at(24, 7, 0), False),
    (at(24, 12, 0), at(22, 7), True),
    (at(24, 12, 0), None, True),
    (at(24, 6, 0), None, True),
])
def test_due(now: datetime, last: datetime | None, expected: bool) -> None:
    assert due(now, last, '07:00') is expected


def test_restarts_at_0659_and_0701_give_one_digest() -> None:
    last = at(23, 7)
    assert not due(at(24, 6, 59), last, '07:00')
    assert due(at(24, 7, 1), last, '07:00')
    assert not due(at(24, 7, 2), at(24, 7, 1), '07:00')
