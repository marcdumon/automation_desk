"""The user's habits, kept in the ledger, outside Todoist: one evening check-in instead of a task per habit per day.

A habit has a rhythm: every day ('daily'), on weekdays ('weekdays'), on set days ('days:mon,wed,fri') or a number of times a
week ('weekly:3', shown every day until the week is met). Streaks count the days (or, for weekly habits, the weeks) the
habit was due and done; today does not break a streak while it can still be ticked. No model is involved.
"""

from datetime import date, datetime, timedelta

from automation_desk.ledger import connect

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
SCHEDULE_HELP = "every day, weekdays, set days ('days:mon,wed') or times a week ('weekly:3')"


def _days(schedule: str) -> set[int]:
    """The weekdays of a 'days:' rhythm, Monday 0."""
    return {DAYS.index(d) for d in schedule.removeprefix('days:').split(',') if d in DAYS}


def _valid(schedule: str) -> str:
    """A rhythm as stored, or ValueError in plain words."""
    schedule = schedule.strip().casefold()
    if schedule in ('daily', 'weekdays') or (schedule.startswith('days:') and _days(schedule)):
        return schedule
    if schedule.startswith('weekly:') and schedule[7:].isdigit() and 1 <= int(schedule[7:]) <= 7:
        return schedule
    raise ValueError(f'A habit repeats {SCHEDULE_HELP}, not {schedule!r}.')


def is_due(schedule: str, day: date, done_this_week: int = 0) -> bool:
    """Whether a habit asks for a tick on a day; a weekly one until its number is reached that week."""
    if schedule == 'daily':
        return True
    if schedule == 'weekdays':
        return day.weekday() < 5
    if schedule.startswith('days:'):
        return day.weekday() in _days(schedule)
    return done_this_week < int(schedule.split(':')[1])


def add(name: str, schedule: str, created: date | None = None) -> int:
    """A new habit; returns its id."""
    if not name.strip():
        raise ValueError('A habit needs a name.')
    with connect(write=True) as db:
        last = db.execute('SELECT COALESCE(MAX(COALESCE(position, id)), 0) FROM habits').fetchone()[0]
        cursor = db.execute('INSERT INTO habits (name, schedule, created, position) VALUES (?, ?, ?, ?)',
                            (' '.join(name.split()), _valid(schedule), (created or date.today()).isoformat(), last + 1))
        return int(cursor.lastrowid)


def update(habit_id: int, name: str | None = None, schedule: str | None = None, paused: bool | None = None) -> None:
    """Rename a habit, change its rhythm, or pause it (a paused habit is never due and keeps its history)."""
    with connect(write=True) as db:
        if name is not None and name.strip():
            db.execute('UPDATE habits SET name = ? WHERE id = ?', (' '.join(name.split()), habit_id))
        if schedule is not None:
            db.execute('UPDATE habits SET schedule = ? WHERE id = ?', (_valid(schedule), habit_id))
        if paused is not None:
            db.execute('UPDATE habits SET paused = ? WHERE id = ?', (int(paused), habit_id))


def reorder(ids: list[int]) -> None:
    """Put the habits in this order; habits not named keep their place after them."""
    with connect(write=True) as db:
        for position, habit_id in enumerate(ids, 1):
            db.execute('UPDATE habits SET position = ? WHERE id = ?', (position, habit_id))
        rest = [r[0] for r in db.execute('SELECT id FROM habits ORDER BY COALESCE(position, id), id').fetchall() if r[0] not in ids]
        for position, habit_id in enumerate(rest, len(ids) + 1):
            db.execute('UPDATE habits SET position = ? WHERE id = ?', (position, habit_id))


def delete(habit_id: int) -> None:
    """Remove a habit and its ticks."""
    with connect(write=True) as db:
        db.execute('DELETE FROM habit_checks WHERE habit_id = ?', (habit_id,))
        db.execute('DELETE FROM habits WHERE id = ?', (habit_id,))


def check(habit_id: int, day: date, done: bool) -> None:
    """Tick or untick a habit for a day."""
    with connect(write=True) as db:
        if done:
            db.execute('INSERT OR IGNORE INTO habit_checks (habit_id, day) VALUES (?, ?)', (habit_id, day.isoformat()))
        else:
            db.execute('DELETE FROM habit_checks WHERE habit_id = ? AND day = ?', (habit_id, day.isoformat()))


def _monday(day: date) -> date:
    """The Monday of a day's week."""
    return day - timedelta(days=day.weekday())


def _week_count(done: set[date], day: date) -> int:
    """Ticks in the week of `day`, before that day."""
    return sum(1 for d in done if _monday(d) == _monday(day) and d < day)


def _streaks(habit: dict, done: set[date], today: date) -> tuple[int, int]:
    """The current and the longest streak: due days in a row done (weeks met, for a weekly habit)."""
    created = date.fromisoformat(habit['created'])
    if habit['schedule'].startswith('weekly:'):
        target = int(habit['schedule'].split(':')[1])
        units, week = [], _monday(created)
        while week <= today:
            units.append((week, sum(1 for d in done if _monday(d) == week) >= target))
            week += timedelta(days=7)
        # CLAUDE> the running week can still be met
        running = _monday(today)
    else:
        units = [(created + timedelta(days=n), created + timedelta(days=n) in done)
                 for n in range((today - created).days + 1) if is_due(habit['schedule'], created + timedelta(days=n))]
        # CLAUDE> today can still be ticked
        running = today
    best = run = 0
    for _, met in units:
        run = run + 1 if met else 0
        best = max(best, run)
    streak = 0
    for unit, met in reversed(units):
        if unit == running and not met:
            continue
        if not met:
            break
        streak += 1
    return streak, best


def _month(habit: dict, done: set[date], today: date) -> tuple[list[dict], int | None]:
    """This month's days with their state, and the share of the due days done (None when nothing was due yet)."""
    first, created = today.replace(day=1), date.fromisoformat(habit['created'])
    days, due, hit = [], 0, 0
    day = first
    while day.month == today.month:
        weekly = habit['schedule'].startswith('weekly:')
        if day > today:
            state = 'future'
        elif day in done:
            state = 'done'
        elif day < created or habit['paused'] or not is_due(habit['schedule'], day, _week_count(done, day)):
            state = 'free'
        elif day == today:
            state = 'open'
        else:
            state = 'free' if weekly else 'missed'
        if state in ('done', 'missed') and day >= created:
            due += 1
            hit += state == 'done'
        days.append({'day': day.isoformat(), 'state': state})
        day += timedelta(days=1)
    return days, round(100 * hit / due) if due else None


def _all() -> tuple[list[dict], dict[int, set[date]]]:
    """Every habit and its ticks."""
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM habits ORDER BY COALESCE(position, id), id').fetchall()]
        checks = db.execute('SELECT habit_id, day FROM habit_checks').fetchall()
    done: dict[int, set[date]] = {r['id']: set() for r in rows}
    for c in checks:
        done.setdefault(c['habit_id'], set()).add(date.fromisoformat(c['day']))
    return rows, done


def overview(today: date) -> dict:
    """What the Habits page shows: today's check-in, and per habit its streaks, month grid and share."""
    rows, done = _all()
    listed, due_today = [], []
    for habit in rows:
        ticks = done[habit['id']]
        streak, best = _streaks(habit, ticks, today)
        days, share = _month(habit, ticks, today)
        listed.append({'id': habit['id'], 'name': habit['name'], 'schedule': habit['schedule'], 'paused': bool(habit['paused']),
                       'streak': streak, 'best': best, 'month_pct': share, 'days': days})
        ticked = today in ticks
        if not habit['paused'] and (ticked or is_due(habit['schedule'], today, _week_count(ticks, today))):
            due_today.append({'id': habit['id'], 'name': habit['name'], 'done': ticked, 'streak': streak})
    return {'day': today.isoformat(), 'today': due_today, 'habits': listed, 'reminder': reminder()}


def reminder() -> str:
    """The daily reminder time ('21:00'), or '' when off."""
    with connect() as db:
        row = db.execute("SELECT value FROM habit_settings WHERE key = 'reminder'").fetchone()
    return row[0] if row else ''


def set_reminder(at: str) -> None:
    """Set the daily reminder ('21:00'), or switch it off with ''."""
    at = at.strip()
    if at:
        datetime.strptime(at, '%H:%M')
    with connect(write=True) as db:
        db.execute("INSERT OR REPLACE INTO habit_settings (key, value) VALUES ('reminder', ?)", (at,))


def mark_reminded(day: date) -> None:
    """Remember that today's reminder went out."""
    with connect(write=True) as db:
        db.execute("INSERT OR REPLACE INTO habit_settings (key, value) VALUES ('reminded', ?)", (day.isoformat(),))


def reminder_text(now: datetime) -> str | None:
    """The reminder to show now: once a day, from its time on, while habits due today are still open; else None."""
    at = reminder()
    if not at or now.strftime('%H:%M') < at:
        return None
    with connect() as db:
        row = db.execute("SELECT value FROM habit_settings WHERE key = 'reminded'").fetchone()
    if row and row[0] == now.date().isoformat():
        return None
    open_ = [h['name'] for h in overview(now.date())['today'] if not h['done']]
    if not open_:
        return None
    return f"Habits: {len(open_)} still open today ({', '.join(open_)})"
