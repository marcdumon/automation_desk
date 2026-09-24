"""The capture broker: the app waits, the browser claims once and delivers."""

import threading

import pytest

from automation_desk.capture import CaptureBroker, Captured, CaptureError, NeedsPerson


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


def test_a_request_says_whether_the_tab_may_come_forward() -> None:
    """News reads run while the user works elsewhere: their tabs must never take the focus. Calendar imports may ask."""
    broker = CaptureBroker()
    for url, may_ask in (('https://news.org', False), ('https://agenda.org', True)):
        threading.Thread(target=lambda u=url, m=may_ask: broker.request(u, timeout=0.5, claim_timeout=0.5, may_ask=m),
                         daemon=True).start()
    claimed: list = []
    while len(claimed) < 2:
        claimed += broker.claim()
    assert {c['url']: c['may_ask'] for c in claimed} == {'https://news.org': False, 'https://agenda.org': True}


def test_a_page_that_needs_a_person_says_so() -> None:
    """A human check on a quiet read is not solved by waiting: the app is told, and lists the site for the user."""
    broker = CaptureBroker()
    errors: list = []

    def wait() -> None:
        """The app waits for the page."""
        try:
            broker.request('https://wsj.com', timeout=5, may_ask=False)
        except NeedsPerson as error:
            errors.append(error)

    waiting = threading.Thread(target=wait)
    waiting.start()
    while not (claimed := broker.claim()):
        pass
    broker.deliver(claimed[0]['id'], None, 'wsj.com shows a human check', needs_person=True)
    waiting.join()
    assert len(errors) == 1 and isinstance(errors[0], CaptureError)
