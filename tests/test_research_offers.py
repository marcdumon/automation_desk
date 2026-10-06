"""Facts per page: JSON-LD prices in code, the model's prices only when the page shows them."""

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from automation_desk.research import offers, pages

SHOPS = Path(__file__).parent / 'fixtures' / 'shops'
EXPECTED = json.loads((SHOPS / 'expected.json').read_text()) if (SHOPS / 'expected.json').exists() else {}


@pytest.mark.parametrize('price, text, found', [
    (1234.56, 'Nu € 1.234,56 incl. btw', True), (1234.56, 'Price $1,234.56', True), (149.0, 'Prijs 149,- euro', True),
    (89.95, '€ 89,95', True), (89.95, '€ 89,99', False), (12.0, 'Opvoerhoogte 12 m', False), (12.0, 'verzending € 12', True),
    (12.0, '12 Europese landen', False), (5.0, 'in 5 Europe', False), (12.0, '12 euro', True), (12.0, '12 euros', True),
])
def test_price_on_page_formats(price: float, text: str, found: bool) -> None:
    """Amounts are found next to a currency in the usual spellings and nowhere else."""
    assert offers.price_on_page(price, text) is found


@pytest.mark.parametrize('name', sorted(EXPECTED))
def test_saved_shop_pages_give_the_prices_on_the_page(name: str, monkeypatch) -> None:
    """Code reads JSON-LD; the stand-in model adds nothing, so these are code-only results."""
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[], ships_to_belgium='unknown', delivery_cost=None))
    soup = BeautifulSoup((SHOPS / name).read_text(), 'lxml')
    read = pages.Read(url=EXPECTED[name]['url'], text=pages.page_text(BeautifulSoup((SHOPS / name).read_text(), 'lxml')),
                      products=pages.jsonld_products(soup))
    facts = offers.extract(read, 'submersible pump', 'product', 'BE')
    got = [{k: p[k] for k in ('name', 'price', 'currency', 'gtin')} for p in facts['products']]
    assert got == EXPECTED[name]['products']


def test_an_invented_price_and_delivery_cost_are_removed(monkeypatch) -> None:
    """A model price the page does not show becomes None, and so does the delivery cost."""
    read = pages.Read(url='https://shop.de/p', text='Tauchpumpe Nova 300 nur 129,95 € Versand 4,95 €', products=[])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(
        products=[offers.FoundProduct(name='Nova 300', brand='DAB', model='Nova 300', price=99.0, specs=[], contact=''),
                  offers.FoundProduct(name='Nova 600', brand='DAB', model='Nova 600', price=129.95, specs=[], contact='')],
        ships_to_belgium='yes', delivery_cost=7.5))
    facts = offers.extract(read, 'pump', 'product', 'DE')
    assert [(p['name'], p['price']) for p in facts['products']] == [('Nova 300', None), ('Nova 600', 129.95)]
    assert facts['delivery_cost'] is None


def test_category_page_keeps_at_most_five(monkeypatch) -> None:
    """A category page gives five products at most, and a .be shop delivers in Belgium."""
    read = pages.Read(url='https://shop.be/c', text=' '.join(f'Pomp {n} € {n}0,00' for n in range(1, 9)), products=[])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[
        offers.FoundProduct(name=f'Pomp {n}', brand='', model='', price=n * 10.0, specs=[], contact='') for n in range(1, 9)],
        ships_to_belgium='no', delivery_cost=None))
    facts = offers.extract(read, 'pump', 'product', 'BE')
    assert len(facts['products']) == 5 and facts['ships_to_belgium'] == 'yes', 'a .be shop delivers in Belgium'


def test_jsonld_price_wins_over_the_model(monkeypatch) -> None:
    """The shop's structured price, GTIN and stock beat the model; the model adds specs."""
    read = pages.Read(url='https://shop.nl/p', text='DAB Nova 300 € 129,95', products=[
        {'name': 'DAB Nova 300', 'brand': 'DAB', 'gtin': '805', 'price': 129.95, 'currency': 'EUR', 'in_stock': True}])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='DAB Nova 300', brand='DAB', model='Nova 300', price=119.0, specs=[offers.Spec(name='Head', value='7 m')],
        contact='')], ships_to_belgium='yes', delivery_cost=None))
    product = offers.extract(read, 'pump', 'product', 'NL')['products'][0]
    assert (product['price'], product['gtin'], product['specs']) == (129.95, '805', {'Head': '7 m'})


def test_products_the_model_named_come_before_the_cut(monkeypatch) -> None:
    """Seven structured products on a category page; the two the model named for the need are kept first."""
    read = pages.Read(url='https://shop.be/c', text='Category', products=[
        {'name': f'Pomp {n}', 'brand': '', 'gtin': '', 'price': None, 'currency': '', 'in_stock': None} for n in range(1, 8)])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[
        offers.FoundProduct(name=f'Pomp {n}', brand='', model='', price=None, specs=[], contact='') for n in (7, 6)],
        ships_to_belgium='yes', delivery_cost=None))
    names = [p['name'] for p in offers.extract(read, 'pump', 'product', 'BE')['products']]
    assert names == ['Pomp 7', 'Pomp 6', 'Pomp 1', 'Pomp 2', 'Pomp 3']


def test_the_model_marks_a_price_without_vat(monkeypatch) -> None:
    """A price shown 'excl. BTW' keeps the model's VAT flag."""
    read = pages.Read(url='https://manutan.be/c', text='Vatpomp € 350,00 excl. BTW', products=[])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='Vatpomp', brand='', model='', price=350.0, specs=[], contact='', vat_included=False)],
        ships_to_belgium='yes', delivery_cost=None))
    product = offers.extract(read, 'pump', 'product', 'BE')['products'][0]
    assert (product['price'], product['vat_included']) == (350.0, False)


def none_from_model(*args: object, **kwargs: object) -> offers.Facts:
    """A stand-in model that adds nothing to the structured data."""
    return offers.Facts(products=[], ships_to_belgium='unknown', delivery_cost=None)


def test_a_price_the_page_does_not_show_is_marked_not_seen(monkeypatch) -> None:
    """Wilo's page shows no price; its data says "price": "127545" (research 1): kept, but marked as not seen on the page."""
    monkeypatch.setattr(offers, 'ask', none_from_model)
    read = pages.Read(url='https://wilo.com/be/p', text='Sub TWI 5-SE pompe immergée', products=[
        {'name': 'Sub TWI 5-SE-305EM-FS', 'brand': 'Wilo', 'gtin': '4048', 'price': 127545.0, 'currency': 'EUR',
         'in_stock': False, 'vat_included': None}])
    product = offers.extract(read, 'pump', 'product', 'BE')['products'][0]
    assert (product['price'], product['price_seen']) == (127545.0, False)
    read = pages.Read(url='https://a.be/p', text='Pomp € 99,00', products=[
        {'name': 'Pomp', 'brand': '', 'gtin': '', 'price': 99.0, 'currency': 'EUR', 'in_stock': None, 'vat_included': None}])
    assert offers.extract(read, 'pump', 'product', 'BE')['products'][0]['price_seen'] is True


def test_one_product_named_twice_on_a_page_is_one(monkeypatch) -> None:
    """wolfswinkel.nl listed the Gardena 20000 under its short and its long name at the same price; the variant with a hose
    at another price stays separate (research 1)."""
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[
        offers.FoundProduct(name='GARDENA Dompelpomp Vuilwater 20000 Aquasensor', brand='GARDENA', model='970486101',
                            price=142.79, specs=[offers.Spec(name='Flow', value='20000 l/h')], contact=''),
        offers.FoundProduct(name='GARDENA Dompelpomp Vuilwater 20000 Aquasensor - met platte slang', brand='', model='',
                            price=179.95, specs=[], contact='')], ships_to_belgium='yes', delivery_cost=None))
    read = pages.Read(url='https://wolfswinkel.nl/p', text='Gardena 20000 € 142,79 met slang € 179,95', products=[
        {'name': 'GARDENA Dompelpomp Vuilwater 20000 Aquasensor - 20.000 liter/uur - 0,9 bar', 'brand': 'Gardena',
         'gtin': '4078500053310', 'price': 142.79, 'currency': 'EUR', 'in_stock': None, 'vat_included': None}])
    products = offers.extract(read, 'pump', 'product', 'NL')['products']
    assert [(p['price'], p['gtin'], p['model']) for p in products] == [(142.79, '4078500053310', '970486101'), (179.95, '', '')]


def test_each_product_gets_its_own_link_from_its_number(monkeypatch) -> None:
    """The model names the link number; code turns it into the address. A number not on the page gives no link."""
    read = pages.Read(url='https://amazon.com.be/b?node=1', text='Einhell GC-DP 7835 [L1] € 53,77 VONROC [L2] € 49,95',
                      products=[], links={1: 'https://amazon.com.be/dp/B01', 2: 'https://amazon.com.be/dp/B02'})
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[
        offers.FoundProduct(name='Einhell GC-DP 7835', brand='Einhell', model='GC-DP 7835', price=53.77, specs=[], contact='', link=1),
        offers.FoundProduct(name='VONROC', brand='', model='', price=49.95, specs=[], contact='', link=9)],
        ships_to_belgium='yes', delivery_cost=None))
    found = offers.extract(read, 'pump', 'product', 'BE')['products']
    assert [p.get('url') for p in found] == ['https://amazon.com.be/dp/B01', None]


def test_a_product_the_page_calls_free_costs_nothing(monkeypatch) -> None:
    """'Free Linux mindmap software': Freeplane is free, and a price of 0 counted as no price, so nothing could be advised."""
    read = pages.Read(url='https://freeplane.org', text='Freeplane — free mind mapping software. Download for Linux.', products=[])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[
        offers.FoundProduct(name='Freeplane', brand='', model='', price=None, specs=[], contact='', free=True),
        offers.FoundProduct(name='Pro edition', brand='', model='', price=0.0, specs=[], contact='')],
        ships_to_belgium='unknown', delivery_cost=None))
    products = {p['name']: p for p in offers.extract(read, 'free mindmap program', 'product', '')['products']}
    assert {k: products['Freeplane'][k] for k in ('price', 'currency', 'price_seen', 'free')} == {
        'price': 0.0, 'currency': 'EUR', 'price_seen': True, 'free': True}
    assert products['Pro edition']['price'] is None, 'a 0 the page does not call free is still no price (e.leclerc)'
