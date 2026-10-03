"""Read one vendor page for a research: its visible text and its schema.org products.

Downloaded when the shop allows it, else read through the user's browser in a background tab that never comes forward.
"""

import json
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from automation_desk.capture import CaptureError
from automation_desk.groups.calendar import web_page
from automation_desk.research.money import amount

NOISE = ('script', 'style', 'noscript', 'nav', 'footer', 'header', 'svg', 'form')


class Unreadable(RuntimeError):
    """Neither a download nor the browser could read the page."""


@dataclass
class Read:
    """A page as the research uses it."""

    url: str
    text: str
    products: list[dict] = field(default_factory=list)
    via: str = 'download'


def page_text(soup: BeautifulSoup, limit: int = 6000) -> str:
    """The visible words of a page without menus, footers and scripts, at most `limit` words."""
    for tag in soup(NOISE):
        tag.decompose()
    return ' '.join(soup.get_text(' ', strip=True).split()[:limit])


def _nodes(node: object) -> list[dict]:
    """Every dict in a JSON-LD tree."""
    if isinstance(node, list):
        return [found for item in node for found in _nodes(item)]
    if not isinstance(node, dict):
        return []
    return [node, *[found for value in node.values() for found in _nodes(value)]]


def _vat(offer: dict) -> bool | None:
    """Whether an offer's price includes VAT, from the offer or its price specification; None when the shop does not say."""
    for node in (offer, *_nodes(offer.get('priceSpecification'))):
        if (flag := node.get('valueAddedTaxIncluded')) is not None:
            return str(flag).casefold() not in ('false', '0')
    return None


def _price(offers: object) -> tuple[float | None, str, bool | None, bool | None]:
    """Price, currency, stock and VAT of the first offer with a price, an offer with VAT included before one without."""
    priced = []
    for offer in _nodes(offers):
        price = amount(offer.get('price', offer.get('lowPrice')))
        # CLAUDE> a price of 0 is a shop's way to say "no price" (e.leclerc: "lowPrice": 0)
        if price is not None and price > 0:
            stock = str(offer.get('availability', ''))
            priced.append((price, str(offer.get('priceCurrency', '')), ('InStock' in stock) if stock else None, _vat(offer)))
    return next((p for p in priced if p[3] is not False), priced[0] if priced else (None, '', None, None))


def _products(node: object) -> list[dict]:
    """The Product nodes of a JSON-LD tree, without the products nested in a product (related or similar ones)."""
    if isinstance(node, list):
        return [found for item in node for found in _products(item)]
    if not isinstance(node, dict):
        return []
    kinds = node.get('@type')
    if 'Product' in (kinds if isinstance(kinds, list) else [kinds]):
        return [node]
    return [found for value in node.values() for found in _products(value)]


def jsonld_products(soup: BeautifulSoup) -> list[dict]:
    """The schema.org Products on a page, with brand, GTIN, price, currency, stock and VAT where the shop gives them."""
    products = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
        except json.JSONDecodeError:
            continue
        for node in _products(data):
            brand = node.get('brand')
            price, currency, in_stock, vat = _price(node.get('offers'))
            products.append({'name': str(node.get('name', '')).strip(),
                             'brand': (brand.get('name', '') if isinstance(brand, dict) else str(brand or '')).strip(),
                             'gtin': str(next((node[k] for k in ('gtin13', 'gtin', 'gtin14', 'gtin12', 'gtin8') if node.get(k)),
                                              '')),
                             'price': price, 'currency': currency, 'in_stock': in_stock, 'vat_included': vat})
    return products


def read(url: str, http: httpx.Client) -> Read:
    """Read a page: a download first, else the browser in a background tab."""
    try:
        page = web_page.fetch(url, http)
    except (httpx.HTTPError, CaptureError):
        try:
            page = web_page.browser_page(url, may_ask=False)
        except CaptureError as error:
            raise Unreadable(f'{url}: {error}') from error
    if page.pdf or not page.html:
        # CLAUDE> a PDF or an empty page has no shop text: no model call is paid for it
        raise Unreadable(f'{url}: a PDF or an empty page, not a shop page')
    soup = BeautifulSoup(page.html, 'lxml')
    products = jsonld_products(soup)
    return Read(url=page.url, text=page_text(soup), products=products, via=page.via)
