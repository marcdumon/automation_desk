"""Daily task statistics from Todoist, kept in the ledger for the Tasks page.

Todoist remembers what got done, but not how many tasks were open on a day. So every morning, the first time the app runs
after 07:00, it writes down what is open; what got done and added per day is read back from Todoist and can be refreshed
at any time. The morning count also keeps which tasks are dated that day (planned), so the share of them done that day
is known later, repeating ones included (Todoist keeps a done repeating task's id). No model is involved.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta

from automation_desk.ledger import connect
from automation_desk.todoist import Todoist

MORNING = time(7)
HORIZONS = ('this_week', 'this_month', 'this_year', 'next_year')
SOMEDAY = 'someday'
FROG = 'frog'
CLEANUP = 'big cleanup'
WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
COUNTS = ('open', 'overdue', 'someday', *HORIZONS)


def take_snapshot(todoist: Todoist, now: datetime) -> bool:
    """Write down what is open this morning, once a day from 07:00 on; True when a snapshot was taken now."""
    today = now.date()
    if now.time() < MORNING or days(today, today):
        return False
    projects = {p['id']: p['name'] for p in todoist.projects()}
    someday = {s['id'] for s in todoist.sections() if s['name'].strip().casefold() == SOMEDAY}
    tasks = todoist.open_tasks()
    counts = {'open': len(tasks),
              'overdue': sum(1 for t in tasks if t.get('due') and t['due']['date'][:10] < today.isoformat()),
              'someday': sum(1 for t in tasks if t.get('section_id') in someday),
              **{h: sum(1 for t in tasks if h in t.get('labels', [])) for h in HORIZONS}}
    planned = [t['id'] for t in tasks if t.get('due') and t['due']['date'][:10] == today.isoformat()]
    save_snapshot(today, counts, Counter(projects.get(t['project_id'], '?') for t in tasks), now, planned)
    return True


def save_snapshot(day: date, counts: dict, per_project: dict[str, int], taken: datetime | None = None,
                  planned: list[str] | None = None) -> None:
    """Store one morning's counts and the tasks planned for the day (dated that day)."""
    with connect(write=True) as db:
        columns, marks = ', '.join(COUNTS), ', '.join('?' * len(COUNTS))
        db.execute(f"INSERT OR REPLACE INTO task_days (day, taken_at, {columns}) VALUES (?, ?, {marks})",
                   (day.isoformat(), (taken or datetime.now()).isoformat(timespec='seconds'), *(counts[c] for c in COUNTS)))
        db.execute('DELETE FROM task_day_projects WHERE day = ?', (day.isoformat(),))
        db.executemany('INSERT INTO task_day_projects (day, project, open) VALUES (?, ?, ?)',
                       [(day.isoformat(), name, count) for name, count in per_project.items()])
        db.execute('DELETE FROM task_day_planned WHERE day = ?', (day.isoformat(),))
        db.executemany('INSERT INTO task_day_planned (day, task_id) VALUES (?, ?)', [(day.isoformat(), t) for t in planned or []])


def _local_day(stamp: str, now: datetime) -> date:
    """The user's local day of a UTC timestamp from Todoist."""
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(now.tzinfo).date()


def refresh_activity(todoist: Todoist, now: datetime, days: int = 7) -> None:
    """Read back from Todoist how many tasks got done and added on each of the last `days` days, and whether a frog did."""
    first = now.date() - timedelta(days=days - 1)
    since = datetime.combine(first, time.min, now.tzinfo)
    completed = todoist.completed(since, now)
    added = Counter(_local_day(t['added_at'], now) for t in [*todoist.open_tasks(), *completed] if t.get('added_at'))
    finished = Counter(_local_day(t['completed_at'], now) for t in completed if t.get('completed_at'))
    frogs = {_local_day(t['completed_at'], now) for t in completed if t.get('completed_at') and FROG in t.get('labels', [])}
    rows = [((first + timedelta(days=n)).isoformat(), finished[d], added[d], int(d in frogs))
            for n in range(days) if (d := first + timedelta(days=n))]
    done_on: dict[str, set[str]] = {}
    for t in completed:
        if t.get('completed_at'):
            done_on.setdefault(_local_day(t['completed_at'], now).isoformat(), set()).add(t['id'])
    with connect(write=True) as db:
        db.executemany('INSERT OR REPLACE INTO task_activity (day, completed, added, frog) VALUES (?, ?, ?, ?)', rows)
        # CLAUDE> a planned task counts as done only when it was completed on its planned day
        for day, *_ in rows:
            finished_ids = done_on.get(day, set())
            for row in db.execute('SELECT task_id FROM task_day_planned WHERE day = ?', (day,)).fetchall():
                db.execute('UPDATE task_day_planned SET done = ? WHERE day = ? AND task_id = ?',
                           (int(row['task_id'] in finished_ids), day, row['task_id']))


def days(first: date, last: date) -> list[dict]:
    """The morning snapshots in a period, with how many tasks were planned for each day and how many of those got done."""
    with connect() as db:
        rows = db.execute('SELECT d.*, COUNT(p.task_id) AS planned, COALESCE(SUM(p.done), 0) AS planned_done FROM task_days d '
                          'LEFT JOIN task_day_planned p ON p.day = d.day WHERE d.day BETWEEN ? AND ? GROUP BY d.day ORDER BY d.day',
                          (first.isoformat(), last.isoformat()))
        return [dict(r) for r in rows.fetchall()]


def activity(first: date, last: date) -> list[dict]:
    """Done, added and frog per day in a period."""
    with connect() as db:
        rows = db.execute('SELECT * FROM task_activity WHERE day BETWEEN ? AND ? ORDER BY day', (first.isoformat(), last.isoformat()))
        return [{**dict(r), 'frog': bool(r['frog'])} for r in rows.fetchall()]


def projects_on(day: date) -> dict[str, int]:
    """Open tasks per project on a morning."""
    with connect() as db:
        rows = db.execute('SELECT project, open FROM task_day_projects WHERE day = ?', (day.isoformat(),)).fetchall()
    return {r['project']: r['open'] for r in rows}


def _cleanup(snapshots: list[dict]) -> dict | None:
    """The Big Cleanup project's pace over the period and the day it runs empty at that pace; None without the project."""
    counts = [(date.fromisoformat(s['day']), projects_on(date.fromisoformat(s['day']))) for s in snapshots]
    named = [(day, name, per[name]) for day, per in counts for name in per if CLEANUP in name.casefold()]
    if not named:
        return None
    (first_day, _, first_open), (last_day, name, last_open) = named[0], named[-1]
    span = (last_day - first_day).days
    per_day = round((first_open - last_open) / span, 2) if span else 0.0
    empty_on = (last_day + timedelta(days=round(last_open / per_day))).isoformat() if per_day > 0 else None
    return {'project': name, 'open': last_open, 'per_day': per_day, 'empty_on': empty_on}


def overview(today: date, period: int = 30) -> dict:
    """Everything the Tasks page shows for the last `period` days (0 = everything recorded)."""
    first = today - timedelta(days=period - 1) if period else date(2000, 1, 1)
    snapshots = days(first, today)
    done = {a['day']: a for a in activity(first, today)}
    by_day = {s['day']: s for s in snapshots}
    start = first if period else (date.fromisoformat(min([*by_day, *done])) if by_day or done else today)
    series = []
    for n in range((today - start).days + 1):
        day = (start + timedelta(days=n)).isoformat()
        snap, act = by_day.get(day, {}), done.get(day, {})
        series.append({'day': day, **{c: snap.get(c) for c in COUNTS}, 'planned': snap.get('planned'),
                       'planned_done': snap.get('planned_done'), 'completed': act.get('completed', 0),
                       'added': act.get('added', 0), 'frog': act.get('frog', False)})
    week = [s for s in series if s['day'] > (today - timedelta(days=7)).isoformat()]
    week_ago = by_day.get((today - timedelta(days=7)).isoformat())
    now_counts = snapshots[-1] if snapshots else {}
    weekdays = [{'label': label, 'average': 0.0, 'plan_pct': None} for label in WEEKDAYS]
    per_weekday: dict[int, list[dict]] = {}
    for s in series:
        per_weekday.setdefault(date.fromisoformat(s['day']).weekday(), []).append(s)
    for index, values in per_weekday.items():
        weekdays[index]['average'] = round(sum(v['completed'] for v in values) / len(values), 1)
        planned = sum(v['planned'] or 0 for v in values)
        weekdays[index]['plan_pct'] = round(100 * sum(v['planned_done'] or 0 for v in values) / planned) if planned else None
    latest = projects_on(date.fromisoformat(snapshots[-1]['day'])) if snapshots else {}
    earlier = projects_on(date.fromisoformat(snapshots[0]['day'])) if snapshots else {}
    month = [s for s in series if s['day'][:7] == today.isoformat()[:7]]
    return {
        'kpis': {'open': {'now': now_counts.get('open'), 'week_ago': week_ago['open'] if week_ago else None,
                          'first': snapshots[0]['open'] if snapshots else None},
                 'done_7d': sum(s['completed'] for s in week), 'added_7d': sum(s['added'] for s in week),
                 'plan_7d': {'planned': sum(s['planned'] or 0 for s in week), 'done': sum(s['planned_done'] or 0 for s in week)},
                 'frogs_month': sum(1 for s in month if s['frog']), 'days_month': len(month)},
        'series': series,
        'weekdays': weekdays,
        'projects': sorted(({'project': name, 'open': count, 'change': count - earlier.get(name, count)}
                            for name, count in latest.items()), key=lambda p: (-p['open'], p['project'])),
        'horizons': [{'label': c, 'open': now_counts.get(c, 0)} for c in (*HORIZONS, 'someday')],
        'cleanup': _cleanup(snapshots),
        'since': snapshots[0]['day'] if snapshots else None,
    }
