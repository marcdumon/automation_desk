"""Saving the site list: known sites kept, new ones looked up, left-out ones removed."""

import httpx
import pytest

from automation_desk.groups.news import sites, store


def test_saving_the_site_list(monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://standaard.be', 'De Standaard', 'https://www.standaard.be/rss', 'feed')
    store.add_source('https://lemonde.fr', 'Le Monde', 'https://www.lemonde.fr/rss/une.xml', 'feed')
    looked_up = []

    def find(site: str, http: object) -> tuple:
        """HLN has a feed; nope.example does not answer."""
        looked_up.append(site)
        if 'nope' in site:
            raise httpx.ConnectError('no route')
        return ('HLN', 'https://www.hln.be/rss.xml', 'feed')

    monkeypatch.setattr(sites, 'find_feed', find)
    problems = sites.save_site_list(['www.standaard.be/', '', 'hln.be', 'HLN.be', 'nope.example'])
    assert [s.name for s in store.sources()] == ['De Standaard', 'HLN'], 'Le Monde was deleted from the list'
    assert looked_up == ['https://hln.be', 'https://nope.example'], 'known sites are not looked up again; repeats once'
    assert problems == ['nope.example was not added: it could not be reached (no route).']
