"""Compare the products the pages offer: one row per product with its shops, cheapest total first. No model."""

import re
from statistics import median
from urllib.parse import urlsplit

OVER_BUDGET_SHOWN = 3
# CLAUDE> a total this many times the budget (or, without one, the middle total) is a misread, not a price: Wilo's data said
# 127545 for a pump of about € 1,275 (research 1)
IMPLAUSIBLE = 10


def _key(product: dict) -> str:
    """Same product: same GTIN, else same brand and model, else same name (case, spaces and dashes ignored)."""
    if product.get('gtin'):
        return f'gtin:{product["gtin"]}'
    model = re.sub(r'[\W_]+', '', product.get('model', '').casefold())
    brand = re.sub(r'[\W_]+', '', product.get('brand', '').casefold())
    return f'model:{brand}:{model}' if model else f'name:{re.sub(r"[\W_]+", "", product["name"].casefold())}'


def _norm(text: str) -> str:
    """Text compared without case, spaces and punctuation."""
    return re.sub(r'[\W_]+', '', text.casefold())


def title(product: dict) -> str:
    """The name a person can tell the product by: the shop's name, with brand and model when the name lacks the model
    (vervelde.com names five Pedrollo pumps "Dompelpomp met vlotter")."""
    model = product.get('model', '')
    if not model or _norm(model) in _norm(product['name']):
        return product['name']
    return f'{product["name"]} — {" ".join(part for part in (product.get("brand", ""), model) if part)}'


def _drop_implausible(rows: list[dict], budget: float | None) -> None:
    """Take away prices far above the budget, or without one far above the middle price: they are misreads."""
    totals = [o['total'] for r in rows for o in r['offers'] if o['total'] is not None]
    if not totals:
        return
    ceiling = IMPLAUSIBLE * (budget if budget else median(totals))
    # CLAUDE> only a price the page did not show can be a misread; a price the page shows is just over the budget
    for offer in (o for r in rows for o in r['offers']):
        if offer['total'] is not None and offer['total'] > ceiling and not offer['price_seen']:
            offer.update(price=None, total=None, implausible=True)


def _same(a: dict, b: dict) -> bool:
    """One product listed twice on a page: the same price, and one name is the start of the other (a short and a long title)."""
    if a.get('price') is None or a.get('price') != b.get('price'):
        return False
    short, long = sorted((_norm(a['name']), _norm(b['name'])), key=len)
    return bool(short) and long.startswith(short)


def page_products(products: list[dict]) -> list[dict]:
    """A page's products each once and with a real price: a price of 0 is none (e.leclerc), and a later listing of the
    same product fills what the first one lacks (GTIN, brand, model, specs). Also for pages read before these checks."""
    kept: list[dict] = []
    for product in products:
        product = dict(product)
        if product.get('price') is not None and product['price'] <= 0:
            product['price'] = None
        if (first := next((k for k in kept if _same(k, product)), None)) is None:
            kept.append(product)
            continue
        for field, value in product.items():
            if field == 'specs':
                first['specs'] = (value or {}) | (first.get('specs') or {})
            elif first.get(field) in (None, '') and value not in (None, ''):
                first[field] = value
    return kept


def _competes(offer: dict) -> bool:
    """An offer whose total can be the product's cheapest: a euro total with VAT (or VAT not stated)."""
    return offer['total'] is not None and offer['currency'] == 'EUR' and not offer['ex_vat']


def table(pages: list[dict], budget: float | None, countries: list[str] | None = None) -> dict:
    """Products with their offers, split into inside the budget, over it (3 cheapest) and without a price. Pages from a
    country the user did not choose are left out (also those read before that rule); pages without a country stay."""
    rows: dict[str, dict] = {}
    for page in pages:
        if countries and page.get('country') and page['country'] not in countries:
            continue
        facts = page.get('facts') or {}
        if facts.get('ships_to_belgium') == 'no':
            continue
        for product in page_products(facts.get('products', [])):
            row = rows.setdefault(_key(product), {'name': product['name'], 'title': title(product),
                                                 'brand': product.get('brand', ''),
                                                 'model': product.get('model', ''), 'gtin': product.get('gtin', ''),
                                                 'specs': {}, 'offers': []})
            row['specs'] |= {k: v for k, v in product.get('specs', {}).items() if k not in row['specs']}
            price, delivery = product.get('price'), facts.get('delivery_cost')
            total = None if price is None else round(price + (delivery or 0), 2)
            # CLAUDE> the product's own page when the shop page names it (a list page lists many); else the page read
            row['offers'].append({'shop': urlsplit(page['url']).netloc.removeprefix('www.'), 'url': product.get('url') or page['url'],
                                  'country': page.get('country', ''), 'price': price, 'currency': product.get('currency', ''),
                                  'delivery': delivery, 'total': total, 'ships_to_belgium': facts.get('ships_to_belgium'),
                                  'in_stock': product.get('in_stock'), 'ex_vat': product.get('vat_included') is False,
                                  'contact': product.get('contact', ''), 'region': product.get('region', ''),
                                  'reviews': product.get('reviews', ''), 'price_seen': product.get('price_seen', True),
                                  'implausible': False})
    _drop_implausible(list(rows.values()), budget)
    inside, over, no_price = [], [], []
    for row in rows.values():
        # CLAUDE> only euro totals with VAT compete; an unknown currency ('') is not taken for euro, a price without VAT is
        # shown marked but never the cheapest total
        row['offers'].sort(key=lambda o: (not _competes(o), o['total'] is None, o['total'] or 0))
        euro = [o['total'] for o in row['offers'] if _competes(o)]
        row['best_total'] = min(euro) if euro else None
        if row['best_total'] is None:
            no_price.append(row)
        elif budget is not None and row['best_total'] > budget:
            over.append(row)
        else:
            inside.append(row)
    inside.sort(key=lambda r: r['best_total'])
    over = sorted(over, key=lambda r: r['best_total'])[:OVER_BUDGET_SHOWN]
    for n, row in enumerate([*inside, *over, *no_price], 1):
        row['n'] = n
    return {'products': inside, 'over_budget': over, 'no_price': no_price}
