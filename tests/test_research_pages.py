"""Reading a vendor page: its text and its schema.org products; the browser when the shop refuses programs."""

import httpx
import pytest
from bs4 import BeautifulSoup

from automation_desk.groups.calendar import web_page
from automation_desk.research import pages

PRODUCT = '''<html><head><script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
{"@type": "Product", "name": "DAB Nova 300", "brand": {"@type": "Brand", "name": "DAB"}, "gtin13": "8051006000000",
 "offers": {"@type": "Offer", "price": "129.95", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"}}]}
</script></head><body><nav>Menu</nav><h1>DAB Nova 300</h1><p>Opvoerhoogte 7 m</p><footer>Cookies</footer></body></html>'''


def test_text_and_products_from_a_downloaded_page(monkeypatch) -> None:
    monkeypatch.setattr(web_page, 'fetch', lambda url, http, use_browser=False: web_page.Page(url=url, html=PRODUCT))
    read = pages.read('https://shop.be/nova', httpx.Client())
    assert read.text == 'DAB Nova 300 Opvoerhoogte 7 m'
    assert read.products == [{'name': 'DAB Nova 300', 'brand': 'DAB', 'gtin': '8051006000000', 'price': 129.95,
                              'currency': 'EUR', 'in_stock': True, 'vat_included': None,
                              'url': None}]


def test_a_refused_page_goes_through_the_browser_in_a_background_tab(monkeypatch) -> None:
    def refused(url: str, http: object, use_browser: bool = False) -> web_page.Page:
        """Refuse programs: 403."""
        raise httpx.HTTPStatusError('403', request=httpx.Request('GET', url), response=httpx.Response(403))

    asked = {}
    monkeypatch.setattr(web_page, 'fetch', refused)
    monkeypatch.setattr(web_page, 'browser_page', lambda url, may_ask=True: asked.setdefault('may_ask', may_ask) is not None
                        and web_page.Page(url=url, html=PRODUCT, via='browser'))
    assert pages.read('https://shop.de/x', httpx.Client()).via == 'browser' and asked['may_ask'] is False


def test_a_page_neither_way_can_read_is_unreadable(monkeypatch) -> None:
    from automation_desk.capture import CaptureError

    monkeypatch.setattr(web_page, 'fetch', lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError('down')))
    monkeypatch.setattr(web_page, 'browser_page', lambda *a, **k: (_ for _ in ()).throw(CaptureError('no browser')))
    with pytest.raises(pages.Unreadable, match=r'shop\.fr'):
        pages.read('https://shop.fr/x', httpx.Client())


def test_page_text_is_cut_to_the_limit() -> None:
    soup = BeautifulSoup(f'<p>{"woord " * 50}</p>', 'lxml')
    assert pages.page_text(soup, limit=10) == ' '.join(['woord'] * 10)


def test_a_pdf_is_unreadable_so_no_model_call_is_paid(monkeypatch) -> None:
    """A PDF has no shop text: the page is not read."""
    monkeypatch.setattr(web_page, 'fetch', lambda url, http, use_browser=False: web_page.Page(url=url, html='', pdf=b'%PDF-1.4'))
    with pytest.raises(pages.Unreadable, match='PDF'):
        pages.read('https://shop.be/datasheet.pdf', httpx.Client())


def test_related_products_inside_a_product_are_not_products_of_the_page() -> None:
    """isRelatedTo and isSimilarTo name other products; only the page's own product counts."""
    html = '''<script type="application/ld+json">{"@type": "Product", "name": "Nova 300",
    "offers": {"@type": "Offer", "price": "129.95", "priceCurrency": "EUR"},
    "isRelatedTo": [{"@type": "Product", "name": "Hose", "offers": {"price": "9.95"}}],
    "isSimilarTo": {"@type": "Product", "name": "Nova 600", "offers": {"price": "199"}}}</script>
    <script type="application/ld+json">{"@type": "ItemList", "itemListElement": [{"@type": "ListItem",
    "item": {"@type": "Product", "name": "Nova 180"}}]}</script>'''
    assert [p['name'] for p in pages.jsonld_products(BeautifulSoup(html, 'lxml'))] == ['Nova 300', 'Nova 180']


def test_an_offer_with_vat_wins_over_one_without() -> None:
    """A shop that gives the price without and with VAT: the price with VAT is kept; without it is marked."""
    html = '''<script type="application/ld+json">{"@type": "Product", "name": "Vatpomp", "offers": [
    {"@type": "Offer", "price": "350.00", "priceCurrency": "EUR",
     "priceSpecification": {"@type": "PriceSpecification", "price": "350.00", "valueAddedTaxIncluded": false}},
    {"@type": "Offer", "price": "423.50", "priceCurrency": "EUR", "valueAddedTaxIncluded": true}]}</script>
    <script type="application/ld+json">{"@type": "Product", "name": "Netto", "offers": {"@type": "Offer",
    "price": "100", "priceCurrency": "EUR", "valueAddedTaxIncluded": "false"}}</script>'''
    found = [(p['price'], p['vat_included']) for p in pages.jsonld_products(BeautifulSoup(html, 'lxml'))]
    assert found == [(423.5, True), (100.0, False)]


def test_a_price_of_zero_is_no_price() -> None:
    """e.leclerc writes "lowPrice": 0 for a product it shows no price for (Trotec TWP 4006 E, research 1)."""
    soup = BeautifulSoup('<script type="application/ld+json">{"@type": "Product", "name": "TROTEC TWP 4006 E", "offers": '
                         '{"@type": "AggregateOffer", "lowPrice": 0, "highPrice": 0, "priceCurrency": "EUR"}}</script>', 'lxml')
    assert pages.jsonld_products(soup)[0]['price'] is None


LIST_PAGE = '''<html><body><nav><a href="/account">Account</a></nav>
<h1>Dompelpompen</h1>
<div><a href="/p/einhell-gc-dp-7835">Einhell GC-DP 7835</a> € 53,77</div>
<div><a href="https://shop.be/p/vonroc">VONROC Dompelpomp</a> € 49,95</div>
<div><a href="/p/einhell-gc-dp-7835">Bekijk</a> <a href="#top">Top</a> <a href="mailto:x@y.be">Mail</a></div>
<script type="application/ld+json">{"@type": "Product", "name": "Gardena 9000", "url": "/p/gardena-9000",
 "offers": {"@type": "Offer", "price": "73.59", "priceCurrency": "EUR"}}</script></body></html>'''


def test_a_list_page_marks_each_product_link_with_a_number(monkeypatch) -> None:
    """Research 3 recommended a pump from an Amazon list page and linked the list: each link is now numbered in the text,
    once per address, so the model can name a product's own link by its number."""
    monkeypatch.setattr(web_page, 'fetch', lambda url, http, use_browser=False: web_page.Page(url=url, html=LIST_PAGE))
    read = pages.read('https://shop.be/c/dompelpompen', httpx.Client())
    assert 'Einhell GC-DP 7835 [L1] € 53,77' in read.text and 'VONROC Dompelpomp [L2]' in read.text
    assert 'Bekijk [L1]' in read.text and 'Account' not in read.text and '[L3]' not in read.text
    assert read.links == {1: 'https://shop.be/p/einhell-gc-dp-7835', 2: 'https://shop.be/p/vonroc'}
    assert read.products[0]['url'] == 'https://shop.be/p/gardena-9000', 'the product data gives its own link'
