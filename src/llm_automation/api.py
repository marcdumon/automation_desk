"""HTTP API and static GUI. Every route is generic over task groups and standard tasks.

interpret: sentence -> model fills the task's arguments -> code resolves against Google -> frozen plan + preview.
execute:   plan id + ticked rows -> exactly the previewed changes are applied.
"""

import logging
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

from llm_automation import google_auth, jobs, reminders
from llm_automation.capture import EXTENSION_DIR, Captured, CaptureError, broker
from llm_automation.config import config
from llm_automation.dates import DateExprError
from llm_automation.google_auth import AuthError
from llm_automation.groups import GROUPS
from llm_automation.groups.base import Context, Preview, TaskGroup, UserError
from llm_automation.groups.calendar.client import timezone
from llm_automation.interpret import fill_args, route
from llm_automation.llm import LLMError
from llm_automation.plans import Plan, PlanStore

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / 'static'

app = FastAPI(title='llm_automation')
plans = PlanStore()


@cache
def user_timezone() -> ZoneInfo:
    """The Calendar timezone, read once; the configured fallback when Google cannot be asked."""
    try:
        return ZoneInfo(timezone(google_auth.service('calendar', 'v3')))
    except (AuthError, HttpError) as error:
        log.warning('using fallback timezone: %s', error)
        return ZoneInfo(config().fallback_timezone)


def context(sentence: str) -> Context:
    """A fresh per-request context."""
    tz = user_timezone()
    return Context(sentence=sentence, now=datetime.now(tz), tz=tz, service=google_auth.service)


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
    return [{'id': g.id, 'name': g.name, 'description': g.description,
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
        response = _interpret(group, request.task_id, text, job)
    # CLAUDE> the job's figures are taken after it closed, so its time is the real one
    response.job = job.summary()
    return response


def _interpret(group: TaskGroup, task_id: str | None, text: str, job: jobs.Job) -> InterpretResponse:
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

    preview, payload = task.resolve(args, context(text))
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
    summaries = [jobs.summarise(raw) for raw in jobs.load()]
    if group:
        summaries = [s for s in summaries if s['group'] == group]
    by_group: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for raw in jobs.load():
        for call in raw.get('llm_calls', []):
            model = call['model_used'] or call['model_requested']
            for key, bucket in ((raw['group'], by_group), (model, by_model)):
                entry = bucket.setdefault(key, {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'cost_usd': 0.0})
                entry['calls'] += 1
                entry['prompt_tokens'] += call['prompt_tokens']
                entry['completion_tokens'] += call['completion_tokens']
                entry['cost_usd'] += call['cost_usd']
    return {'jobs': summaries, 'total_cost_usd': sum(g['cost_usd'] for g in by_group.values()),
            'by_group': by_group, 'by_model': by_model, 'configured_model': config().llm_model}


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str) -> dict:
    """One job in full: every model call with prompt and reply, every Google call, every page fetched."""
    for raw in jobs.load():
        if raw['id'] == job_id:
            return {**raw, 'summary': jobs.summarise(raw)}
    raise HTTPException(404, f'No job {job_id!r}')


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
    uvicorn.run(app, host='127.0.0.1', port=config().port)
