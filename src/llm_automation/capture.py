"""Pages read through the user's own browser, for sites that refuse programs or build their content with JavaScript.

The app never drives a browser itself. It posts a request here; the Automation desk page, open in the user's browser,
picks it up and hands it to the Automation desk extension (browser_extension/), which opens the page in a background
tab of that same browser, waits for it, returns its HTML and closes the tab. If the site shows a human check, the
extension brings the tab to the front for the user to complete.
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

WAIT_S = 240
# CLAUDE> an open Automation desk page polls every second; silence this long means none is listening
CLAIM_WAIT_S = 15


EXTENSION_DIR = Path(__file__).resolve().parents[2] / 'browser_extension'


class CaptureError(RuntimeError):
    """The page could not be read through the browser. `setup` names a one-time setup step the GUI walks the user through."""

    def __init__(self, message: str, setup: str = '') -> None:
        """Message for the user, plus the setup step that fixes it, if any."""
        super().__init__(message)
        self.setup = setup


@dataclass(frozen=True)
class Captured:
    """A page as the user's browser showed it."""

    url: str
    html: str
    asked_you: bool


@dataclass
class _Request:
    """A page the app is waiting for."""

    url: str
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    claimed: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    result: Captured | None = None
    error: str = ''
    missing_extension: bool = False


class CaptureBroker:
    """Hands page requests to the browser and waits for the answers."""

    def __init__(self) -> None:
        """Start with nothing pending."""
        self._requests: dict[str, _Request] = {}
        self._lock = threading.Lock()

    def request(self, url: str, timeout: float = WAIT_S, claim_timeout: float = CLAIM_WAIT_S) -> Captured:
        """Block until the browser delivered `url`, or fail with a reason the user can act on."""
        req = _Request(url=url)
        with self._lock:
            self._requests[req.id] = req
        try:
            if not req.claimed.wait(claim_timeout):
                raise CaptureError(f'{url} has to be read through your browser, but no Automation desk page picked up the '
                                   f'request within {int(claim_timeout)} s. Reload the Automation desk page (it may be '
                                   'running an older version) and try again.')
            if not req.done.wait(timeout):
                raise CaptureError(f'{url} was not delivered by your browser within {int(timeout)} s. Keep the Automation '
                                   'desk page open in your browser, with the Automation desk extension installed.')
            if req.missing_extension:
                raise CaptureError(f'{url} has to be read through your browser, and the Automation desk reader is not '
                                   'installed in it yet.', setup='extension')
            if req.error:
                raise CaptureError(req.error)
            assert req.result is not None
            return req.result
        finally:
            with self._lock:
                self._requests.pop(req.id, None)

    def claim(self) -> list[dict]:
        """Requests no browser has taken yet; each is handed out once."""
        with self._lock:
            fresh = [r for r in self._requests.values() if not r.claimed.is_set()]
            for r in fresh:
                r.claimed.set()
        return [{'id': r.id, 'url': r.url} for r in fresh]

    def deliver(self, request_id: str, result: Captured | None, error: str = '', missing_extension: bool = False) -> bool:
        """The browser's answer; False when nobody is waiting for it any more."""
        with self._lock:
            req = self._requests.get(request_id)
        if req is None:
            return False
        req.result, req.error, req.missing_extension = result, error, missing_extension
        req.done.set()
        return True


broker = CaptureBroker()


def read_in_browser(url: str) -> tuple[Captured, int]:
    """`url` read through the user's browser, and how long it took in milliseconds."""
    started = time.monotonic()
    captured = broker.request(url)
    return captured, int((time.monotonic() - started) * 1000)
