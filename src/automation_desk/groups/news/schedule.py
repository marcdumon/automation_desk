"""The daily digest schedule: due at the digest time, caught up at app start, checked every minute in a background thread."""

import logging
import threading
from datetime import datetime, time, timedelta

from automation_desk.config import config
from automation_desk.groups.news import digest, store

log = logging.getLogger(__name__)
CHECK_SECONDS = 60
RETRY_AFTER = timedelta(hours=1)
# CLAUDE> when the last scheduled run failed, so a broken run is not repeated every minute
_last_failure: dict[str, datetime] = {}
_started = threading.Event()


def due(now: datetime, last: datetime | None, at: str) -> bool:
    """Whether a digest should be made: none yet, or the latest is older than the most recent digest time that has passed."""
    if last is None:
        return True
    hour, minute = (int(part) for part in at.split(':'))
    slot = datetime.combine(now.date(), time(hour, minute), now.tzinfo)
    if now < slot:
        slot = datetime.combine(now.date() - timedelta(days=1), time(hour, minute), now.tzinfo)
    return last < slot


def tick(now: datetime) -> None:
    """One check: make a digest when due, unattended so never waiting on the browser; after a failure, wait an hour."""
    failed = _last_failure.get('at')
    if failed and now < failed + RETRY_AFTER:
        return
    try:
        if store.sources() and due(now, store.latest_made_at(), config().news.digest_time):
            digest.make_digest('scheduled', allow_browser=False)
        _last_failure.clear()
    except Exception:
        _last_failure['at'] = now
        log.exception('the scheduled news digest failed; trying again in an hour')


def _loop() -> None:
    """Check every minute."""
    while True:
        tick(datetime.now().astimezone())
        threading.Event().wait(CHECK_SECONDS)


def start() -> None:
    """Start the schedule once per process."""
    if not _started.is_set():
        _started.set()
        threading.Thread(target=_loop, name='news-digest', daemon=True).start()
