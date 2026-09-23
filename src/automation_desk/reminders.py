"""Reminders the app shows until they are done: a popup when due, snoozable, some closing themselves.

Kept in data/reminders.json inside the project. A reminder with a `check` is marked done by the app as soon as the check
passes, e.g. when the OpenRouter key in .env is no longer the one the reminder was written about.
"""

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from automation_desk.config import ROOT, config

STORE = ROOT / 'data' / 'reminders.json'
_lock = threading.Lock()


def _now() -> datetime:
    """Local time with offset."""
    return datetime.now().astimezone()


def key_fingerprint() -> str:
    """A short hash of the current OpenRouter key, never the key itself."""
    return hashlib.sha256(config().openrouter_key.encode()).hexdigest()[:12]


@dataclass
class Reminder:
    """Something the user asked to be reminded of."""

    id: str
    title: str
    steps: list[str]
    created: str = field(default_factory=lambda: _now().isoformat(timespec='seconds'))
    snoozed_until: str = ''
    done_at: str = ''
    done_how: str = ''
    # CLAUDE> 'openrouter_key_changed:<fingerprint>' closes the reminder once the key differs from that fingerprint
    check: str = ''


def _load() -> dict[str, Reminder]:
    """All reminders by id."""
    if not STORE.exists():
        return {}
    return {r['id']: Reminder(**r) for r in json.loads(STORE.read_text())}


def _save(reminders: dict[str, Reminder]) -> None:
    """Write all reminders."""
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps([asdict(r) for r in reminders.values()], ensure_ascii=False, indent=2))


def _check_passes(reminder: Reminder) -> bool:
    """Whether the reminder's own check says it is done."""
    kind, _, value = reminder.check.partition(':')
    return kind == 'openrouter_key_changed' and bool(config().openrouter_key) and key_fingerprint() != value


def add(reminder: Reminder) -> None:
    """Add a reminder unless one with the same id exists."""
    with _lock:
        reminders = _load()
        reminders.setdefault(reminder.id, reminder)
        _save(reminders)


def open_reminders() -> list[dict]:
    """Reminders not done yet, each with whether it is due now; those whose check passes are closed first."""
    with _lock:
        reminders = _load()
        for reminder in reminders.values():
            if not reminder.done_at and reminder.check and _check_passes(reminder):
                reminder.done_at, reminder.done_how = _now().isoformat(timespec='seconds'), 'detected by the app'
        _save(reminders)
    now = _now()
    return [{**asdict(r), 'due': not r.snoozed_until or datetime.fromisoformat(r.snoozed_until) <= now}
            for r in reminders.values() if not r.done_at]


def snooze(reminder_id: str, days: int) -> None:
    """Do not show the reminder again for `days` days."""
    with _lock:
        reminders = _load()
        reminders[reminder_id].snoozed_until = (_now() + timedelta(days=days)).isoformat(timespec='seconds')
        _save(reminders)


def done(reminder_id: str) -> None:
    """Mark a reminder done by hand."""
    with _lock:
        reminders = _load()
        reminders[reminder_id].done_at, reminders[reminder_id].done_how = _now().isoformat(timespec='seconds'), 'by you'
        _save(reminders)
