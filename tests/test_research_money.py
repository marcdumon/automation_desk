"""Amounts as shops write them: one parser for European and US spellings, and the currency sign next to an amount."""

import pytest
from bs4 import BeautifulSoup

from automation_desk.research import compare, money, offers, pages


@pytest.mark.parametrize('written, value', [
    ('1.299', 1299.0), ('1.299,00', 1299.0), ('1,299.00', 1299.0), ('129.95', 129.95), ('129,95', 129.95),
    ('1 299,00', 1299.0), ('1\u00a0299,00', 1299.0), ('1.299,-', 1299.0), ('149,-', 149.0), ('1.234.567,89', 1234567.89),
    ('0.750', 0.75), (129.95, 129.95), (1299, 1299.0), ('€ 89,95', 89.95), ('', None), ('abc', None),
])
def test_amounts_in_european_and_us_spelling(written: object, value: float | None) -> None:
    """Thousands with a dot, comma or space; decimals with a comma or a dot; JSON numbers as they are."""
    assert money.amount(written) == value


@pytest.mark.parametrize('price', ['1.299', '1.299,00', '1,299.00'])
def test_structured_prices_with_thousands_are_read_whole(price: str) -> None:
    """A schema.org price written with thousands is 1299, not 1.299 or nothing."""
    html = ('<script type="application/ld+json">{"@type": "Product", "name": "Pomp", '
            f'"offers": {{"@type": "Offer", "price": "{price}", "priceCurrency": "EUR"}}}}</script>')
    assert pages.jsonld_products(BeautifulSoup(html, 'lxml'))[0]['price'] == 1299.0


@pytest.mark.parametrize('price, text, found', [
    (1299.0, 'Prix 1 299,00 €', True), (1299.0, 'Prix 1\u00a0299,00 €', True), (1299.0, 'Prix 1\u202f299,00 €', True),
    (1299.0, 'Nu € 1.299,-', True), (26.9, 'Prix 26 €90 TTC', True), (26.9, 'Prix 126 €90', False),
    (299.0, 'Prix 1 299,00 €', False), (1.0, 'Prix € 1 299,00', False),
])
def test_french_thousands_and_dash_cents_are_confirmed(price: float, text: str, found: bool) -> None:
    """A space, a no-break space or a narrow no-break space between thousands; ',-' for whole euros."""
    assert offers.price_on_page(price, text) is found


@pytest.mark.parametrize('text, currency', [
    ('Pomp € 129,95', 'EUR'), ('Pomp 129,95 EUR', 'EUR'), ('Pump $129.95', 'USD'), ('Pump £ 129.95', 'GBP'),
    ('129,95 par heure en Europe', None),
])
def test_the_currency_is_the_sign_next_to_the_amount(text: str, currency: str | None) -> None:
    """The currency comes from the sign at the number, not from 'eur' somewhere in the text."""
    assert money.currency_on_page(129.95, text) == currency


def test_a_structured_price_the_page_contradicts_is_dropped_and_the_models_confirmed_price_used(monkeypatch) -> None:
    """The page shows € 1.299,00: the structured 1.299 is not on the page; the model's 1299 is."""
    read = pages.Read(url='https://shop.be/p', text='Pomp X nu € 1.299,00 incl. btw', products=[
        {'name': 'Pomp X', 'brand': '', 'gtin': '', 'price': 1.299, 'currency': 'EUR', 'in_stock': None}])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='Pomp X', brand='', model='X', price=1299.0, specs=[], contact='', region='', reviews='', vat_included=True)],
        ships_to_belgium='yes', delivery_cost=None))
    product = offers.extract(read, 'pump', 'product', 'BE')['products'][0]
    assert (product['price'], product['currency']) == (1299.0, 'EUR')


def test_a_page_without_visible_prices_keeps_the_structured_price(monkeypatch) -> None:
    """Prices drawn by JavaScript are not in the text: the shop's structured price stays."""
    read = pages.Read(url='https://shop.be/p', text='Pomp X', products=[
        {'name': 'Pomp X', 'brand': '', 'gtin': '', 'price': 129.95, 'currency': 'EUR', 'in_stock': None}])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[], ships_to_belgium='yes', delivery_cost=None))
    assert offers.extract(read, 'pump', 'product', 'BE')['products'][0]['price'] == 129.95


def test_the_model_price_takes_the_currency_of_its_sign(monkeypatch) -> None:
    """A dollar price is in dollars even when the page says 'Europe' elsewhere."""
    read = pages.Read(url='https://shop.com/p', text='Ships to Europe. Pump $129.95', products=[])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='Pump', brand='', model='', price=129.95, specs=[], contact='', region='', reviews='', vat_included=None)],
        ships_to_belgium='unknown', delivery_cost=None))
    assert offers.extract(read, 'pump', 'product', '')['products'][0]['currency'] == 'USD'


def test_an_unknown_currency_is_not_compared_as_euro() -> None:
    """An offer without a known currency gets no euro total."""
    page = {'url': 'https://a.com/1', 'country': '', 'facts': {'ships_to_belgium': 'yes', 'delivery_cost': None, 'products': [
        {'name': 'A', 'brand': '', 'model': '', 'gtin': '', 'price': 100.0, 'currency': '', 'in_stock': None, 'specs': {}}]}}
    result = compare.table([page], budget=None)
    assert result['products'] == [] and [p['name'] for p in result['no_price']] == ['A']
