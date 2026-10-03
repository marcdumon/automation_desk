"""HTTP API and static GUI. Every route is generic over task groups and standard tasks.

interpret: sentence -> model fills the task's arguments -> code resolves against Google -> frozen plan + preview.
execute:   plan id + ticked rows -> exactly the previewed changes are applied.
"""

import hashlib
import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field

from automation_desk import google_auth, habits, jobs, ledger, llm, reminders, stop
from automation_desk.capture import EXTENSION_DIR, Captured, CaptureError, broker
from automation_desk.config import ROOT, config
from automation_desk.dates import DateExprError
from automation_desk.google_auth import AuthError
from automation_desk.groups import GROUPS
from automation_desk.groups.base import Context, Preview, TaskGroup, UserError
from automation_desk.groups.calendar import watch
from automation_desk.groups.calendar.client import timezone
from automation_desk.groups.calendar.tasks import check_watched
from automation_desk.groups.news import digest as news_digest
from automation_desk.groups.news import store as news
from automation_desk.groups.news.sites import save_site_list
from automation_desk.groups.tasks import stats as task_stats_store
from automation_desk.interpret import fill_args, route
from automation_desk.llm import LLMError
from automation_desk.plans import Plan, PlanStore
from automation_desk.research import export as research_export
from automation_desk.research import run as research_run
from automation_desk.research import store as research_store
from automation_desk.todoist import TodoistError
from automation_desk.tools import pdf_margin

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / 'static'

app = FastAPI(title='automation_desk')
plans = PlanStore()


@cache
def user_timezone() -> ZoneInfo:
    """The Calendar timezone, read once; the configured fallback when Google cannot be asked."""
    try:
        return ZoneInfo(timezone(google_auth.service('calendar', 'v3')))
    except (AuthError, HttpError) as error:
        log.warning('using fallback timezone: %s', error)
        return ZoneInfo(config().fallback_timezone)


def context(sentence: str, files: list[tuple[str, bytes]] | None = None) -> Context:
    """A fresh per-request context."""
    tz = user_timezone()
    return Context(sentence=sentence, now=datetime.now(tz), tz=tz, service=google_auth.service, files=files or [])


UPLOADS = ROOT / 'data' / 'uploads'
UPLOAD_ID = re.compile(r'^[0-9a-f]{16}-[\w.\- ]{1,120}$')
MAX_UPLOAD_BYTES = 20_000_000


def uploaded(upload_ids: list[str]) -> list[tuple[str, bytes]]:
    """The files the user attached, by the ids the upload returned."""
    files = []
    for upload_id in upload_ids:
        path = UPLOADS / upload_id
        if not UPLOAD_ID.match(upload_id) or not path.is_file():
            raise UserError('An attached file is no longer there. Attach it again.')
        files.append((upload_id.split('-', 1)[1], path.read_bytes()))
    return files


def group_or_404(group_id: str) -> TaskGroup:
    """The task group with this id."""
    if group_id not in GROUPS:
        raise HTTPException(404, f'No task group {group_id!r}')
    return GROUPS[group_id]


@app.exception_handler(AuthError)
def auth_error(_: Request, error: AuthError) -> JSONResponse:
    """Google login missing or expired: the GUI shows a login button."""
    return JSONResponse({'detail': str(error), 'auth': True}, status_code=401)


@app.exception_handler(UserError)
@app.exception_handler(DateExprError)
def user_error(_: Request, error: ValueError) -> JSONResponse:
    """Something the user can fix by rephrasing."""
    return JSONResponse({'detail': str(error)}, status_code=422)


@app.exception_handler(TodoistError)
def todoist_error(_: Request, error: TodoistError) -> JSONResponse:
    """Todoist refused or could not be reached: the message says what to do."""
    return JSONResponse({'detail': str(error)}, status_code=502)


@app.exception_handler(CaptureError)
def capture_error(_: Request, error: CaptureError) -> JSONResponse:
    """A page could not be read through the browser; `setup` tells the GUI which steps to show."""
    return JSONResponse({'detail': str(error), 'setup': error.setup}, status_code=422)


@app.exception_handler(LLMError)
def llm_error(_: Request, error: LLMError) -> JSONResponse:
    """The model call failed."""
    return JSONResponse({'detail': f'Language model: {error}'}, status_code=502)


@app.exception_handler(HttpError)
def google_error(_: Request, error: HttpError) -> JSONResponse:
    """A Google API call failed."""
    return JSONResponse({'detail': f'Google API: {error.reason or error}'}, status_code=502)


@app.get('/api/groups')
def list_groups() -> list[dict]:
    """Task groups and their standard tasks, for the sidebar and the task cards."""
    return [{'id': g.id, 'name': g.name, 'description': g.description, 'accepts_files': g.accepts_files,
             'tasks': [{'id': t.id, 'name': t.name, 'description': t.description, 'example': t.example} for t in g.tasks]}
            for g in GROUPS.values()]


@app.get('/api/reminders')
def list_reminders() -> list[dict]:
    """Reminders not done yet; the GUI pops up the due ones."""
    return reminders.open_reminders()


class SnoozeRequest(BaseModel):
    """How long to hide a reminder."""

    days: int


@app.post('/api/reminders/{reminder_id}/snooze')
def snooze_reminder(reminder_id: str, request: SnoozeRequest) -> dict:
    """Hide a reminder for a number of days."""
    reminders.snooze(reminder_id, request.days)
    return {'ok': True}


@app.post('/api/reminders/{reminder_id}/done')
def reminder_done(reminder_id: str) -> dict:
    """Mark a reminder done by hand."""
    reminders.done(reminder_id)
    return {'ok': True}


def code_stamp() -> float:
    """When the app's Python code last changed on disk (GUI files are served fresh, so they need no restart)."""
    return max((f.stat().st_mtime for f in Path(__file__).parent.rglob('*.py')), default=0.0)


# CLAUDE> the running server's own code; newer code on disk only takes effect after a restart
STARTED_STAMP = code_stamp()


@app.get('/api/version')
def version() -> dict:
    """Which GUI build the server serves, so an open page can tell it is outdated, and whether the server itself is."""
    index = STATIC / 'index.html'
    return {'build': str(int(index.stat().st_mtime)) if index.exists() else '', 'restart_needed': code_stamp() > STARTED_STAMP}


@app.get('/api/auth')
def auth_status() -> dict:
    """Whether Google is usable."""
    ok, message = google_auth.status()
    return {'ok': ok, 'message': message}


@app.post('/api/auth/login')
def auth_login() -> dict:
    """Open the Google consent page in the local browser and wait for it."""
    google_auth.login()
    user_timezone.cache_clear()
    return {'ok': True, 'message': 'Logged in'}


class InterpretRequest(BaseModel):
    """A sentence typed in a group's command box, optionally for a chosen standard task."""

    text: str
    task_id: str | None = None
    upload_ids: list[str] = []


class InterpretResponse(BaseModel):
    """Either a preview waiting for confirmation, or a question/refusal from the model."""

    status: str
    message: str = ''
    task_id: str | None = None
    task_name: str | None = None
    plan_id: str | None = None
    arguments: dict | None = None
    preview: Preview | None = None
    job: dict | None = None


@app.post('/api/groups/{group_id}/interpret')
def interpret(group_id: str, request: InterpretRequest) -> InterpretResponse:
    """Translate the sentence, resolve it against Google and freeze the plan. Recorded as a 'preview' job."""
    group = group_or_404(group_id)
    text = request.text.strip()
    if not text:
        raise UserError('Type what you want done.')
    if not group.tasks:
        raise UserError(f'{group.name} has no standard tasks yet.')

    job = jobs.Job(group=group.id, sentence=text)
    with jobs.run(job):
        response = _interpret(group, request.task_id, text, job, uploaded(request.upload_ids))
    # CLAUDE> the job's figures are taken after it closed, so its time is the real one
    response.job = job.summary()
    return response


def _interpret(group: TaskGroup, task_id: str | None, text: str, job: jobs.Job,
               files: list[tuple[str, bytes]]) -> InterpretResponse:
    """Pick the task, have the model fill its arguments and resolve them; the caller records it all on `job`."""
    if task_id:
        task = group.task(task_id)
    else:
        choice = route(group, text)
        if choice.task == 'none':
            job.status, job.message = 'unsupported', choice.message or 'No standard task here does that.'
            return InterpretResponse(status=job.status, message=job.message)
        task = group.task(choice.task)
    job.task_id, job.task_name = task.id, task.name

    args = fill_args(group, task, text)
    if task_id and args.status == 'unsupported':
        # CLAUDE> the page keeps a task chosen; a sentence meant for another task goes to the one that fits it
        choice = route(group, text)
        if choice.task not in ('none', task.id):
            task = group.task(choice.task)
            job.task_id, job.task_name = task.id, task.name
            args = fill_args(group, task, text)
    if args.status != 'ok':
        job.status, job.message = args.status, args.message
        return InterpretResponse(status=args.status, message=args.message, task_id=task.id, task_name=task.name)

    preview, payload = task.resolve(args, context(text, files))
    plan_id = plans.put(Plan(group_id=group.id, task_id=task.id, payload=payload, job=job,
                             row_ids={row.id for row in preview.rows if row.selectable}))
    job.message, job.preview = preview.summary, preview.model_dump()
    return InterpretResponse(status='preview', task_id=task.id, task_name=task.name, plan_id=plan_id,
                             arguments=args.model_dump(exclude={'status', 'message'}), preview=preview)


def _watch_state() -> dict:
    """The watched agenda sites as the Calendar page edits them, and the ones that need the browser."""
    return {'lines': watch.as_lines(), 'default_calendar': watch.default_calendar(),
            'needs_browser': [{'id': w.id, 'site': w.site} for w in watch.sites() if w.last_result == watch.NEEDS_BROWSER],
            'progress': {**watch.progress(), 'pause': llm.pause_left()}}


@app.get('/api/calendar/watch')
def watch_list() -> dict:
    """The watched agenda sites."""
    return _watch_state()


class WatchList(BaseModel):
    """The watched agenda sites, one per line ('site → calendar' or just the site), and the default calendar."""

    lines: list[str]
    default_calendar: str = ''


@app.post('/api/calendar/watch')
def watch_save(request: WatchList) -> dict:
    """Make the watched sites exactly these lines."""
    watch.save_list(request.lines)
    if request.default_calendar.strip():
        watch.set_default_calendar(request.default_calendar)
    return _watch_state()


class HabitIn(BaseModel):
    """A habit as the Habits page sends it."""

    name: str | None = None
    schedule: str | None = None
    paused: bool | None = None


class HabitCheck(BaseModel):
    """A tick, or its removal, for a day."""

    day: str
    done: bool


class HabitReminder(BaseModel):
    """The daily reminder time ('21:00'), or '' for off."""

    at: str


def _habits_view(day: str | None = None) -> dict:
    """The Habits page for a day (today by default, in the user's timezone)."""
    today = context('Habits').today
    return habits.overview(datetime.strptime(day, '%Y-%m-%d').date() if day else today) | {'today_date': today.isoformat()}


@app.get('/api/habits')
def habits_list(day: str | None = None) -> dict:
    """Today's check-in (or another day's) and every habit's streaks and month."""
    return _habits_view(day)


@app.post('/api/habits')
def habits_add(habit: HabitIn) -> dict:
    """Add a habit."""
    try:
        habits.add(habit.name or '', habit.schedule or 'daily', created=context('Habits').today)
    except ValueError as error:
        raise UserError(str(error)) from error
    return _habits_view()


@app.patch('/api/habits/{habit_id}')
def habits_change(habit_id: int, habit: HabitIn) -> dict:
    """Rename a habit, change its rhythm, pause or resume it."""
    try:
        habits.update(habit_id, name=habit.name, schedule=habit.schedule, paused=habit.paused)
    except ValueError as error:
        raise UserError(str(error)) from error
    return _habits_view()


@app.delete('/api/habits/{habit_id}')
def habits_remove(habit_id: int) -> dict:
    """Delete a habit and its ticks, at once."""
    habits.delete(habit_id)
    return _habits_view()


@app.post('/api/habits/{habit_id}/check')
def habits_check(habit_id: int, tick: HabitCheck) -> dict:
    """Tick or untick a habit for a day."""
    habits.check(habit_id, datetime.strptime(tick.day, '%Y-%m-%d').date(), tick.done)
    return _habits_view(tick.day)


class HabitOrder(BaseModel):
    """The habits' ids in the order the user wants them."""

    ids: list[int]


@app.post('/api/habits/order')
def habits_order(order: HabitOrder) -> dict:
    """Put the habits in the user's order."""
    habits.reorder(order.ids)
    return _habits_view()


@app.post('/api/habits/reminder')
def habits_reminder(setting: HabitReminder) -> dict:
    """Set or clear the daily reminder."""
    try:
        habits.set_reminder(setting.at)
    except ValueError as error:
        raise UserError('Type the reminder time as 21:00, or leave it empty for no reminder.') from error
    return _habits_view()


@app.get('/api/tasks/stats')
def task_stats(period: int = 30) -> dict:
    """The Tasks page's statistics for the last `period` days (0 = all); today's done and added come fresh from Todoist."""
    ctx = context('Task statistics')
    try:
        task_stats_store.take_snapshot(ctx.todoist(), ctx.now)
        task_stats_store.refresh_activity(ctx.todoist(), ctx.now, days=2)
    except TodoistError as error:
        log.warning('task statistics not refreshed: %s', error)
    return task_stats_store.overview(ctx.today, period)


def _habit_reminder(now: datetime) -> None:
    """Once a day, from the time the user set, a desktop notification while habits due today are still open."""
    if text := habits.reminder_text(now):
        subprocess.run(['notify-send', '--app-name=Automation desk', 'Habits', text], check=False, timeout=10)
        habits.mark_reminded(now.date())


def _task_stats_every_morning() -> None:
    """The morning snapshot: checked every minute, taken once a day from 07:00 on, with 90 days of history read back.

    The PC mostly wakes from hibernation rather than booting: the app keeps running through it, and its sleep counts only
    running time, so the first check after waking comes within a minute. A check before the snapshot is due only reads the
    ledger; Todoist is asked once a day.
    """
    while True:
        try:
            ctx = context('Daily task statistics')
            if task_stats_store.take_snapshot(ctx.todoist(), ctx.now):
                task_stats_store.refresh_activity(ctx.todoist(), ctx.now, days=90)
        except Exception:
            log.exception('daily task statistics failed')
        try:
            _habit_reminder(context('Habit reminder').now)
        except Exception:
            log.exception('habit reminder failed')
        time.sleep(60)


@app.post('/api/stop/{action}')
def stop_action(action: str) -> dict:
    """Ask a running long action to stop; it ends with what it has done so far."""
    if not stop.stoppable(action):
        raise HTTPException(404, f'Nothing called {action!r} can be stopped.')
    stop.request(action)
    return {'stopping': action}


@app.post('/api/calendar/watch/from-events')
def watch_from_events() -> dict:
    """Add the agenda pages of the events in the calendars, each with the calendar its events are in."""
    job = jobs.Job(group='calendar', sentence='Add watched sites from calendar events', task_id='check_watched_sites',
                   task_name='Watched agenda sites')
    with jobs.run(job), httpx.Client(timeout=30.0) as http:
        found, unmatched = check_watched.sites_from_events(context(job.sentence).google('calendar', 'v3'), http)
        added = watch.add_sites(found)
        job.message = f'{added} site(s) added of {len(found)} found; {unmatched} import(s) without a web page'
    return {**_watch_state(), 'added': added, 'unmatched': unmatched}


class WatchCheck(BaseModel):
    """Check every watched site, or `only` these, through the browser when the user asked for it."""

    via_browser: bool = False
    only: list[int] | None = None


@app.post('/api/calendar/watch/check')
def watch_check(request: WatchCheck) -> InterpretResponse:
    """The new events of the watched sites as a preview, like a command's, without a model call to understand a sentence."""
    group = group_or_404('calendar')
    task = group.task('check_watched_sites')
    job = jobs.Job(group=group.id, sentence='Check watched agenda sites' + (' through the browser' if request.via_browser else ''),
                   task_id=task.id, task_name=task.name)
    with jobs.run(job):
        preview, payload = task.check(context(job.sentence), request.via_browser, set(request.only) if request.only else None)
        plan_id = plans.put(Plan(group_id=group.id, task_id=task.id, payload=payload, job=job,
                                 row_ids={row.id for row in preview.rows if row.selectable}))
        job.message, job.preview = preview.summary, preview.model_dump()
    response = InterpretResponse(status='preview', task_id=task.id, task_name=task.name, plan_id=plan_id, arguments={},
                                 preview=preview)
    response.job = job.summary()
    return response


class AdjustRequest(BaseModel):
    """New values for a preview's options."""

    options: dict[str, str]


@app.post('/api/groups/{group_id}/plans/{plan_id}/adjust')
def adjust(group_id: str, plan_id: str, request: AdjustRequest) -> InterpretResponse:
    """Recompose an open preview with the options the user changed; recorded as more preview work of the same job."""
    group = group_or_404(group_id)
    plan = plans.get(plan_id)
    if plan is None or plan.group_id != group.id:
        raise HTTPException(410, 'This preview expired or was already executed. Run the command again.')
    task = group.task(plan.task_id)
    with jobs.run(plan.job):
        preview, payload = task.adjust(plan.payload, request.options, context(plan.job.sentence))
        plan.payload, plan.row_ids = payload, {row.id for row in preview.rows if row.selectable}
        plan.job.message, plan.job.preview = preview.summary, preview.model_dump()
    return InterpretResponse(status='preview', task_id=task.id, task_name=task.name, plan_id=plan_id, preview=preview,
                             job=plan.job.summary())


class ExecuteRequest(BaseModel):
    """Confirmation of a previewed plan with the ticked rows."""

    plan_id: str
    selected: list[str]
    # CLAUDE> one section of the preview only (a watched site); the plan stays for the other sections
    section: str = ''


@app.post('/api/groups/{group_id}/execute')
def execute(group_id: str, request: ExecuteRequest) -> dict:
    """Apply a frozen plan to the ticked rows, as the 'apply' step of the job that previewed it. Runs at most once."""
    group = group_or_404(group_id)
    plan = plans.get(request.plan_id) if request.section else plans.take(request.plan_id)
    if plan is None or plan.group_id != group.id:
        raise HTTPException(410, 'This preview expired or was already executed. Run the command again.')
    job = plan.job
    if request.section:
        with jobs.run(job, 'apply'):
            results, done = group.task(plan.task_id).execute_group(plan.payload, set(request.selected) & plan.row_ids,
                                                                    request.section, context(job.sentence))
            plan.row_ids.difference_update(done)
            job.applied_rows = sorted(set(job.applied_rows) | (set(request.selected) & done))
            job.results = [*job.results, *results]
            job.apply_message = f'{len(job.results)} result(s)'
        return {'results': results, 'job': job.summary()}
    with jobs.run(job, 'apply'):
        selected = set(request.selected) & plan.row_ids
        job.applied_rows = sorted(selected)
        task = group.task(plan.task_id)
        job.results = (task.execute(plan.payload, selected, context(job.sentence)) if selected or task.declines_unticked
                       else ['Nothing selected, nothing changed.'])
        job.apply_message = f'{len(job.results)} result(s)'
    return {'results': job.results, 'job': job.summary()}


@app.get('/api/jobs')
def list_jobs(group: str | None = None) -> dict:
    """Job summaries, newest first, with totals overall, per group and per model."""
    return {'jobs': ledger.summaries(group), **ledger.totals(), 'configured_model': config().llm_model}


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str) -> dict:
    """One job in full: every model call with prompt and reply, every Google call, every page read, its result."""
    found = ledger.detail(job_id)
    if found is None:
        raise HTTPException(404, f'No job {job_id!r}')
    return found


@app.post('/api/tools/pdf-margin')
async def tool_pdf_margin(request: Request, side: str = 'right', percent: float = 33, name: str = 'document') -> Response:
    """The PDF in the request body with white space beside its pages, as a download under `name`."""
    content = await request.body()
    if len(content) > MAX_UPLOAD_BYTES:
        raise UserError(f'The file is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB.')
    try:
        widened = pdf_margin.add_margin(content, side, percent)
    except ValueError as error:
        raise UserError(str(error)) from error
    stem = re.sub(r'[\\/:*?"<>|]', '_', name.strip()).removesuffix('.pdf').removesuffix('.PDF') or 'document'
    return Response(widened, media_type='application/pdf',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(stem + '.pdf')}"})


@app.post('/api/uploads')
async def upload(request: Request, name: str) -> dict:
    """Keep a file the user attached (the raw request body) in data/uploads inside the project."""
    content = await request.body()
    if not content:
        raise UserError('The file is empty.')
    if len(content) > MAX_UPLOAD_BYTES:
        raise UserError(f'The file is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB.')
    safe = re.sub(r'[^\w.\- ]', '_', Path(name).name)[:120] or 'file'
    upload_id = f'{hashlib.sha1(content).hexdigest()[:16]}-{safe}'
    UPLOADS.mkdir(parents=True, exist_ok=True)
    (UPLOADS / upload_id).write_bytes(content)
    return {'id': upload_id, 'name': safe, 'size': len(content)}


@app.get('/api/capture/pending')
def capture_pending() -> list[dict]:
    """Pages the app wants read through the browser; the Automation desk page polls this while a job runs."""
    return broker.claim()


class CaptureResult(BaseModel):
    """A page as the extension read it, or why it could not."""

    url: str = ''
    html: str = ''
    asked_you: bool = False
    error: str = ''
    missing_extension: bool = False
    needs_person: bool = False


@app.post('/api/capture/{request_id}')
def capture_deliver(request_id: str, result: CaptureResult) -> dict:
    """The browser's answer to one page request."""
    captured = None if result.error else Captured(url=result.url, html=result.html, asked_you=result.asked_you)
    return {'accepted': broker.deliver(request_id, captured, result.error, result.missing_extension, result.needs_person)}


@app.get('/api/setup/extension')
def extension_setup() -> dict:
    """Where the browser extension lives, for the setup steps shown in the GUI."""
    return {'folder': str(EXTENSION_DIR)}


@app.get('/api/news/overview')
def news_overview() -> dict:
    """Everything the News panel shows besides a digest's stories."""
    return {'sources': [s.__dict__ for s in news.sources()], 'subjects': news.subjects(), 'suggestions': news.open_suggestions(),
            'blocked': news.blocked(), 'digests': news.digests(), 'cap_usd': news.cap(), 'running': news_digest.running(),
            'failure': news_digest.failure(), 'nothing_new': news.nothing_new(),
            'progress': news_digest.progress()}


@app.get('/api/news/digests/{digest_id}')
def news_digest_detail(digest_id: int) -> dict:
    """One digest with its stories grouped by subject."""
    found = news.digest(digest_id)
    if found is None:
        raise HTTPException(404, f'No digest {digest_id}')
    return {**found, 'cap_to_sort': news_digest.cap_to_sort(digest_id), 'cap_usd': news.cap()}


class ContinueDigest(BaseModel):
    """Whether to raise the daily cap to what the held-back articles need."""

    raise_cap: bool = False


@app.post('/api/news/digests/{digest_id}/continue')
def news_continue(digest_id: int, request: ContinueDigest) -> dict:
    """Continue a digest in the background: the sites the user passed a check on, and the articles held back at the cap."""
    threading.Thread(target=news_digest.continue_digest, args=(digest_id, request.raise_cap), daemon=True).start()
    return {'started': True}


class SuggestionAnswer(BaseModel):
    """What to do with a suggested subject; the name travels in the body, so a name with '/' works."""

    name: str
    answer: Literal['accept', 'block', 'reject']


@app.post('/api/news/suggestions')
def news_suggestion(request: SuggestionAnswer) -> dict:
    """Accept (a new subject), block (a new blocked topic) or reject a suggestion."""
    news.set_suggestion(request.name, {'accept': 'accepted', 'block': 'blocked', 'reject': 'rejected'}[request.answer])
    return {'ok': True}


class SubjectOrder(BaseModel):
    """The subject list, in order."""

    names: list[str]


@app.post('/api/news/subjects')
def news_subjects(order: SubjectOrder) -> dict:
    """Rename, reorder or remove subjects from the panel."""
    news.set_subjects(order.names)
    return {'subjects': news.subjects()}


@app.post('/api/news/blocked')
def news_blocked(order: SubjectOrder) -> dict:
    """Change the blocked topics from the panel."""
    news.set_blocked(order.names)
    return {'blocked': news.blocked()}


@app.delete('/api/news/digests/{digest_id}')
def news_delete_digest(digest_id: int) -> dict:
    """Remove a whole digest; its articles never come back."""
    if not news.delete_digest(digest_id):
        raise HTTPException(404, f'No digest {digest_id}')
    return {'ok': True}


class SubjectChoice(BaseModel):
    """One subject of a digest; in the body, so any name works."""

    subject: str


@app.post('/api/news/digests/{digest_id}/delete-subject')
def news_delete_subject(digest_id: int, choice: SubjectChoice) -> dict:
    """Remove every story of one subject from a digest."""
    return {'deleted': news.delete_subject(digest_id, choice.subject)}


@app.delete('/api/news/stories/{story_id}')
def news_delete_story(story_id: str) -> dict:
    """Take a story out of its digest; its articles never come back."""
    if not news.delete_story(story_id):
        raise HTTPException(404, f'No story {story_id}')
    return {'ok': True}


class SiteList(BaseModel):
    """The followed sites, one address per entry."""

    sites: list[str]


@app.post('/api/news/sources')
def news_sources(site_list: SiteList) -> dict:
    """Make the followed sites exactly this list; new ones get their feed looked up."""
    return {'problems': save_site_list(site_list.sites), 'sources': [s.__dict__ for s in news.sources()]}


@app.delete('/api/news/sources/{source_id}')
def news_remove_source(source_id: int) -> dict:
    """Stop following a site."""
    news.remove_source(source_id)
    return {'ok': True}


class CapSetting(BaseModel):
    """The daily cost cap."""

    usd: float


@app.post('/api/news/cap')
def news_cap(setting: CapSetting) -> dict:
    """Change the daily cost cap (0 to 10 dollars)."""
    if not 0 <= setting.usd <= 10:
        raise UserError('The daily cap must be between $0 and $10.')
    news.set_cap(setting.usd)
    return {'cap_usd': news.cap()}


@app.post('/api/news/make')
def news_make() -> dict:
    """Make a digest now, in the background; the panel polls the overview until it is done."""
    threading.Thread(target=news_digest.make_digest, args=('button',), daemon=True).start()
    return {'started': True}


def _in_background(work: Callable[..., object], *args: object) -> None:
    """Run long work in a thread; the page polls for its progress."""
    threading.Thread(target=work, args=args, daemon=True).start()


class ResearchIn(BaseModel):
    """A new research."""

    request: str
    budget: float | None = None
    countries: list[str]


class ResearchAnswers(BaseModel):
    """Answers to the question form or the follow-up questions."""

    answers: dict[str, str]
    kind: str = 'product'


class ResearchBudget(BaseModel):
    """The budget the user picked."""

    amount: float


class ResearchLimits(BaseModel):
    """The limits of one research: whole searches and pages, a cost in dollars; each above zero."""

    searches: int = Field(gt=0)
    pages: int = Field(gt=0)
    cost: float = Field(gt=0)


class ResearchSettings(BaseModel):
    """The Research page settings; only the given ones change."""

    countries: list[str] | None = None
    municipality: str | None = None
    limits: ResearchLimits | None = None
    vault: str | None = None
    subdir: str | None = None


class ResearchTitle(BaseModel):
    """A research's new title."""

    title: str


class ResearchRequirement(BaseModel):
    """One requirement as the user set it."""

    text: str
    weight: Literal['must', 'important', 'nice']


class ResearchRequirements(BaseModel):
    """The requirements the user checked."""

    requirements: list[ResearchRequirement]


def _save_note(research_id: int) -> None:
    """Save a finished research to the Obsidian vault set on the Research page; without a vault nothing happens. The note's
    place, or why it could not be saved, goes with the result."""
    row = research_store.get(research_id)
    settings = research_store.settings()
    if not row or row['state'] not in ('done', 'stopped') or not settings['vault']:
        return
    try:
        note = research_export.save(row, settings['vault'], settings['subdir']) | {'error': ''}
    except (research_export.ExportError, OSError) as error:
        note = {'path': '', 'obsidian_url': '', 'error': str(error)}
    research_store.update(research_id, result=(row['result'] or {}) | {'note': note})


def _research_run_and_save(research_id: int) -> None:
    """Run a research on, and save it to Obsidian once it is finished: the user's notes were never saved by the button alone."""
    research_run.advance(research_id)
    _save_note(research_id)


def _research(research_id: int) -> dict:
    """A research with its pages (without their facts) and the progress, or 404."""
    row = research_store.get(research_id)
    if row is None:
        raise HTTPException(404, f'There is no research {research_id}. It was deleted.')
    found = [{k: v for k, v in p.items() if k != 'facts'} for p in research_store.pages(research_id)]
    return row | {'pages': found, 'progress': research_run.progress(), 'stopped_text': research_run.stop_text(row)}


@app.get('/api/research')
def research_list() -> dict:
    """The history, the settings, the cost estimate and what runs."""
    settings = research_store.settings()
    return {'researches': research_store.listing(), 'settings': settings, 'vaults': research_export.vaults(),
            'estimate': research_run.estimate(settings['limits']), 'progress': research_run.progress()}


@app.post('/api/research')
def research_start(new: ResearchIn) -> dict:
    """A new research: the question form is made at once."""
    if not new.request.strip():
        raise HTTPException(400, 'Write what you need first, for example "A pump that empties the lift pit".')
    if not new.countries:
        raise HTTPException(400, 'Select at least one country.')
    return _research(research_run.start(new.request, new.budget, new.countries))


@app.get('/api/research/{research_id}')
def research_open(research_id: int) -> dict:
    """One research."""
    return _research(research_id)


@app.post('/api/research/{research_id}/answers')
def research_answers(research_id: int, given: ResearchAnswers) -> dict:
    """The form is answered: the research runs."""
    _research(research_id)
    if not research_store.transition_state(research_id, 'questions', 'running'):
        raise HTTPException(409, 'This research is not waiting for that. Reload the page.')
    research_run.answer(research_id, given.answers, given.kind)
    _in_background(_research_run_and_save, research_id)
    return _research(research_id)


@app.post('/api/research/{research_id}/requirements')
def research_requirements(research_id: int, given: ResearchRequirements) -> dict:
    """The user checked the requirements: the research runs on with them."""
    _research(research_id)
    if not any(r.text.strip() for r in given.requirements):
        raise HTTPException(400, 'Keep at least one requirement: the products are scored against them.')
    if not research_store.transition_state(research_id, 'requirements', 'running'):
        raise HTTPException(409, 'This research is not waiting for that. Reload the page.')
    research_run.confirm_requirements(research_id, [r.model_dump() for r in given.requirements])
    _in_background(_research_run_and_save, research_id)
    return _research(research_id)


@app.post('/api/research/{research_id}/requirements/cancel')
def research_requirements_cancel(research_id: int) -> dict:
    """Keep the earlier result of a research the user wanted to score again, unchanged."""
    _research(research_id)
    try:
        research_run.cancel_requirements(research_id)
    except research_run.WrongState as error:
        raise HTTPException(409, str(error)) from error
    return _research(research_id)


@app.post('/api/research/{research_id}/rescore')
def research_rescore(research_id: int, given: ResearchRequirements | None = None) -> dict:
    """Score a finished research again from the pages read: with the requirements as edited in the result's table, at
    once; without, its requirements come back for changes first. Nothing is searched again."""
    _research(research_id)
    if given is not None and not any(r.text.strip() for r in given.requirements):
        raise HTTPException(400, 'Keep at least one requirement: the products are scored against them.')
    if not research_store.pages(research_id, 'read'):
        raise HTTPException(409, 'This research read no shop page, so there is nothing to score again. Start a new research.')
    if not any(research_store.transition_state(research_id, state, 'running') for state in ('done', 'stopped')):
        raise HTTPException(409, 'Only a finished research can be scored again. Reload the page.')
    research_run.rescore(research_id, [r.model_dump() for r in given.requirements] if given else None)
    _in_background(_research_run_and_save, research_id)
    return _research(research_id)


@app.patch('/api/research/{research_id}')
def research_rename(research_id: int, given: ResearchTitle) -> dict:
    """Give a research another title; its saved note keeps its file name."""
    _research(research_id)
    title = ' '.join(given.title.split())
    if not title:
        raise HTTPException(400, 'Write a title, for example "Lift pit sump pump".')
    research_store.update(research_id, title=title)
    return _research(research_id)


@app.post('/api/research/{research_id}/export')
def research_export_note(research_id: int) -> dict:
    """Save the research as a note in the Obsidian vault set on the Research page."""
    row = _research(research_id)
    settings = research_store.settings()
    try:
        note = research_export.save(row, settings['vault'], settings['subdir']) | {'error': ''}
    except research_export.ExportError as error:
        raise HTTPException(400, str(error)) from error
    research_store.update(research_id, result=(row['result'] or {}) | {'note': note})
    return note


@app.post('/api/research/{research_id}/budget')
def research_budget(research_id: int, given: ResearchBudget) -> dict:
    """A budget is picked: the research runs."""
    _research(research_id)
    if not research_store.transition_state(research_id, 'budget', 'running'):
        raise HTTPException(409, 'This research is not waiting for that. Reload the page.')
    research_run.set_budget(research_id, given.amount)
    _in_background(_research_run_and_save, research_id)
    return _research(research_id)


@app.post('/api/research/{research_id}/reply')
def research_reply(research_id: int, given: ResearchAnswers) -> dict:
    """The follow-up questions are answered: the advice is written."""
    _research(research_id)
    if not research_store.transition_state(research_id, 'waiting', 'running'):
        raise HTTPException(409, 'This research is not waiting for that. Reload the page.')
    research_run.reply(research_id, given.answers)
    _in_background(_research_run_and_save, research_id)
    return _research(research_id)


@app.post('/api/research/{research_id}/continue')
def research_continue(research_id: int) -> dict:
    """A failed research goes on from its step."""
    _research(research_id)
    if not research_store.transition_state(research_id, 'failed', 'running'):
        raise HTTPException(409, 'This research is not waiting for that. Reload the page.')
    research_run.resume(research_id)
    _in_background(_research_run_and_save, research_id)
    return _research(research_id)


@app.delete('/api/research/{research_id}')
def research_delete(research_id: int) -> dict:
    """Remove a research at once."""
    row = research_store.get(research_id)
    if row and row['state'] == 'running':
        raise HTTPException(409, 'This research runs now. Press Stop, then delete it.')
    if research_run.progress().get('id') == research_id:
        raise HTTPException(409, 'This research runs now. Press Stop, then delete it.')
    return {'deleted': research_store.delete(research_id)}


@app.post('/api/research/settings')
def research_settings(given: ResearchSettings) -> dict:
    """Change the default countries, the municipality or the limits."""
    return research_store.save_settings(given.countries, given.municipality,
                                        given.limits.model_dump() if given.limits else None, given.vault, given.subdir)


if STATIC.exists():
    app.mount('/assets', StaticFiles(directory=STATIC / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """The single-page GUI for every non-API path."""
        file = (STATIC / path).resolve()
        inside = file.is_relative_to(STATIC.resolve())
        return FileResponse(file if path and inside and file.is_file() else STATIC / 'index.html')


def main() -> None:
    """Serve the app on localhost only."""
    logging.basicConfig(level=logging.INFO)
    research_store.fail_running()
    threading.Thread(target=_task_stats_every_morning, daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=config().port)
