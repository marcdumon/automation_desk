"""Compare: the same product across shops, price with delivery, the budget, products without a price."""

from automation_desk.research import compare


def page(url: str, country: str, products: list[dict], delivery: float | None = None, ships: str = 'yes') -> dict:
    """Facts of one page as offers.extract returns them, with the page's country."""
    full = [{'brand': '', 'model': '', 'gtin': '', 'currency': 'EUR', 'in_stock': None, 'specs': {}, 'contact': ''} | p
            for p in products]
    return {'url': url, 'country': country, 'facts': {'url': url, 'ships_to_belgium': ships, 'delivery_cost': delivery,
                                                      'products': full}}


def test_same_product_in_two_shops_by_gtin_then_by_brand_and_model() -> None:
    result = compare.table([
        page('https://a.be/1', 'BE', [{'name': 'DAB Nova 300', 'gtin': '805', 'price': 139.0}]),
        page('https://b.nl/2', 'NL', [{'name': 'Nova 300 dompelpomp', 'gtin': '805', 'price': 119.0}], delivery=6.95),
        page('https://c.de/3', 'DE', [{'name': 'Tauchpumpe', 'brand': 'Grundfos', 'model': 'Unilift KP 250', 'price': 180.0}]),
        page('https://d.fr/4', 'FR', [{'name': 'Unilift KP250', 'brand': 'grundfos', 'model': 'Unilift KP-250', 'price': 175.0}]),
    ], budget=200)
    assert [(p['n'], len(p['offers']), p['best_total']) for p in result['products']] == [(1, 2, 125.95), (2, 2, 175.0)]
    assert [o['shop'] for o in result['products'][0]['offers']] == ['b.nl', 'a.be'], 'cheapest with delivery first'


def test_budget_shops_that_do_not_deliver_and_products_without_price() -> None:
    result = compare.table([
        page('https://a.be/1', 'BE', [{'name': 'Cheap', 'price': 80.0}, {'name': 'Pricey', 'price': 400.0},
                                      {'name': 'Mystery', 'price': None}]),
        page('https://x.de/1', 'DE', [{'name': 'Only here', 'price': 50.0}], ships='no'),
    ], budget=150)
    assert [p['name'] for p in result['products']] == ['Cheap']
    assert [(p['n'], p['name']) for p in result['over_budget']] == [(2, 'Pricey')]
    assert [(p['n'], p['name']) for p in result['no_price']] == [(3, 'Mystery')]


def test_without_budget_nothing_is_over_it() -> None:
    result = compare.table([page('https://a.be/1', 'BE', [{'name': 'A', 'price': 9999.0}])], budget=None)
    assert [p['name'] for p in result['products']] == ['A'] and result['over_budget'] == []


def test_offers_without_vat_are_marked_and_do_not_compete() -> None:
    """An ex-VAT price is shown marked but leaves the cheapest total to the prices with VAT."""
    result = compare.table([
        page('https://a.be/1', 'BE', [{'name': 'Vatpomp', 'gtin': '1', 'price': 350.0, 'vat_included': False}]),
        page('https://b.be/1', 'BE', [{'name': 'Vatpomp', 'gtin': '1', 'price': 423.5, 'vat_included': True}]),
        page('https://c.be/1', 'BE', [{'name': 'Netto', 'price': 100.0, 'vat_included': False}]),
    ], budget=None)
    row = result['products'][0]
    assert row['best_total'] == 423.5 and [(o['price'], o['ex_vat']) for o in row['offers']] == [(423.5, False), (350.0, True)]
    assert [p['name'] for p in result['no_price']] == ['Netto']


def test_service_contact_region_and_reviews_reach_the_offers() -> None:
    """The company table needs contact, region and reviews from each page."""
    result = compare.table([page('https://p.be/', 'BE', [{'name': 'Pompservice', 'price': None, 'contact': '03 123 45 67',
                                                          'region': 'Antwerpen', 'reviews': '4.6 (52)'}])], budget=None)
    offer = result['no_price'][0]['offers'][0]
    assert (offer['contact'], offer['region'], offer['reviews']) == ('03 123 45 67', 'Antwerpen', '4.6 (52)')


def test_a_price_far_above_the_budget_or_the_others_counts_as_no_price() -> None:
    """€127,545 for a pump (a shop's price in cents) must not be shown as a price: more than 10 times the budget, or, without a
    budget, more than 10 times the middle price of the others."""
    pages = [page('https://a.be/1', 'BE', [{'name': 'A', 'price': 120.0}]),
             page('https://b.be/1', 'BE', [{'name': 'B', 'price': 150.0}]),
             page('https://w.be/1', 'BE', [{'name': 'Wilo', 'price': 127545.0, 'price_seen': False}])]
    for budget in (500, None):
        result = compare.table(pages, budget=budget)
        assert [p['name'] for p in result['no_price']] == ['Wilo'] and result['over_budget'] == []
        assert result['no_price'][0]['offers'][0]['price'] is None


def test_a_generic_name_gets_its_brand_and_model() -> None:
    """vervelde.com calls five Pedrollo pumps "Dompelpomp met vlotter" (research 1): the title adds brand and model."""
    result = compare.table([page('https://v.nl/1', 'NL', [{'name': 'Dompelpomp met vlotter', 'brand': 'Pedrollo',
                                                           'model': 'Top 2 (205122)', 'price': 198.98}])], budget=None)
    assert result['products'][0]['title'] == 'Dompelpomp met vlotter — Pedrollo Top 2 (205122)'
    result = compare.table([page('https://g.be/1', 'BE', [{'name': 'Gardena 9000', 'brand': 'Gardena', 'model': '9000',
                                                           'price': 73.59}])], budget=None)
    assert result['products'][0]['title'] == 'Gardena 9000'


def test_stored_pages_from_before_the_price_checks_are_cleaned() -> None:
    """Research 1's pages were read before the checks: a price of 0 and a product listed twice on one page are cleaned
    when the comparison is made again."""
    result = compare.table([
        page('https://e.fr/t', 'FR', [{'name': 'TROTEC TWP 4006 E', 'price': 0.0}]),
        page('https://w.nl/g', 'NL', [{'name': 'GARDENA 20000 Aquasensor', 'model': '970486101', 'price': 142.79},
                                      {'name': 'GARDENA 20000 Aquasensor - 20.000 liter/uur', 'gtin': '4078', 'price': 142.79},
                                      {'name': 'GARDENA 20000 Aquasensor - met slang', 'price': 179.95}]),
    ], budget=500)
    assert [p['name'] for p in result['no_price']] == ['TROTEC TWP 4006 E']
    assert [(p['name'], p['best_total']) for p in result['products']] == [('GARDENA 20000 Aquasensor', 142.79),
                                                                          ('GARDENA 20000 Aquasensor - met slang', 179.95)]


def test_a_price_the_page_shows_is_never_dropped_as_implausible() -> None:
    """A € 450 pump on a € 40 budget is over the budget, not a product without a price."""
    result = compare.table([page('https://a.be/1', 'BE', [{'name': 'Pump B', 'price': 450.0, 'price_seen': True}]),
                            page('https://w.be/1', 'BE', [{'name': 'Wilo', 'price': 127545.0, 'price_seen': False}])], budget=40)
    assert [p['name'] for p in result['over_budget']] == ['Pump B'] and [p['name'] for p in result['no_price']] == ['Wilo']


def test_shops_from_countries_not_chosen_are_left_out() -> None:
    """Research 3 chose BE and NL; a French-language search for Belgium brought nhp-motoculture.fr, which became the
    recommendation. A page from a country not chosen is out; a .com page (no country) stays."""
    pages = [page('https://nhp-motoculture.fr/p', 'FR', [{'name': 'Pompe vide cave', 'price': 138.0}]),
             page('https://shop.be/p', 'BE', [{'name': 'Pomp', 'price': 150.0}]),
             page('https://shop.com/p', '', [{'name': 'Global pomp', 'price': 160.0}])]
    result = compare.table(pages, budget=500, countries=['BE', 'NL'])
    assert [p['name'] for p in result['products']] == ['Pomp', 'Global pomp']


def test_an_offer_links_to_the_product_itself_when_known() -> None:
    result = compare.table([page('https://amazon.com.be/b?node=1', 'BE', [
        {'name': 'Einhell', 'price': 53.77, 'url': 'https://amazon.com.be/dp/B01'}, {'name': 'Vonroc', 'price': 49.95}])], budget=None)
    assert {p['name']: p['offers'][0]['url'] for p in result['products']} == {
        'Einhell': 'https://amazon.com.be/dp/B01', 'Vonroc': 'https://amazon.com.be/b?node=1'}
