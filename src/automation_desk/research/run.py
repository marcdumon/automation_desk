"""The research engine: runs the steps of one research in order, one research at a time, with progress, Stop, limits,
pauses for the user (the requirements, the budget, follow-up questions), and Continue from the step that failed."""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx

from automation_desk import jobs, stop
from automation_desk.config import config
from automation_desk.groups.calendar import web_page
from automation_desk.research import advice, compare, offers, pages, plan, questions, requirements, score, search, store

GROUP = 'research'
STEPS = ('requirements', 'classes', 'plan', 'search', 'read', 'compare', 'followups', 'score', 'advise')
STEP_NAMES = {'requirements': 'the requirements', 'classes': 'the price classes', 'plan': 'the search plan', 'search': 'the searches',
              'read': 'reading the shop pages', 'compare': 'the comparison', 'followups': 'the follow-up questions',
              'score': 'the scores', 'advise': 'the advice'}
WORKERS = 4
# CLAUDE> what the scores and the advice still need when a limit stops the reading
ADVICE_RESERVE_USD = 0.03
CLASS_SEARCHES = 2
STOPPED_BY_YOU = 'you stopped it'
SKIP_READING = (STOPPED_BY_YOU, 'the cost limit')

_lock = threading.Lock()
_progress: dict = {}
_progress_lock = threading.Lock()
# CLAUDE> per running job: the part of its cost already added to research.cost_usd, so no cost is counted twice
_booked: dict[str, float] = {}
_book_lock = threading.Lock()


class WrongState(ValueError):
    """The research is not at the point this action needs; the message says what to do."""


class Halt(Exception):
    """Searching and reading end early; the reason goes into the result."""

    def __init__(self, reason: str) -> None:
        """Keep the reason."""
        super().__init__(reason)
        self.reason = reason


def stop_key(research_id: int) -> str:
    """The name the Research page stops this research by."""
    return f'research-{research_id}'


def progress() -> dict:
    """What the running research does, for the page."""
    with _progress_lock:
        return dict(_progress)


def _show(research_id: int, step: str, done: int = 0, total: int = 0) -> None:
    """Tell the page the step and how far it is."""
    with _progress_lock:
        _progress.update(id=research_id, step=step, done=done, total=total, cost_usd=round(_spent(research_id), 4))


def _spent(research_id: int) -> float:
    """The cost of the research so far: the stored cost plus the part of the running job not stored yet."""
    with _book_lock:
        job = jobs.current()
        running = sum(c.cost_usd for c in job.llm_calls) - _booked.get(job.id, 0.0) if job else 0.0
        return (store.get(research_id) or {}).get('cost_usd', 0.0) + running


def _book(research_id: int, job: jobs.Job | None = None, save: bool = True) -> None:
    """Add the running job's new cost to the research and save the job (after each step), so a restart loses no cost."""
    job = job or jobs.current()
    if job is None:
        return
    with _book_lock:
        total = sum(c.cost_usd for c in job.llm_calls)
        new = total - _booked.get(job.id, 0.0)
        _booked[job.id] = total
        if new:
            store.update(research_id, cost_usd=round((store.get(research_id) or {}).get('cost_usd', 0.0) + new, 6))
        if save:
            jobs.save(job)


def _told(asked: list[dict] | None, answers: dict | None) -> dict:
    """The answers keyed by the text of their question, as the model must read them (the page sends question ids)."""
    texts = {q['id']: q['text'] for q in asked or []}
    return {texts.get(key, key): value for key, value in (answers or {}).items()}


def estimate(limits: dict) -> float:
    """The most a research can cost under its limits: searches with their result tokens, pages, and the model steps."""
    cfg = config()
    per_token = cfg.price_in_per_m / 1_000_000
    search_cost = limits['searches'] * (0.007 + 6000 * per_token)
    page_cost = limits['pages'] * (9000 * per_token + 800 * cfg.price_out_per_m / 1_000_000)
    return round(min(search_cost + page_cost + 0.04, limits['cost']), 2)


def start(request: str, budget: float | None, countries: list[str]) -> int:
    """A new research with its question form."""
    research_id = store.create(request, budget, countries)
    job = jobs.Job(group=GROUP, sentence=f'Research questions: {request[:80]}', task_id='research', task_name='Research: questions')
    try:
        with jobs.run(job):
            form = questions.make(request, budget, countries)
    except Exception:
        # CLAUDE> no research without its questions stays in the history; the error goes on to the page
        store.delete(research_id)
        raise
    store.update(research_id, kind=form.kind, questions=form.model_dump()['questions'], title=form.title,
                 note='\n'.join(form.unknowns), cost_usd=job.summary()['cost_usd'])
    return research_id


def answer(research_id: int, answers: dict, kind: str) -> None:
    """The user answered the form: the requirements are made next, for the user to check."""
    stop.begin(stop_key(research_id))
    store.update(research_id, answers=answers, kind=kind, state='running', step='requirements', note='')


def _after_requirements(research_id: int, row: dict) -> str:
    """Where a research goes once its requirements are set: straight to the comparison when its pages are read already
    (score again), else to the price classes (no budget) or the search plan."""
    if store.pages(research_id, 'read'):
        return 'compare'
    return 'plan' if row['budget'] is not None else 'classes'


def cancel_requirements(research_id: int) -> None:
    """Leave the requirements of a research scored before unchanged: it shows its earlier result again."""
    row = store.get(research_id)
    if row is None or row['state'] != 'requirements' or not (row['result'] or {}).get('summary'):
        raise WrongState('Only a research that was scored before can go back to its result. Reload the page.')
    stopped = row['result'].get('stopped_by') == STOPPED_BY_YOU
    store.update(research_id, state='stopped' if stopped else 'done', step='advise')


def confirm_requirements(research_id: int, given: list[dict]) -> None:
    """The user checked the requirements: they are kept as changed, and the research runs on."""
    row = store.get(research_id)
    stop.begin(stop_key(research_id))
    store.update(research_id, requirements=requirements.clean(given), state='running',
                 step=_after_requirements(research_id, row), note='')


def rescore(research_id: int, given: list[dict] | None = None) -> None:
    """Score a finished research again from the pages already read, so a research without a page read cannot be. With
    the requirements as the user edited them in the result's table, it goes straight to the scores; without, its
    requirements come back to the page for changes first."""
    if not store.pages(research_id, 'read'):
        raise WrongState('This research read no shop page, so there is nothing to score again. Start a new research.')
    stop.begin(stop_key(research_id))
    if given is None:
        store.update(research_id, state='running', step='requirements', note='')
    else:
        store.update(research_id, requirements=requirements.clean(given), state='running', step='compare', note='')


def stop_text(row: dict) -> str:
    """Why a research ended early, with the limit's number; '' when it ran to the end. Facts only."""
    reason, limits = (row.get('result') or {}).get('stopped_by', ''), row.get('limits') or {}
    uses = 'The recommendation uses the products found until then.'
    texts = {
        'the page limit': f'The research stopped after reading {limits.get("pages")} shop pages, your page limit. {uses}',
        'the search limit': f'The research stopped after {limits.get("searches")} searches, your search limit. {uses}',
        'the cost limit': f'The research stopped at your cost limit of ${limits.get("cost", 0):.2f}. {uses}',
        STOPPED_BY_YOU: f'You stopped the research. {uses}',
    }
    return texts.get(reason, '')


def set_budget(research_id: int, amount: float) -> None:
    """The user picked a price class or typed an amount."""
    stop.begin(stop_key(research_id))
    store.update(research_id, budget=amount, state='running', step='plan')


def reply(research_id: int, answers: dict) -> None:
    """The user answered the follow-up questions."""
    stop.begin(stop_key(research_id))
    store.update(research_id, followup_answers=answers, state='running', step='score')


def resume(research_id: int) -> None:
    """Continue a failed research from its step."""
    stop.begin(stop_key(research_id))
    store.update(research_id, state='running', note='')


def advance(research_id: int) -> None:
    """Run the research from its step until it is done, waits for the user, stops or fails."""
    with _lock:
        row = store.get(research_id)
        if row is None or row['state'] != 'running':
            return
        # CLAUDE> an earlier Stop is cleared when the research is sent to run (answer, budget, reply, Continue), not here:
        # a Stop pressed while it waited for its turn still counts
        token = web_page.BROWSER_MAY_ASK.set(False)
        # CLAUDE> the jobs list shows the sentence: name the research and the steps this run covers, not the whole request
        name = row['title'] or row['request'][:80]
        job = jobs.Job(group=GROUP, sentence=f'Research "{name}": from {STEP_NAMES[row["step"]]} on', task_id='research',
                       task_name=f'Research: {row["step"]}')
        try:
            with jobs.run(job), httpx.Client(timeout=30.0) as http:
                _steps(research_id, http)
        except Exception as error:
            store.update(research_id, state='failed', note=f'{error}. Press Continue to try this step again.')
        finally:
            web_page.BROWSER_MAY_ASK.reset(token)
            _book(research_id, job)
            with _book_lock:
                _booked.pop(job.id, None)
            stop.begin(stop_key(research_id))
            with _progress_lock:
                _progress.clear()


def _check(research_id: int, row: dict, searches: int | None = None, pages_read: int | None = None) -> None:
    """Halt at Stop or at a limit."""
    if stop.requested(stop_key(research_id)):
        raise Halt(STOPPED_BY_YOU)
    limits = row['limits']
    if searches is not None and searches >= limits['searches']:
        raise Halt('the search limit')
    if pages_read is not None and pages_read >= limits['pages']:
        raise Halt('the page limit')
    if _spent(research_id) >= limits['cost'] - ADVICE_RESERVE_USD:
        raise Halt('the cost limit')


def _steps(research_id: int, http: httpx.Client) -> None:
    """Each step from the stored one; the step is saved before it starts, so a failure keeps it."""
    row = store.get(research_id)
    stopped_by = ''
    key = stop_key(research_id)
    for step in STEPS[STEPS.index(row['step']):]:
        store.update(research_id, step=step)
        row = store.get(research_id)
        _show(research_id, step)
        if step in ('requirements', 'classes', 'plan') and stop.requested(key):
            # CLAUDE> a Stop pressed while the research waited in the queue: no paid class or plan call, straight to the end
            stopped_by = stopped_by or STOPPED_BY_YOU
            continue
        if step == 'requirements':
            if not row['requirements']:
                store.update(research_id, requirements=requirements.make(
                    row['request'], row['kind'], _told(row['questions'], row['answers']), http=http))
            store.update(research_id, state='requirements', step=_after_requirements(research_id, row))
            return
        if step == 'classes':
            _classes(research_id, row, http)
            return
        if step == 'plan':
            store.update(research_id, plan=plan.make(row['request'], row['kind'], _told(row['questions'], row['answers']),
                                                     row['budget'], row['countries'], store.settings()['municipality'],
                                                     row['limits']['searches'], http=http,
                                                     requirements=row['requirements'] or []))
        elif (step == 'search' and not stopped_by) or (step == 'read' and stopped_by not in SKIP_READING):
            # CLAUDE> after the search limit the pages already found are still read; after Stop or the cost limit not
            try:
                (_search if step == 'search' else _read)(research_id, row, http)
            except Halt as halt:
                stopped_by = stopped_by or halt.reason
        elif step == 'compare':
            stopped_by = stopped_by or (row['result'] or {}).get('stopped_by', '')
            comparison = compare.table(store.pages(research_id, 'read'), row['budget'], row['countries'])
            store.update(research_id, result={'comparison': comparison, 'stopped_by': stopped_by})
        elif step == 'score':
            # CLAUDE> scored also after Stop or a limit: the user gets a recommendation from what was found
            comparison = row['result']['comparison']
            rows = score.candidates(comparison, row['kind'])
            reqs = row['requirements'] or []
            told = _told(row['questions'], row['answers']) | _told(row['followups'], row['followup_answers'])
            rated = score.rate(row['request'], row['kind'], reqs, rows, http=http, answers=told) if rows and reqs else \
                {p['n']: {'checks': {r['id']: 'unknown' for r in reqs}, 'pros': [], 'cons': []} for p in rows}
            ranking = score.rank(reqs, rows, rated, comparison, row['budget'], row['kind'])
            store.update(research_id, result=row['result'] | {'ranking': ranking})
        elif step == 'followups':
            if row['followups'] is None and not stopped_by and not stop.requested(key):
                asked = advice.followups(row['request'], _told(row['questions'], row['answers']), row['result']['comparison'],
                                         http=http)
                store.update(research_id, followups=[q.model_dump() for q in asked])
                if asked and not stop.requested(key):
                    store.update(research_id, state='waiting', step='score')
                    return
            if stop.requested(key):
                stopped_by = stopped_by or STOPPED_BY_YOU
        elif step == 'advise':
            stopped_by = stopped_by or (row['result'] or {}).get('stopped_by', '')
            if stop.requested(key):
                stopped_by = stopped_by or STOPPED_BY_YOU
            _advise(research_id, row, http, stopped_by)
        _book(research_id)


def _classes(research_id: int, row: dict, http: httpx.Client) -> None:
    """Two price searches and the price classes; then the user picks a budget."""
    hits = [h for country in row['countries'][:CLASS_SEARCHES] for h in search.run(f'{row["request"]} prijs {country}', http=http)]
    # CLAUDE> these two searches do not count toward the search limit: that limit is for the research itself
    store.update(research_id,
                 classes=[c.model_dump() for c in questions.price_classes(row['request'], _told(row['questions'], row['answers']),
                                                                          hits, http=http)],
                 state='budget', step='plan')


def _in_parallel(work: list, do: object) -> None:
    """Run `do` on each item, WORKERS at a time, each on a copy of this context so its calls land on the job."""
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, do, item) for item in work]
        for future in futures:
            future.result()


def _search(research_id: int, row: dict, http: httpx.Client) -> None:
    """Run the planned searches not yet run; keep each page once."""
    first = row['searches_done']
    queries = row['plan'][first:]
    started, finished = {'n': first}, set()
    count_lock = threading.Lock()

    def one(item: tuple[int, dict]) -> None:
        """One search, its pages kept; `searches_done` is the longest run of finished searches, so Continue skips none."""
        index, query = item
        with count_lock:
            _check(research_id, row, searches=started['n'])
            started['n'] += 1
        for hit in search.run(query['text'], http=http):
            # CLAUDE> the page's country is that of its own domain: a .com shop found by a Belgian search is not Belgian
            # CLAUDE> only pages from the chosen countries, or without a country (.com): a French-language search for
            # Belgium also brings French shops, and one became the recommendation of a BE/NL research
            if (country := search.country_of(hit.url)) and country not in row['countries']:
                continue
            store.add_page(research_id, hit.url, query['text'], hit.title, hit.snippet, country)
        with count_lock:
            finished.add(index)
            prefix = first
            while prefix in finished:
                prefix += 1
            store.update(research_id, searches_done=prefix)
        _book(research_id, save=False)
        _show(research_id, 'search', len(finished) + first, len(row['plan']))

    _in_parallel(list(enumerate(queries, first)), one)


def _read(research_id: int, row: dict, http: httpx.Client) -> None:
    """Read each found page and take its facts."""
    todo = store.pages(research_id, 'found')
    done = {'n': len(store.pages(research_id)) - len(todo)}
    # CLAUDE> the progress counts up to what will be read: the page limit, not every page the searches found (273 in one run)
    total = min(len(store.pages(research_id)), row['limits']['pages'])
    count_lock = threading.Lock()

    def one(page: dict) -> None:
        """One page read and its facts kept; a page that fails is noted with its error, not fatal."""
        with count_lock:
            _check(research_id, row, pages_read=done['n'])
            done['n'] += 1
        try:
            read = pages.read(page['url'], http)
            facts = offers.extract(read, row['request'], row['kind'], page['country'], http=http)
        except pages.Unreadable as error:
            store.update_page(research_id, page['url'], status='failed', error=str(error))
        except Exception as error:
            # CLAUDE> a bad link, a model or a parse error on one page: the page keeps its error and stays 'found', so
            # Continue reads it again; the other pages go on (Halt comes only from _check, outside this try)
            store.update_page(research_id, page['url'], error=str(error) or type(error).__name__)
        else:
            store.update_page(research_id, page['url'], status='read', via=read.via, facts=facts, error=None)
        _book(research_id, save=False)
        _show(research_id, 'read', min(done['n'], total), total)

    _in_parallel(todo, one)
    if todo and not store.pages(research_id, 'read'):
        errors = [p['error'] for p in store.pages(research_id, 'found') if p['error']]
        if errors:
            raise RuntimeError(f'No page could be read: {errors[0]}')


def _advise(research_id: int, row: dict, http: httpx.Client, stopped_by: str) -> None:
    """Write the advice; the research ends done, or stopped when the user pressed Stop."""
    if not (row['result'] or {}).get('comparison'):
        row['result'] = {'comparison': compare.table(store.pages(research_id, 'read'), row['budget'], row['countries'])}
    answers = _told(row['questions'], row['answers']) | _told(row['followups'], row['followup_answers'])
    written = advice.write(row['request'], answers, row['requirements'] or [], (row['result'] or {}).get('ranking', []),
                           stopped_by, http=http, kind=row['kind'], budget=row['budget'])
    if stop.requested(stop_key(research_id)) and not stopped_by:
        # CLAUDE> Stop pressed while the advice was being written still ends the research as stopped
        stopped_by = written['stopped_by'] = STOPPED_BY_YOU
    unread = [p['url'] for p in store.pages(research_id) if p['status'] == 'failed' or (p['status'] == 'found' and p['error'])]
    store.update(research_id, result=row['result'] | written | {'unread': unread, 'requirements': row['requirements'] or []},
                 state='stopped' if stopped_by == STOPPED_BY_YOU else 'done', step='advise')
