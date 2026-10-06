"""The euro exchange rates of the day from the European Central Bank: a research turns a price in dollars or pounds into
euros with them, plus the Belgian import VAT, so it competes with the euro prices. Downloaded once a day and kept; without
any rate a foreign price stays out of the ranking, as before."""

import re
from datetime import date

import httpx

from automation_desk.research import store

ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
CURRENCIES = ('USD', 'GBP')
# CLAUDE> a shop outside the EU sells without Belgian VAT: it is paid at import (customs duties and fees come on top)
IMPORT_VAT = 0.21
KEY = 'rates'


def parse(xml: str) -> dict | None:
    """The day and the rates (units per euro) of the currencies the research converts; None when the file holds none."""
    day = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", xml)
    found = {code: float(rate) for code, rate in re.findall(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([\d.]+)['\"]", xml)
             if code in CURRENCIES}
    return {'date': day.group(1), **found} if day and found else None


def current(http: httpx.Client | None = None, today: date | None = None) -> dict | None:
    """The rates of the day: downloaded at most once a day, else the ones kept; None when there never were any."""
    today = today or date.today()
    kept = store.kept(KEY)
    if kept and kept.get('fetched') == today.isoformat():
        return {k: v for k, v in kept.items() if k != 'fetched'}
    client = http or httpx.Client(timeout=20.0)
    try:
        fresh = parse(client.get(ECB_URL).raise_for_status().text)
    except httpx.HTTPError:
        fresh = None
    finally:
        if http is None:
            client.close()
    if fresh:
        store.keep(KEY, fresh | {'fetched': today.isoformat()})
        return fresh
    return {k: v for k, v in kept.items() if k != 'fetched'} if kept else None


def to_euro(amount: float, currency: str, day: dict | None) -> float | None:
    """An amount in dollars or pounds as euros with the Belgian import VAT; None without a rate for its currency."""
    if not day or currency not in CURRENCIES or not day.get(currency):
        return None
    return round(amount / day[currency] * (1 + IMPORT_VAT), 2)
