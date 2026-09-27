"""Stopping a long action from its page: the page asks, the action looks between two steps and ends with what it has."""

import threading

# CLAUDE> the actions a page can stop: saving the News sites, making a digest, adding sites from calendar events, checking
# watched sites
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
