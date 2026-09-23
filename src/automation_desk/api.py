"""HTTP API and static GUI. Every route is generic over task groups and standard tasks.

interpret: sentence -> model fills the task's arguments -> code resolves against Google -> frozen plan + preview.
execute:   plan id + ticked rows -> exactly the previewed changes are applied.
"""

import hashlib
import logging
import re
import threading
from datetime import datetime
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from automation_desk import google_auth, jobs, ledger, reminders
from automation_desk.capture import EXTENSION_DIR, Captured, CaptureError, broker
from automation_desk.config import ROOT, config
from automation_desk.dates import DateExprError
from automation_desk.google_auth import AuthError
from automation_desk.groups import GROUPS
from automation_desk.groups.base import Context, Preview, TaskGroup, UserError
from automation_desk.groups.calendar.client import timezone
from automation_desk.groups.news import digest as news_digest
from automation_desk.groups.news import store as news
from automation_desk.interpret import fill_args, route
from automation_desk.llm import LLMError
from automation_desk.plans import Plan, PlanStore

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


@app.get('/api/version')
def version() -> dict:
    """Which GUI build the server serves, so an open page can tell it is outdated."""
    index = STATIC / 'index.html'
    return {'build': str(int(index.stat().st_mtime)) if index.exists() else ''}


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
    if args.status != 'ok':
        job.status, job.message = args.status, args.message
        return InterpretResponse(status=args.status, message=args.message, task_id=task.id, task_name=task.name)

    preview, payload = task.resolve(args, context(text, files))
    plan_id = plans.put(Plan(group_id=group.id, task_id=task.id, payload=payload, job=job,
                             row_ids={row.id for row in preview.rows if row.selectable}))
    job.message, job.preview = preview.summary, preview.model_dump()
    return InterpretResponse(status='preview', task_id=task.id, task_name=task.name, plan_id=plan_id,
                             arguments=args.model_dump(exclude={'status', 'message'}), preview=preview)


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


@app.post('/api/groups/{group_id}/execute')
def execute(group_id: str, request: ExecuteRequest) -> dict:
    """Apply a frozen plan to the ticked rows, as the 'apply' step of the job that previewed it. Runs at most once."""
    group = group_or_404(group_id)
    plan = plans.take(request.plan_id)
    if plan is None or plan.group_id != group.id:
        raise HTTPException(410, 'This preview expired or was already executed. Run the command again.')
    job = plan.job
    with jobs.run(job, 'apply'):
        selected = set(request.selected) & plan.row_ids
        job.applied_rows = sorted(selected)
        job.results = (group.task(plan.task_id).execute(plan.payload, selected, context(job.sentence)) if selected
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


@app.post('/api/capture/{request_id}')
def capture_deliver(request_id: str, result: CaptureResult) -> dict:
    """The browser's answer to one page request."""
    captured = None if result.error else Captured(url=result.url, html=result.html, asked_you=result.asked_you)
    return {'accepted': broker.deliver(request_id, captured, result.error, result.missing_extension)}


@app.get('/api/setup/extension')
def extension_setup() -> dict:
    """Where the browser extension lives, for the setup steps shown in the GUI."""
    return {'folder': str(EXTENSION_DIR)}


@app.get('/api/news/overview')
def news_overview() -> dict:
    """Everything the News panel shows besides a digest's stories."""
    return {'sources': [s.__dict__ for s in news.sources()], 'subjects': news.subjects(), 'suggestions': news.open_suggestions(),
            'blocked': news.blocked(), 'digests': news.digests(), 'cap_usd': news.cap(), 'running': news_digest.running(),
            'failure': news_digest.failure()}


@app.get('/api/news/digests/{digest_id}')
def news_digest_detail(digest_id: int) -> dict:
    """One digest with its stories grouped by subject."""
    found = news.digest(digest_id)
    if found is None:
        raise HTTPException(404, f'No digest {digest_id}')
    return found


class SuggestionAnswer(BaseModel):
    """Accept or reject a suggested subject."""

    accept: bool


@app.post('/api/news/suggestions/{name}')
def news_suggestion(name: str, answer: SuggestionAnswer) -> dict:
    """Accept (the subject joins the list) or reject a suggestion."""
    news.set_suggestion(name, 'accepted' if answer.accept else 'rejected')
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


@app.delete('/api/news/stories/{story_id}')
def news_delete_story(story_id: str) -> dict:
    """Take a story out of its digest; its articles never come back."""
    if not news.delete_story(story_id):
        raise HTTPException(404, f'No story {story_id}')
    return {'ok': True}


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
    threading.Thread(target=news_digest.make_digest, args=('button', True), daemon=True).start()
    return {'started': True}


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
    from automation_desk.groups.news import schedule

    schedule.start()
    uvicorn.run(app, host='127.0.0.1', port=config().port)
