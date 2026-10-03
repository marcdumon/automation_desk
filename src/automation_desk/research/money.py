"""Amounts of money as shops write them: one parser for European and US spellings, and the check that a page shows an
amount next to a currency sign (which also gives the currency)."""

import re

# CLAUDE> the signs a price stands next to, and their currency; 'eur' only as a word end, so 'Europe' never counts
SIGNS = r'€|eur\b|euros?\b|\$|usd\b|£|gbp\b'
WIDE_SPACES = str.maketrans({'\u00a0': ' ', '\u202f': ' '})


def amount(value: object) -> float | None:
    """'1.299' / '1.299,00' / '1,299.00' / '1 299,00' / '149,-' / a JSON number as a float; None when it is no amount."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    found = re.fullmatch(r'\D*?(\d[\d., ]*?)[.,\-\s]*\D*', str(value).translate(WIDE_SPACES).strip())
    if not found:
        return None
    text = found.group(1).replace(' ', '')
    dot, comma = text.rfind('.'), text.rfind(',')
    if dot >= 0 and comma >= 0:
        decimal, thousands = ('.', ',') if dot > comma else (',', '.')
        whole, _, cents = text.rpartition(decimal)
        whole = whole.replace(thousands, '')
        return float(f'{whole}.{cents}') if whole.isdigit() and cents.isdigit() else None
    separator = '.' if dot >= 0 else ',' if comma >= 0 else ''
    if not separator:
        return float(text)
    parts = text.split(separator)
    # CLAUDE> one separator before exactly three digits, or several separators, mark thousands ('1.299' is 1299)
    if (len(parts) > 2 or len(parts[1]) == 3) and parts[0] != '0':
        grouped = 1 <= len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:])
        return float(''.join(parts)) if grouped else None
    return float(f'{parts[0]}.{parts[1]}') if len(parts) == 2 else None


def _spellings(price: float) -> set[str]:
    """The ways a shop writes an amount: 1.234,56 / 1 234,56 / 1234,56 / 1,234.56 / 1234.56, and whole euros as
    149 / 149,- / 149,00."""
    whole, cents = f'{price:.2f}'.split('.')
    commas = f'{int(whole):,}'
    dots, spaces = commas.replace(',', '.'), commas.replace(',', ' ')
    forms = {f'{whole},{cents}', f'{whole}.{cents}', f'{dots},{cents}', f'{commas}.{cents}', f'{spaces},{cents}'}
    if cents == '00':
        forms |= {whole, dots, commas, spaces, f'{whole},-', f'{whole}.-', f'{dots},-', f'{spaces},-'}
    return forms


def _currency(sign: str) -> str:
    """The currency code of a sign as written on the page."""
    sign = sign.casefold()
    return 'USD' if sign in ('$', 'usd') else 'GBP' if sign in ('£', 'gbp') else 'EUR'


def currency_on_page(price: float, text: str) -> str | None:
    """The currency of the sign next to this amount on the page ('EUR', 'USD', 'GBP'); None when the page does not show
    the amount as money."""
    text = text.translate(WIDE_SPACES)
    for form in sorted(_spellings(price), key=len, reverse=True):
        # CLAUDE> not a part of a longer number: '299' is not in '1 299,00' and '1' is not in '1 299'
        number = rf'(?<![\d.,])(?<!\d ){re.escape(form)}(?![\d]|[.,]\d| \d{{3}}(?!\d))'
        found = re.search(rf'(?P<before>{SIGNS})\s*{number}|{number}\s*(?P<after>{SIGNS})', text, re.IGNORECASE)
        if found:
            return _currency(found.group('before') or found.group('after'))
    # CLAUDE> the French shop spelling '26 €90': the euro sign stands between the euros and the cents
    whole, cents = f'{price:.2f}'.split('.')
    for form in {whole, f'{int(whole):,}'.replace(',', '.'), f'{int(whole):,}'.replace(',', ' ')}:
        if re.search(rf'(?<![\d.,])(?<!\d ){re.escape(form)}\s*€\s*{cents}(?!\d)', text):
            return 'EUR'
    return None


def price_on_page(price: float, text: str) -> bool:
    """Whether the page shows this amount as money, in any usual spelling."""
    return currency_on_page(price, text) is not None


def shows_prices(text: str) -> bool:
    """Whether the page text shows any amount next to a currency sign."""
    return re.search(rf'(?:{SIGNS})\s*\d|\d\s*(?:{SIGNS})', text.translate(WIDE_SPACES), re.IGNORECASE) is not None
