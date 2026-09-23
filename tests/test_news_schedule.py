"""When a digest is due: 07:00, catching up at start, never twice the same morning."""

from datetime import datetime, timedelta

import pytest

from automation_desk.groups.news import schedule, store
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
    (at(24, 6, 0), at(22, 7), True),
])
def test_due(now: datetime, last: datetime | None, expected: bool) -> None:
    assert due(now, last, '07:00') is expected


def test_restarts_at_0659_and_0701_give_one_digest() -> None:
    last = at(23, 7)
    assert not due(at(24, 6, 59), last, '07:00')
    assert due(at(24, 7, 1), last, '07:00')
    assert not due(at(24, 7, 2), at(24, 7, 1), '07:00')


def test_a_failed_digest_is_retried_an_hour_later(monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://a.be', 'A', 'https://a.be/rss', 'feed')
    calls = []

    def fail(trigger: str, allow_browser: bool) -> int:
        """A digest run that breaks."""
        calls.append(trigger)
        raise RuntimeError('boom')

    monkeypatch.setattr(schedule.digest, 'make_digest', fail)
    monkeypatch.setattr(schedule, '_last_failure', {})
    start = at(24, 7, 0)
    for minutes in (0, 1, 30, 59):
        schedule.tick(start + timedelta(minutes=minutes))
    assert calls == ['scheduled'], 'not again every minute'
    schedule.tick(start + timedelta(minutes=60))
    assert calls == ['scheduled', 'scheduled']
