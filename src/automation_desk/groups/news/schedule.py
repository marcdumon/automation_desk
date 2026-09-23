"""The daily digest schedule: due at the digest time, caught up at app start, checked every minute in a background thread."""

import logging
import threading
from datetime import datetime, time

from automation_desk.config import config
from automation_desk.groups.news import digest, store

log = logging.getLogger(__name__)
CHECK_SECONDS = 60
_started = threading.Event()


def due(now: datetime, last: datetime | None, at: str) -> bool:
    """Whether a digest should be made: none yet, or the latest is older than the most recent digest time that has passed."""
    if last is None:
        return True
    hour, minute = (int(part) for part in at.split(':'))
    todays = datetime.combine(now.date(), time(hour, minute), now.tzinfo)
    return now >= todays and last < todays


def _loop() -> None:
    """Check every minute; make a digest when due. Unattended, so never wait on the browser."""
    while True:
        try:
            if store.sources() and due(datetime.now().astimezone(), store.latest_made_at(), config().news.digest_time):
                digest.make_digest('scheduled', allow_browser=False)
        except Exception:
            log.exception('the scheduled news digest failed')
        threading.Event().wait(CHECK_SECONDS)


def start() -> None:
    """Start the schedule once per process."""
    if not _started.is_set():
        _started.set()
        threading.Thread(target=_loop, name='news-digest', daemon=True).start()
