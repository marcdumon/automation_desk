"""Organiser profiles: short names in titles, normal capitals, addresses only as found on the site."""

import pytest

from llm_automation.groups.calendar import organisers
from llm_automation.groups.calendar.organisers import Organiser, fix_caps, placed, titled

KMSKA = Organiser('kmska.be', 'KMSKA', 'Leopold de Waelplaats 1, 2000 Antwerpen')


def test_titles_start_with_the_organiser_in_normal_capitals() -> None:
    assert titled('KMSKA X YOUNG FASHION DESIGNERS', KMSKA) == 'KMSKA X Young Fashion Designers'
    assert titled('Kosmorama', KMSKA) == 'KMSKA: Kosmorama'
    assert titled('ZWART OP WIT. RUBENSGRAFIEK UIT DE COLLECTIE', KMSKA) == 'KMSKA: Zwart op Wit. Rubensgrafiek uit de Collectie'
    assert fix_caps('What\u2019s the Story?') == 'What\u2019s the Story?', 'normal titles are left alone'


def test_places_get_the_organiser_address_only_when_missing_or_just_the_organiser() -> None:
    assert placed('', KMSKA) == 'KMSKA, Leopold de Waelplaats 1, 2000 Antwerpen'
    assert placed('prentenkabinet van het KMSKA', KMSKA) == 'prentenkabinet van het KMSKA, Leopold de Waelplaats 1, 2000 Antwerpen'
    assert placed('Grand Palais, Paris', KMSKA) == 'Grand Palais, Paris', 'a place elsewhere is not touched'
    assert placed('KMSKA, Leopold de Waelplaats 1, 2000 Antwerpen', KMSKA).count('Waelplaats') == 1


def test_proposed_address_must_be_on_the_site(monkeypatch: pytest.MonkeyPatch) -> None:
    html = '<html><head><title>Onze expo\'s</title></head><body><footer>Leopold de Waelplaats 1, 2000 Antwerpen</footer></body></html>'
    monkeypatch.setattr(organisers, 'ask', lambda *a, **k: organisers.OrganiserFound(
        short_name='KMSKA', address='Leopold de Waelplaats 1, 2000 Antwerpen'))
    assert organisers.propose(html, 'kmska.be') == KMSKA
    monkeypatch.setattr(organisers, 'ask', lambda *a, **k: organisers.OrganiserFound(
        short_name='KMSKA', address='Leopold De Waelplaats 1, 2000 Antwerpen, Belgium'))
    assert organisers.propose(html, 'kmska.be').address == '', 'an invented part drops the address'


def test_profiles_are_stored_per_site(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(organisers, 'STORE', tmp_path / 'organisers.json')
    calls: list = []
    monkeypatch.setattr(organisers, 'propose', lambda html, site, http=None: calls.append(site) or KMSKA)
    assert organisers.organiser_for('https://www.kmska.be/nl/x', '') == KMSKA
    assert organisers.organiser_for('https://kmska.be/nl/y', '') == KMSKA
    assert calls == ['kmska.be'], 'proposed once, then read from the store'
