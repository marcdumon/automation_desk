"""The user's real agenda sites, saved: every date and the page it links to must stay as reviewed.

A fix for one site must not break another. `expected.json` holds, per site, each date the code reads and the event page
it belongs to, checked by hand against the pages. After a deliberate change, rewrite it with
`UPDATE_SITES=1 uv run pytest tests/test_sites.py` and review the diff before keeping it.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from automation_desk.groups.calendar.web_page import _date_matches, marked_text

from .conftest import TZ

SITES = Path(__file__).parent / 'fixtures' / 'sites'
EXPECTED = SITES / 'expected.json'
# CLAUDE> the day the pages were saved: years without one on the page are guessed from it
SAVED = date(2026, 9, 26)
PAGES = {
    'kmska': 'https://kmska.be/nl/overzicht/tentoonstellingen?tab=verwacht',
    'muhka': 'https://www.muhka.be/en/programma',
    'patrickderomgallery': 'https://patrickderomgallery.com/exhibitions',
    'bozar': 'https://www.bozar.be/nl/tentoonstellingen',
    'smak': 'https://smak.be/nl/tentoonstellingen',
    'kanal': 'https://kanal.brussels/en/calendar?production_type=Exhibition',
    # CLAUDE> the same page as the browser shows it once its list has arrived
    'kanal-browser': 'https://kanal.brussels/en/calendar?production_type=Exhibition',
    'axel-vervoordt': 'https://www.axel-vervoordt.com/gallery/exhibitions#exhibitions-current',
    'ticktack': 'https://ticktack.be/exhibitions',
}
# CLAUDE> agendas whose events all have a page of their own (Kanal's list is filled by JavaScript: nothing to link)
WITH_EVENT_PAGES = ('kmska', 'muhka', 'patrickderomgallery', 'bozar', 'smak', 'kanal-browser', 'axel-vervoordt', 'ticktack')


def read(name: str) -> list[list[str]]:
    """Each date on a saved page with the page it links to, in page order."""
    html = (SITES / f'{name}.html').read_text()
    spans = marked_text(BeautifulSoup(html, 'lxml'), PAGES[name], SAVED, TZ)
    return [[spans.dates[n][0].isoformat(), spans.links.get(spans.date_links.get(n, -1), '')] for n in sorted(spans.dates)]


def test_the_saved_sites_read_as_reviewed() -> None:
    found = {name: read(name) for name in PAGES}
    if os.environ.get('UPDATE_SITES'):
        EXPECTED.write_text(json.dumps(found, indent=1) + '\n')
    expected = json.loads(EXPECTED.read_text())
    for name in PAGES:
        assert found[name] == expected[name], f'{name} reads differently than reviewed'


@pytest.mark.parametrize('name', PAGES)
def test_a_year_on_the_page_is_the_year_read(name: str) -> None:
    """Every date on a line that writes its year gets that year ('25 Jan - 1 Feb 2026', "14 Nov.'24")."""
    text = BeautifulSoup((SITES / f'{name}.html').read_text(), 'lxml').get_text('\n', strip=True)
    wrong = []
    for line in dict.fromkeys(text.split('\n')):
        written = {int(a or f'20{b or c}') for a, b, c in re.findall(r"\b(20\d\d)\b|'(\d\d)\b|\.(\d\d)\b", line)}
        days = [d for _, _, found, _ in _date_matches(line, SAVED) for d, _ in found]
        # CLAUDE> a range across New Year writes only its end's year: '13.12—18.01.2025' starts in 2024
        crossing = {d for i, d in enumerate(days) if d.year + 1 in written and any(e.year == d.year + 1 for e in days[i + 1:])}
        if len(line) <= 60 and written and any(d.year not in written and d not in crossing for d in days):
            wrong.append((line, [d.isoformat() for d in days]))
    assert wrong == []


@pytest.mark.parametrize('name', WITH_EVENT_PAGES)
def test_every_event_links_to_its_own_page(name: str) -> None:
    """Every date sits in a link to an event's own page, never the agenda itself; only the end of a range whose start is
    linked may stand outside ('24 - 31 Jan 2027' on Patrick Derom)."""
    agenda = PAGES[name].split('#')[0].rstrip('/')
    rows = read(name)
    assert all(page.split('#')[0].rstrip('/') != agenda for _, page in rows if page)
    assert all(page or (i > 0 and rows[i - 1][1]) for i, (_, page) in enumerate(rows))
