"""The euro rates of the day from the European Central Bank, kept for the day; a research converts dollar and pound prices."""

from datetime import date

import httpx

from automation_desk.research import rates, store

# CLAUDE> the shape of https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml, shortened
ECB = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-10-02'><Cube currency='USD' rate='1.1705'/><Cube currency='JPY' rate='172.4'/>
<Cube currency='GBP' rate='0.8712'/></Cube></Cube></gesmes:Envelope>"""


def answering(body: str, calls: list) -> httpx.Client:
    """A client whose every request gets `body`, counted in `calls`."""
    def handler(request: httpx.Request) -> httpx.Response:
        """One answer."""
        calls.append(str(request.url))
        return httpx.Response(200, text=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_rates_of_the_day_are_read_and_kept_for_the_day() -> None:
    calls: list = []
    http = answering(ECB, calls)
    assert rates.current(http, date(2026, 10, 4)) == {'date': '2026-10-02', 'USD': 1.1705, 'GBP': 0.8712}
    assert rates.current(http, date(2026, 10, 4))['USD'] == 1.1705 and calls == [rates.ECB_URL], 'one download a day'
    rates.current(http, date(2026, 10, 5))
    assert len(calls) == 2, 'a new day: a new download'
    assert 'rates' not in store.settings(), 'not a setting of the page'


def test_without_a_download_the_rates_kept_are_used_and_else_none() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        """The bank's site is down."""
        raise httpx.ConnectError('down')
    down = httpx.Client(transport=httpx.MockTransport(broken))
    assert rates.current(down, date(2026, 10, 4)) is None
    rates.current(answering(ECB, []), date(2026, 10, 4))
    assert rates.current(down, date(2026, 10, 6))['date'] == '2026-10-02'


def test_a_price_in_dollars_becomes_euros_with_import_vat() -> None:
    day = {'date': '2026-10-02', 'USD': 1.25, 'GBP': 0.8}
    assert rates.to_euro(100.0, 'USD', day) == 96.8, '100 / 1.25 = 80 euro, plus 21% Belgian import VAT'
    assert rates.to_euro(80.0, 'GBP', day) == 121.0
    assert rates.to_euro(100.0, 'JPY', day) is None and rates.to_euro(100.0, 'USD', None) is None
