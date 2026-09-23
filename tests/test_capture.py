"""The capture broker: the app waits, the browser claims once and delivers."""

import threading

import pytest

from automation_desk.capture import CaptureBroker, Captured, CaptureError


def test_request_is_claimed_once_and_delivered() -> None:
    broker = CaptureBroker()
    answers: list = []
    waiting = threading.Thread(target=lambda: answers.append(broker.request('https://x.org', timeout=5)))
    waiting.start()
    while not (claimed := broker.claim()):
        pass
    assert claimed[0]['url'] == 'https://x.org' and broker.claim() == [], 'handed out only once'
    assert broker.deliver(claimed[0]['id'], Captured('https://x.org/', '<html/>', asked_you=True))
    waiting.join()
    assert answers == [Captured('https://x.org/', '<html/>', asked_you=True)]
    assert not broker.deliver(claimed[0]['id'], None, 'late'), 'nobody waits any more'


def test_error_and_timeout_reach_the_user() -> None:
    broker = CaptureBroker()
    with pytest.raises(CaptureError, match='no Automation desk page picked up'):
        broker.request('https://x.org', claim_timeout=0.05)
    errors: list = []

    def ask() -> None:
        """Request a page and keep the error."""
        try:
            broker.request('https://y.org', timeout=5)
        except CaptureError as error:
            errors.append(str(error))

    asker = threading.Thread(target=ask)
    asker.start()
    while not (claimed := broker.claim()):
        pass
    broker.deliver(claimed[0]['id'], None, 'extension missing')
    asker.join()
    assert errors == ['extension missing']


def test_missing_extension_asks_for_setup() -> None:
    broker = CaptureBroker()
    errors: list = []

    def ask() -> None:
        """Request a page and keep the error."""
        try:
            broker.request('https://z.org', timeout=5)
        except CaptureError as error:
            errors.append(error.setup)

    asker = threading.Thread(target=ask)
    asker.start()
    while not (claimed := broker.claim()):
        pass
    broker.deliver(claimed[0]['id'], None, missing_extension=True)
    asker.join()
    assert errors == ['extension']
