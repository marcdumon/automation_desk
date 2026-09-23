"""Reminders: due, snoozed, and closed by their own check."""

import pytest

from llm_automation import reminders
from llm_automation.config import config


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Keep reminders out of the real data folder."""
    monkeypatch.setattr(reminders, 'STORE', tmp_path / 'reminders.json')


def test_snooze_hides_until_due_and_done_removes() -> None:
    reminders.add(reminders.Reminder(id='r', title='T', steps=['a']))
    assert [r['due'] for r in reminders.open_reminders()] == [True]
    reminders.snooze('r', 2)
    assert [r['due'] for r in reminders.open_reminders()] == [False]
    reminders.done('r')
    assert reminders.open_reminders() == []


def test_key_reminder_closes_itself_when_the_key_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(config()), 'openrouter_key', property(lambda self: 'old-key'))
    reminders.add(reminders.Reminder(id='k', title='T', steps=[], check=f'openrouter_key_changed:{reminders.key_fingerprint()}'))
    assert len(reminders.open_reminders()) == 1
    monkeypatch.setattr(type(config()), 'openrouter_key', property(lambda self: 'new-key'))
    assert reminders.open_reminders() == []
