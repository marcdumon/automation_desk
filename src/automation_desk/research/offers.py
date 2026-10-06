"""Facts per vendor page: products with price, brand, model, GTIN and specifications.

Code reads schema.org data first; the model reads the specifications and what the structured data lacks. A price or a
delivery cost is kept only when the page text shows that amount (a structured price also when the page shows no prices).
"""

import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from automation_desk.llm import ask
from automation_desk.research.compare import page_products
from automation_desk.research.money import currency_on_page, price_on_page, shows_prices
from automation_desk.research.pages import Read

MAX_PRODUCTS = 5
SYSTEM = '''You read one web page for a buyer and list the {kind}s on it that fit the buyer's need.
For each: name, brand, model, the price as shown on the page (a number, null if no price is shown), and the specifications
that matter for the need as name/value pairs, values copied from the page with their units, and whether the price includes
VAT (true, false when the page says excl. VAT / excl. BTW / HT / netto, null when it does not say). For a service company
also the contact data (phone, e-mail, address), the region it works in and its reviews (score and count) as on the page.
Links in the text carry a number like [L12]: give the number of the link to each product's own page.
Mark a product free when the page says the product itself costs nothing (free software, a free version).
List at most 5. Say whether the shop delivers to Belgium (yes, no or
unknown when the page does not say) and the delivery cost to Belgium if the page shows it. Never guess a number.'''


class Spec(BaseModel):
    """One specification."""

    name: str
    value: str


class FoundProduct(BaseModel):
    """A product or service company on the page."""

    name: str
    brand: str
    model: str
    price: float | None = Field(description='The price shown on the page, null when none is shown.')
    specs: list[Spec]
    contact: str = Field(description='Phone, e-mail or address of a service company; empty for a product.')
    region: str = Field('', description='The region or municipalities a service company works in; empty for a product.')
    reviews: str = Field('', description='Review score and count of a service company as on the page; empty if none.')
    vat_included: bool | None = Field(None, description='Whether the price includes VAT; null when the page does not say.')
    link: int | None = Field(None, description='The number in [L..] of the link to this product\'s own page; null when none.')
    free: bool = Field(False, description='True when the page says the product itself costs nothing (free software).')


class Facts(BaseModel):
    """What one page offers."""

    products: list[FoundProduct]
    ships_to_belgium: Literal['yes', 'no', 'unknown']
    delivery_cost: float | None


def _key(name: str) -> str:
    """A name compared without case, spaces and punctuation."""
    return re.sub(r'[\W_]+', '', name.casefold())


def _structured(product: dict, text: str, priced: bool) -> dict:
    """A schema.org product as the research keeps it; its price only when the page shows it, or when the page shows no
    prices at all (a price drawn by JavaScript)."""
    price, currency, seen = product['price'], product['currency'], None
    if price is not None:
        seen = currency_on_page(price, text)
        price = None if seen is None and priced else price
        currency = currency or seen or ''
    # CLAUDE> price_seen: the page text shows this price; a price only in the shop's data (drawn by JavaScript, or a
    # misread like Wilo's 127545 in cents) is kept but marked
    return dict(product, price=price, currency=currency, vat_included=product.get('vat_included'), specs={}, model='',
                contact='', region='', reviews='', price_seen=seen is not None)


def extract(read: Read, request: str, kind: str, country: str, http: httpx.Client | None = None) -> dict:
    """The facts of one page, every price and delivery cost checked against the page text; the products the model named
    first, then the other structured ones, at most MAX_PRODUCTS."""
    facts = ask(SYSTEM.format(kind='service company' if kind == 'service' else 'product'),
                f'Need: {request}\n\nPage {read.url}:\n{read.text}', Facts, http=http, purpose='research: read a page')
    structured = [_structured(p, read.text, shows_prices(read.text)) for p in read.products]
    by_name = {_key(p['name']): p for p in structured}
    named: list[dict] = []
    for found in facts.products:
        specs = {s.name: s.value for s in found.specs}
        # CLAUDE> a price of 0 is never a price, even when the page shows '€ 0,00' (free delivery)
        seen = currency_on_page(found.price, read.text) if found.price is not None and found.price > 0 else None
        service = {'contact': found.contact, 'region': found.region, 'reviews': found.reviews}
        # CLAUDE> the model names a link by its number; a number not on the page gives no link, so none is made up
        own = read.links.get(found.link) if found.link is not None else None
        if (known := by_name.get(_key(found.name))) is not None:
            known.update(specs=specs, model=found.model, brand=known['brand'] or found.brand, **service)
            known['url'] = known.get('url') or own
            if known['price'] is None and seen is not None:
                known.update(price=found.price, currency=seen, vat_included=found.vat_included, price_seen=True)
            if known['vat_included'] is None:
                known['vat_included'] = found.vat_included
            if not any(p is known for p in named):
                named.append(known)
            continue
        # CLAUDE> a product the page calls free costs € 0: a real price, not 'no price' (the mindmap research could advise
        # nothing). The model's word counts here; a bare 0 still counts as no price (e.leclerc).
        free = found.free and not found.price
        named.append({'name': found.name, 'brand': found.brand, 'model': found.model, 'gtin': '',
                      'price': 0.0 if free else found.price if seen is not None else None,
                      'currency': 'EUR' if free else seen or '', 'in_stock': None, 'vat_included': found.vat_included,
                      'specs': specs, 'price_seen': free or seen is not None, 'url': own, 'free': free, **service})
    products = page_products([*named, *(p for p in structured if not any(p is n for n in named))])
    delivery = facts.delivery_cost if facts.delivery_cost is not None and price_on_page(facts.delivery_cost, read.text) else None
    ships = 'yes' if country == 'BE' else facts.ships_to_belgium
    return {'url': read.url, 'ships_to_belgium': ships, 'delivery_cost': delivery, 'products': products[:MAX_PRODUCTS]}
