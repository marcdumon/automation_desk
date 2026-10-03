"""Stopping a long action from its page: the page asks, the action looks between two steps and ends with what it has."""

import re
import threading

# CLAUDE> the actions a page can stop: saving the News sites, making a digest, adding sites from calendar events, checking
# watched sites; and each research by its own id ('research-12')
ACTIONS = frozenset({'news-sites', 'news-digest', 'watch-from-events', 'watch-check'})

_requested: set[str] = set()
_lock = threading.Lock()


def begin(action: str) -> None:
    """An action starts: an earlier Stop no longer counts."""
    with _lock:
        _requested.discard(action)


def request(action: str) -> None:
    """The user pressed Stop."""
    with _lock:
        _requested.add(action)


def requested(action: str) -> bool:
    """Whether the user asked the action to stop."""
    with _lock:
        return action in _requested


def stoppable(action: str) -> bool:
    """Whether a page may stop this action: one of ACTIONS, or one research by its id."""
    return action in ACTIONS or re.fullmatch(r'research-\d+', action) is not None
