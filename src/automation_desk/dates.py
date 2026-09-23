"""Turn the model's relative expressions into dates. The model never writes a date; this module does.

Accepted day expressions (case-insensitive):
    today, tomorrow, yesterday, day after tomorrow
    monday..sunday          the next such day strictly after today
    this <weekday>          that day in the current Monday-Sunday week
    next <weekday>          that day in next week
    in N days|weeks, N days|weeks ago
    <day expr>+N / -N       offset in days, e.g. today+2, friday+7
    <month> <day>, <day> <month>   only when those words appear verbatim in the user's sentence
Range expressions: this|next|last week, this|next month, this|next weekend, or '<day expr> to <day expr>'.
Numeric and ISO dates are always rejected.
"""

import re
from datetime import date, time, timedelta

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
MONTHS = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3, 'april': 4, 'apr': 4, 'may': 5,
    'june': 6, 'jun': 6, 'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}
_NUMERIC_DATE = re.compile(r'\d{1,4}\s*[-/.]\s*\d{1,2}|\d{4}')
_OFFSET = re.compile(r'^(.+?)\s*([+-])\s*(\d+)\s*(d|days?|w|weeks?)?$')
_IN_N = re.compile(r'^in\s+(\d+)\s+(days?|weeks?)$')
_AGO = re.compile(r'^(\d+)\s+(days?|weeks?)\s+ago$')
_MONTH_DAY = re.compile(r'^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?$')
_DAY_MONTH = re.compile(r'^(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)$')
_TIME = re.compile(r'^(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm|h)?$')


class DateExprError(ValueError):
    """An expression outside the grammar, or a date the model made up."""


def _norm(text: str) -> str:
    """Lowercase and collapse whitespace."""
    return ' '.join(text.lower().split())


def _explicit(month: int, day: int, today: date) -> date:
    """A month/day without a year: its next occurrence, today included."""
    try:
        candidate = date(today.year, month, day)
        if candidate < today:
            candidate = date(today.year + 1, month, day)
    except ValueError as error:
        raise DateExprError(f'{day}/{month} is not a real date') from error
    return candidate


def resolve_day(expr: str, today: date, sentence: str = '') -> date:
    """Resolve one day expression relative to `today`."""
    e = _norm(expr).removeprefix('on ')
    if not e:
        raise DateExprError('empty date expression')

    if m := _OFFSET.match(e):
        base, sign, n, unit = m.groups()
        days = int(n) * (7 if unit and unit.startswith('w') else 1)
        return resolve_day(base, today, sentence) + timedelta(days=days if sign == '+' else -days)
    if m := _IN_N.match(e):
        return today + timedelta(days=int(m[1]) * (7 if m[2].startswith('w') else 1))
    if m := _AGO.match(e):
        return today - timedelta(days=int(m[1]) * (7 if m[2].startswith('w') else 1))

    fixed = {'today': 0, 'tomorrow': 1, 'yesterday': -1, 'day after tomorrow': 2, 'the day after tomorrow': 2}
    if e in fixed:
        return today + timedelta(days=fixed[e])

    words = e.split()
    if words[-1] in WEEKDAYS and len(words) <= 2:
        target = WEEKDAYS.index(words[-1])
        monday = today - timedelta(days=today.weekday())
        if len(words) == 1:
            return today + timedelta(days=(target - today.weekday() - 1) % 7 + 1)
        if words[0] == 'this':
            return monday + timedelta(days=target)
        if words[0] == 'next':
            return monday + timedelta(days=7 + target)
        raise DateExprError(f'unknown weekday form: {expr!r}')

    explicit = _MONTH_DAY.match(e) or _DAY_MONTH.match(e)
    if explicit:
        a, b = explicit.groups()
        month_word, day = (a, int(b)) if a.isalpha() else (b, int(a))
        if month_word not in MONTHS:
            raise DateExprError(f'unknown month in {expr!r}')
        if _norm(expr) not in _norm(sentence):
            raise DateExprError(f'{expr!r} is not in your sentence; the model may not invent dates')
        return _explicit(MONTHS[month_word], day, today)

    if _NUMERIC_DATE.search(e):
        raise DateExprError(f'{expr!r} is a numeric date; only relative expressions are accepted')
    raise DateExprError(f'cannot understand the date {expr!r}')


def resolve_range(expr: str, today: date, sentence: str = '') -> tuple[date, date]:
    """Resolve a range expression to (first day, last day), both inclusive."""
    e = _norm(expr)
    monday = today - timedelta(days=today.weekday())
    weeks = {'this week': 0, 'next week': 1, 'last week': -1}
    if e in weeks:
        start = monday + timedelta(weeks=weeks[e])
        return start, start + timedelta(days=6)
    weekends = {'this weekend': 0, 'next weekend': 1}
    if e in weekends:
        saturday = monday + timedelta(days=5, weeks=weekends[e])
        return saturday, saturday + timedelta(days=1)
    if e in ('this month', 'next month'):
        year, month = today.year, today.month + (e == 'next month')
        if month == 13:
            year, month = year + 1, 1
        first = date(year, month, 1)
        following = date(year + (month == 12), month % 12 + 1, 1)
        return first, following - timedelta(days=1)
    for separator in (' to ', ' until ', ' till ', ' through '):
        if separator in e:
            left, right = e.split(separator, 1)
            start, end = resolve_day(left, today, sentence), resolve_day(right, today, sentence)
            if end < start:
                raise DateExprError(f'{expr!r} ends before it starts')
            return start, end
    day = resolve_day(e, today, sentence)
    return day, day


def resolve_time(expr: str) -> time:
    """Resolve a clock time such as 3pm, 15:00, 20h30, noon."""
    e = _norm(expr).removeprefix('at ')
    if e in ('noon', 'midday'):
        return time(12)
    if e == 'midnight':
        return time(0)
    m = _TIME.match(e)
    if not m:
        raise DateExprError(f'cannot understand the time {expr!r}')
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3] == 'pm' and hour < 12:
        hour += 12
    if m[3] == 'am' and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise DateExprError(f'{expr!r} is not a valid time')
    return time(hour, minute)


def weekday_index(name: str) -> int:
    """Monday=0 .. Sunday=6, accepting plurals like 'fridays'."""
    n = _norm(name).removesuffix('s')
    if n not in WEEKDAYS:
        raise DateExprError(f'{name!r} is not a weekday')
    return WEEKDAYS.index(n)


def label(day: date) -> str:
    """A date as shown in previews: 'Thu 24 Sep 2026'."""
    return day.strftime('%a %d %b %Y')


_AGE = re.compile(r'^(?:an?|one|(\d+))\s*(d|days?|w|weeks?|m|months?|y|years?)$')


def gmail_age(expr: str) -> str:
    """An age such as '2 weeks', 'a month', '30 days' in Gmail's own relative form ('14d', '1m'); code does this, not the model."""
    m = _AGE.match(_norm(expr))
    if not m:
        raise DateExprError(f"cannot understand the age {expr!r}; say e.g. '2 weeks', '30 days', 'a month'")
    count = int(m[1] or 1)
    unit = m[2][0]
    return f'{count * 7}d' if unit == 'w' else f'{count}{unit}'
