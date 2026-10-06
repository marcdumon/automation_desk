"""The engine with stand-ins for the search, the pages and every model step."""

import threading

import httpx
import pytest

from automation_desk import jobs, ledger, stop
from automation_desk.research import advice, offers, pages, plan, questions, requirements, run, score, search, store, summary


@pytest.fixture
def standins(monkeypatch) -> dict:
    """Every outside call replaced; `calls` counts searches and page reads."""
    calls = {'search': 0, 'read': 0}
    count = threading.Lock()

    def fake_search(query: str, http: object = None) -> list[search.Hit]:
        """Two shop pages per search, one shared by all searches; the other named after the query, so threads cannot clash."""
        with count:
            calls['search'] += 1
        return [search.Hit(url='https://a.be/p', title='A', snippet='', country='BE'),
                search.Hit(url=f'https://b-{query}.nl/p', title='B', snippet='', country='NL')]

    def fake_read(url: str, http: object) -> pages.Read:
        """Every page offers one product at € 100."""
        with count:
            calls['read'] += 1
        return pages.Read(url=url, text='Pomp € 100,00', products=[])

    monkeypatch.setattr(search, 'run', fake_search)
    monkeypatch.setattr(pages, 'read', fake_read)
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='Pomp', brand='X', model='P1', price=100.0, specs=[], contact='')], ships_to_belgium='yes', delivery_cost=None))
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: questions.Form(kind='product', unknowns=[], questions=[
        questions.Question(id='', text='Where does the water go?', choices=['Sewer', 'Drain'])]))
    monkeypatch.setattr(plan, 'ask', lambda *a, **k: plan.Plan(queries=[plan.Query(country='BE', text=f'q{n}') for n in range(3)]))
    monkeypatch.setattr(advice, 'ask', lambda system, *a, **k: advice.Followups(questions=[]) if 'buyer has not told' in system
                        else advice.Advice(reasons='Fits.', risks=[], summary='Pomp.'))
    monkeypatch.setattr(requirements, 'ask', lambda *a, **k: requirements.Requirements(requirements=[
        requirements.Requirement(text='Pumps the water away', weight='must')]))
    monkeypatch.setattr(score, 'ask', lambda *a, **k: score.Ratings(products=[]))
    return calls


def cost(usd: float) -> None:
    """Record a model call of `usd` on the running job, as a paid call would."""
    jobs.current().llm_calls.append(jobs.LLMCall(
        purpose='test', model_requested='m', model_used='m', provider='p', prompt_tokens=0, completion_tokens=0,
        cost_usd=usd, latency_ms=0, finish_reason='stop', generation_id='g', system='', user='', reply=''))


def started(budget: float | None = 150) -> int:
    """A research past its question form."""
    rid = run.start('A pump for the lift pit', budget, ['BE', 'NL'])
    run.answer(rid, {'q1': 'Sewer'}, 'product')
    run.advance(rid)
    assert store.get(rid)['state'] == 'requirements'
    run.confirm_requirements(rid, store.get(rid)['requirements'])
    return rid


def test_a_research_runs_from_questions_to_advice(standins) -> None:
    rid = started()
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'done' and row['result']['best']['name'] == 'Pomp'
    assert standins == {'search': 3, 'read': 4}, '3 searches; a.be found 3 times is read once'


def test_without_budget_it_stops_at_the_price_classes(standins, monkeypatch) -> None:
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: questions.PriceClasses(classes=[
        questions.PriceClass(name='Basic', low=80, high=150, difference='')]) if k.get('purpose', '').endswith('classes')
        else questions.Form(kind='product', unknowns=[], questions=[]))
    rid = started(budget=None)
    run.advance(rid)
    assert store.get(rid)['state'] == 'budget' and store.get(rid)['classes'][0]['name'] == 'Basic'
    run.set_budget(rid, 150)
    run.advance(rid)
    assert store.get(rid)['state'] == 'done'


def test_a_followup_question_pauses_and_the_reply_continues_without_new_searches(standins, monkeypatch) -> None:
    monkeypatch.setattr(advice, 'ask', lambda system, *a, **k: advice.Followups(questions=[questions.Question(
        id='', text='Is the lift hydraulic?', choices=['yes', 'no'])]) if 'buyer has not told' in system
        else advice.Advice(reasons='', risks=[], summary=''))
    rid = started()
    run.advance(rid)
    assert store.get(rid)['state'] == 'waiting' and store.get(rid)['followups'][0]['id'] == 'f1'
    searched = standins['search']
    run.reply(rid, {'f1': 'yes'})
    run.advance(rid)
    assert store.get(rid)['state'] == 'done' and standins['search'] == searched


def test_the_search_limit_stops_searching_but_advice_is_written(standins, monkeypatch) -> None:
    # CLAUDE> plan.make already cuts the plan to the limit, so a plan longer than the limit needs its own stand-in
    monkeypatch.setattr(plan, 'make', lambda *a, **k: [{'country': 'BE', 'text': f'q{n}'} for n in range(3)])
    store.save_settings(limits={'searches': 2, 'pages': 30, 'cost': 1.0})
    rid = started()
    run.advance(rid)
    row = store.get(rid)
    assert standins['search'] == 2 and row['state'] == 'done' and row['result']['stopped_by'] == 'the search limit'
    assert standins['read'] == 3, 'the pages already found are still read'


def test_stop_ends_with_the_advice_from_what_was_found(standins, monkeypatch) -> None:
    monkeypatch.setattr(run, 'WORKERS', 1)
    rid = started()
    real = pages.read
    monkeypatch.setattr(pages, 'read', lambda url, http: (stop.request(run.stop_key(rid)), real(url, http))[1])
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'stopped' and row['result']['stopped_by'] == 'you stopped it' and standins['read'] == 1


def test_a_failure_keeps_the_step_and_continue_resumes(standins, monkeypatch) -> None:
    rid = started()
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('OpenRouter returned 502')))
    run.advance(rid)
    row = store.get(rid)
    assert (row['state'], row['step']) == ('failed', 'read') and '502' in row['note']
    assert all('502' in (p['error'] or '') for p in store.pages(rid)), 'each page keeps its own error'
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[], ships_to_belgium='yes', delivery_cost=None))
    run.resume(rid)
    run.advance(rid)
    assert store.get(rid)['state'] == 'done'


def test_running_research_after_restart_is_failed(standins) -> None:
    rid = started()
    store.update(rid, state='running', step='plan')
    store.fail_running()
    run.resume(rid)
    run.advance(rid)
    assert store.get(rid)['state'] == 'done'


def test_one_research_at_a_time(standins, monkeypatch) -> None:
    first, second = started(), started()
    inside = threading.Event()
    release = threading.Event()
    real = pages.read

    def slow(url: str, http: object) -> pages.Read:
        """Hold the first research inside its reads."""
        inside.set()
        release.wait(5)
        return real(url, http)

    monkeypatch.setattr(pages, 'read', slow)
    worker = threading.Thread(target=run.advance, args=(first,))
    worker.start()
    inside.wait(5)
    waiting = threading.Thread(target=run.advance, args=(second,))
    waiting.start()
    waiting.join(0.3)
    assert waiting.is_alive() and run.progress()['id'] == first
    release.set()
    worker.join(5)
    waiting.join(5)
    assert store.get(first)['state'] == store.get(second)['state'] == 'done'


def test_stop_during_the_followup_call_still_ends_stopped(standins, monkeypatch) -> None:
    rid = started()
    real = advice.ask
    monkeypatch.setattr(advice, 'ask', lambda system, *a, **k: (stop.request(run.stop_key(rid)), real(system, *a, **k))[1])
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'stopped' and row['result']['stopped_by'] == 'you stopped it'


def test_stop_during_the_advice_call_ends_stopped(standins, monkeypatch) -> None:
    rid = started()
    real = advice.ask
    monkeypatch.setattr(advice, 'ask', lambda system, *a, **k: (stop.request(run.stop_key(rid)) if 'buyer has not told' not in system
                                                                   else None, real(system, *a, **k))[1])
    run.advance(rid)
    assert store.get(rid)['state'] == 'stopped'


def test_advance_returns_early_on_missing_row(monkeypatch) -> None:
    """Advance returns silently if research is deleted."""
    # CLAUDE> Set up stand-ins that fail if called (should never reach them)
    monkeypatch.setattr(search, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('search should not be called')))
    monkeypatch.setattr(pages, 'read', lambda *a, **k: (_ for _ in ()).throw(AssertionError('read should not be called')))
    # CLAUDE> Calling advance with non-existent research should return early without error
    run.advance(999)


def test_advance_returns_early_on_non_running_state(standins, monkeypatch) -> None:
    """Advance returns silently if research is not in 'running' state."""
    rid = started()
    # CLAUDE> Set state to 'done' (not 'running')
    store.update(rid, state='done')
    # CLAUDE> Set up stand-ins that fail if called (should never reach them)
    fake_search_calls = []

    def fail_on_search(*a, **k):
        fake_search_calls.append('called')
        raise AssertionError('search should not be called')

    monkeypatch.setattr(search, 'run', fail_on_search)
    # CLAUDE> Calling advance should return early without calling search
    run.advance(rid)
    assert len(fake_search_calls) == 0


def test_the_model_sees_the_question_text_with_each_answer(standins, monkeypatch) -> None:
    """Answers go to the model as 'question: answer', never as 'q1: answer'."""
    prompts: dict[str, list[str]] = {'plan': [], 'classes': [], 'followups': [], 'advice': []}
    monkeypatch.setattr(questions, 'ask', lambda system, user, *a, **k: (prompts['classes'].append(user), questions.PriceClasses(
        classes=[questions.PriceClass(name='Basic', low=80, high=150, difference='')]))[1] if k.get('purpose', '').endswith(
        'classes') else questions.Form(kind='product', unknowns=[], questions=[
            questions.Question(id='', text='Where does the water go?', choices=['Sewer', 'Drain'])]))
    monkeypatch.setattr(plan, 'ask', lambda system, user, *a, **k: (prompts['plan'].append(user), plan.Plan(queries=[
        plan.Query(country='BE', text='q0')]))[1])
    monkeypatch.setattr(advice, 'ask', lambda system, user, *a, **k: (prompts['followups'].append(user), advice.Followups(
        questions=[questions.Question(id='', text='Is the lift hydraulic?', choices=['yes', 'no'])]))[1]
        if 'buyer has not told' in system else (prompts['advice'].append(user), advice.Advice(reasons='', risks=[], summary=''))[1])
    rid = started(budget=None)
    run.advance(rid)
    run.set_budget(rid, 150)
    run.advance(rid)
    run.reply(rid, {'f1': 'yes'})
    run.advance(rid)
    assert store.get(rid)['state'] == 'done'
    for step in ('classes', 'plan', 'followups', 'advice'):
        assert 'Where does the water go?: Sewer' in prompts[step][0] and 'q1' not in prompts[step][0], step
    assert 'Is the lift hydraulic?: yes' in prompts['advice'][0]


def test_one_bad_page_does_not_block_the_research(standins, monkeypatch) -> None:
    """A bad link and a model error on two pages: they are noted, the other pages are read, the advice is written."""
    real_read, real_ask = pages.read, offers.ask

    def bad_link(url: str, http: object) -> pages.Read:
        """One page has a link the reader cannot use."""
        if url == 'https://b-q0.nl/p':
            raise httpx.InvalidURL('Invalid non-printable ASCII character in URL')
        return real_read(url, http)

    def bad_answer(system: str, user: str, *a: object, **k: object) -> offers.Facts:
        """The model fails on one page."""
        if 'b-q1.nl' in user:
            raise ValueError('The model returned no JSON')
        return real_ask(system, user, *a, **k)

    monkeypatch.setattr(pages, 'read', bad_link)
    monkeypatch.setattr(offers, 'ask', bad_answer)
    rid = started()
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'done' and row['result']['best']['name'] == 'Pomp'
    errors = {p['url']: p['error'] for p in store.pages(rid) if p['error']}
    assert set(errors) == {'https://b-q0.nl/p', 'https://b-q1.nl/p'} and 'JSON' in errors['https://b-q1.nl/p']
    assert set(row['result']['unread']) == set(errors)


def test_the_stored_cost_is_up_to_date_while_the_research_runs(standins, monkeypatch) -> None:
    """Each page's cost is in the research and in the ledger before the advice starts; nothing is counted twice."""
    real_ask = offers.ask
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: (cost(0.01), real_ask(*a, **k))[1])
    seen = {}

    def advise(system: str, *a: object, **k: object) -> object:
        """Note the stored cost when the advice step calls the model."""
        if 'buyer has not told' in system:
            return advice.Followups(questions=[])
        seen['stored'], seen['ledger'] = store.get(rid)['cost_usd'], ledger.cost_since('research', '')
        cost(0.005)
        return advice.Advice(reasons='', risks=[], summary='')

    monkeypatch.setattr(advice, 'ask', advise)
    rid = started()
    run.advance(rid)
    assert seen == {'stored': pytest.approx(0.04), 'ledger': pytest.approx(0.04)}
    assert store.get(rid)['cost_usd'] == pytest.approx(0.045) and ledger.cost_since('research', '') == pytest.approx(0.045)


def test_a_com_shop_found_by_a_belgian_search_is_not_belgian(standins, monkeypatch) -> None:
    """The page country comes from its own domain; only a .be shop is taken to deliver in Belgium."""
    monkeypatch.setattr(search, 'run', lambda query, http=None: [search.Hit(url='https://shop.com/p', title='S', snippet='',
                                                                             country='')])
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='Pomp', brand='X', model='P1', price=100.0, specs=[], contact='')], ships_to_belgium='unknown', delivery_cost=None))
    rid = started()
    run.advance(rid)
    page = store.pages(rid)[0]
    assert (page['country'], page['facts']['ships_to_belgium']) == ('', 'unknown')


def test_a_service_can_be_advised_without_a_price(standins, monkeypatch) -> None:
    """Companies seldom show a price: the best company may be one without a price."""
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='Pompservice Peeters', brand='', model='', price=None, specs=[], contact='03 123 45 67', region='Antwerpen',
        reviews='4.6 (52)')], ships_to_belgium='yes', delivery_cost=None))
    rid = started()
    store.update(rid, kind='service')
    run.advance(rid)
    best = store.get(rid)['result']['best']
    assert best['name'] == 'Pompservice Peeters'
    assert (best['contact'], best['region'], best['reviews']) == ('03 123 45 67', 'Antwerpen', '4.6 (52)')


def test_the_page_limit_stops_reading_and_the_advice_is_written(standins) -> None:
    """At the page limit the reading ends; the advice uses the pages read."""
    store.save_settings(limits={'searches': 20, 'pages': 2, 'cost': 1.0})
    rid = started()
    run.advance(rid)
    row = store.get(rid)
    assert standins['read'] == 2 and row['state'] == 'done' and row['result']['stopped_by'] == 'the page limit'
    assert row['result']['best']['name'] == 'Pomp'


def test_the_cost_limit_stops_reading_and_the_advice_is_written(standins, monkeypatch) -> None:
    """Pages that cost $0.40 each: the third page passes the $1.00 limit (less the advice reserve), the fourth is not read."""
    monkeypatch.setattr(run, 'WORKERS', 1)
    real_ask = offers.ask
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: (cost(0.4), real_ask(*a, **k))[1])
    rid = started()
    run.advance(rid)
    row = store.get(rid)
    assert standins['read'] == 3 and row['state'] == 'done' and row['result']['stopped_by'] == 'the cost limit'
    assert row['result']['best']['name'] == 'Pomp' and row['cost_usd'] == pytest.approx(1.2)


def test_stop_belongs_to_one_research(standins) -> None:
    """A Stop for another research does not stop this one."""
    other, rid = started(), started()
    stop.request(run.stop_key(other))
    run.advance(rid)
    assert store.get(rid)['state'] == 'done'


def test_a_stop_pressed_while_the_research_waits_in_the_queue_is_kept(standins) -> None:
    """Stop pressed before the research gets its turn: it stops as soon as it runs."""
    rid = started()
    stop.request(run.stop_key(rid))
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'stopped' and standins['search'] == 0


def test_a_failed_question_form_leaves_no_research(monkeypatch) -> None:
    """When the questions cannot be made, no half research stays in the history and the error reaches the caller."""
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('OpenRouter returned 502')))
    with pytest.raises(RuntimeError, match='502'):
        run.start('A pump', 150, ['BE'])
    assert store.listing() == []


def test_a_queued_stop_also_stops_a_research_without_budget(standins, monkeypatch) -> None:
    """Stop pressed in the queue before the price classes: no class search or model call is paid."""
    asked = []
    monkeypatch.setattr(questions, 'price_classes', lambda *a, **k: asked.append(a) or [])
    rid = started(budget=None)
    stop.request(run.stop_key(rid))
    run.advance(rid)
    assert store.get(rid)['state'] == 'stopped' and standins['search'] == 0 and asked == []


def test_the_research_waits_for_the_user_to_check_the_requirements(standins, monkeypatch) -> None:
    """After the answers the model proposes requirements; nothing is searched until the user confirms them, as changed."""
    planned = {}
    monkeypatch.setattr(plan, 'ask', lambda system, user, *a, **k: (planned.setdefault('user', user), plan.Plan(queries=[
        plan.Query(country='BE', text='q0')]))[1])
    rid = run.start('A pump for the lift pit', 150, ['BE'])
    run.answer(rid, {'q1': 'Sewer'}, 'product')
    run.advance(rid)
    row = store.get(rid)
    assert (row['state'], row['requirements']) == ('requirements', [{'id': 'r1', 'text': 'Pumps the water away', 'weight': 'must'}])
    assert standins['search'] == 0
    run.confirm_requirements(rid, [{'text': 'Pumps dirty water', 'weight': 'must'}, {'text': 'Quiet', 'weight': 'nice'}])
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'done' and [r['text'] for r in row['result']['requirements']] == ['Pumps dirty water', 'Quiet']
    assert '- Pumps dirty water (must)' in planned['user']
    assert row['result']['ranking'][0]['tag'] == 'recommended' and row['result']['best']['title'] == 'Pomp — X P1'


def test_score_again_reuses_the_pages_read(standins, monkeypatch) -> None:
    """A finished research is scored again with changed requirements: no new search, no page read again, no new
    requirement call."""
    rid = started()
    run.advance(rid)
    searched, read = standins['search'], standins['read']
    monkeypatch.setattr(requirements, 'ask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('requirements are kept')))
    run.rescore(rid)
    run.advance(rid)
    assert store.get(rid)['state'] == 'requirements'
    run.confirm_requirements(rid, [{'text': 'Fits a 15 cm hole', 'weight': 'must'}])
    assert store.get(rid)['step'] == 'compare'
    run.advance(rid)
    row = store.get(rid)
    assert row['state'] == 'done' and (standins['search'], standins['read']) == (searched, read)
    assert row['result']['requirements'][0]['text'] == 'Fits a 15 cm hole'


def test_a_research_from_before_the_requirements_gets_them_when_scored_again(standins) -> None:
    """Research 1 was made before requirements existed: score again makes them first."""
    rid = started()
    run.advance(rid)
    store.update(rid, requirements=None)
    run.rescore(rid)
    run.advance(rid)
    assert store.get(rid)['requirements'][0]['text'] == 'Pumps the water away'


def test_score_again_needs_pages_read_and_can_be_cancelled(standins) -> None:
    rid = started()
    run.advance(rid)
    run.rescore(rid)
    run.advance(rid)
    assert store.get(rid)['state'] == 'requirements'
    run.cancel_requirements(rid)
    assert store.get(rid)['state'] == 'done' and store.get(rid)['result']['best']
    empty = run.start('Nothing read', 100, ['BE'])
    store.update(empty, state='done', result={'summary': 'x'})
    with pytest.raises(run.WrongState, match='no shop page'):
        run.rescore(empty)


def test_a_stop_in_the_queue_skips_the_paid_requirements_call(standins, monkeypatch) -> None:
    monkeypatch.setattr(requirements, 'ask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no requirements call')))
    rid = run.start('A pump', 150, ['BE'])
    run.answer(rid, {'q1': 'Sewer'}, 'product')
    stop.request(run.stop_key(rid))
    run.advance(rid)
    assert store.get(rid)['state'] == 'stopped'


@pytest.mark.parametrize('reason, says', [
    ('the page limit', 'after reading 30 shop pages'), ('the search limit', 'after 20 searches'),
    ('the cost limit', 'cost limit of $1.00'), ('you stopped it', 'You stopped the research'), ('', '')])
def test_the_stop_text_names_the_limit(reason: str, says: str) -> None:
    """"Stopped by the page limit" told the user nothing: the text names the number and where to change it."""
    text = run.stop_text({'limits': {'searches': 20, 'pages': 30, 'cost': 1.0}, 'result': {'stopped_by': reason}})
    assert says in text and 'Settings' not in text and 'raise' not in text, 'the facts only, no advice'


def test_score_again_with_edited_requirements_goes_straight_to_the_scores(standins) -> None:
    rid = started()
    run.advance(rid)
    searched = standins['search']
    run.rescore(rid, [{'text': 'Fits a 15 cm hole', 'weight': 'must'}, {'text': 'Quiet', 'weight': 'nice'}])
    row = store.get(rid)
    assert (row['state'], row['step']) == ('running', 'compare') and row['requirements'][1] == {'id': 'r2', 'text': 'Quiet',
                                                                                               'weight': 'nice'}
    run.advance(rid)
    assert store.get(rid)['state'] == 'done' and standins['search'] == searched


def test_pages_from_countries_not_chosen_are_not_kept_or_read(standins, monkeypatch) -> None:
    """The country choice steered only the search words; a French page answered a French-language search for Belgium."""
    monkeypatch.setattr(search, 'run', lambda query, http=None: [
        search.Hit(url='https://shop.fr/p', title='F', snippet='', country='FR'),
        search.Hit(url='https://shop.be/p', title='B', snippet='', country='BE'),
        search.Hit(url='https://shop.com/p', title='C', snippet='', country='')])
    rid = started()
    run.advance(rid)
    assert sorted(p['url'] for p in store.pages(rid)) == ['https://shop.be/p', 'https://shop.com/p']


@pytest.fixture
def summary_standins(monkeypatch) -> dict:
    """The site search, the page reader and the model replaced for a subject summary; `calls` keeps the sites searched,
    the pages read and the points the summary got."""
    calls = {'search': [], 'read': [], 'compiled': [], 'web': [], 'queries': 0}
    count = threading.Lock()

    def search_web_hits(query: str, http: object) -> list[search.Hit]:
        """Three pages of the web for each search; the second is about sport."""
        with count:
            calls['web'].append(query)
        slug = query.replace(' ', '-')
        return [search.Hit(url=f'https://web.be/{slug}/{name}', title='', snippet='', country='') for name in ('a', 'sport', 'c')]

    def search_site(subject: str, site: str, http: object) -> list[search.Hit]:
        """Three pages of the site; the second is about sport."""
        with count:
            calls['search'].append(site)
        return [search.Hit(url=f'https://{site}/{name}', title='', snippet='', country='') for name in ('a', 'sport', 'c')]

    def read(url: str, http: object) -> pages.Read:
        """Every page reads."""
        with count:
            calls['read'].append(url)
        return pages.Read(url=url, text=f'Text of {url}', products=[])

    def ask(system: str, user: str, schema: type, **kwargs: object) -> object:
        """A point per page about the subject; one group that uses the first point."""
        if schema is summary.Queries:
            calls['queries'] += 1
            return summary.Queries(queries=['flat roof rules', 'flat roof permit'])
        if schema is summary.KeyPoints:
            url = user.split('Page: ', 1)[1].split('\n', 1)[0]
            return summary.KeyPoints(relevant='sport' not in url, title=f'Title {url}', points=[f'Point of {url}.'])
        calls['compiled'].append(user)
        return summary.Compiled(title='Flat roofs', paragraphs=['Sum.'], groups=[summary.Group(heading='Rules', items=[
            summary.KeyPoint(text='A permit.', points=[1])])])

    monkeypatch.setattr(summary, 'search_site', search_site)
    monkeypatch.setattr(summary, 'search_web_hits', search_web_hits)
    monkeypatch.setattr(pages, 'read', read)
    monkeypatch.setattr(summary, 'ask', ask)
    return calls


def summary_started(*lines: str, target: dict | None = None) -> int:
    """A summary of flat roof rules from the sites and pages given, ready to run."""
    return run.start_summary('Rules for flat roofs', summary.parse_sites(list(lines or ('vrt.be', 'https://hln.be/b'))), target or {})


def test_a_summary_runs_from_the_site_searches_to_the_summary(summary_standins) -> None:
    rid = summary_started()
    assert (store.get(rid)['state'], store.get(rid)['kind'], store.get(rid)['step']) == ('running', 'summary', 'search')
    run.advance(rid)
    row = store.get(rid)
    assert (row['state'], row['title']) == ('done', 'Flat roofs') and row['result']['paragraphs'] == ['Sum.']
    assert summary_standins['search'] == ['vrt.be'], 'the page named is read, not searched'
    assert summary_standins['read'][0] == 'https://hln.be/b' and len(summary_standins['read']) == 4, 'Belgian pages are kept'
    assert row['result']['sources'] == [{'n': 1, 'url': 'https://hln.be/b', 'title': 'Title https://hln.be/b', 'site': 'hln.be'}]
    assert row['result']['off_subject'] == ['https://vrt.be/sport'] and row['result']['unread'] == []
    assert 'P3. Point of https://vrt.be/c.' in summary_standins['compiled'][0] and 'sport' not in summary_standins['compiled'][0]


def test_a_page_that_cannot_be_read_is_listed(summary_standins, monkeypatch) -> None:
    real = pages.read
    monkeypatch.setattr(pages, 'read', lambda url, http: (_ for _ in ()).throw(pages.Unreadable(f'{url}: a PDF'))
                        if url.endswith('/c') else real(url, http))
    rid = summary_started()
    run.advance(rid)
    assert store.get(rid)['result']['unread'] == ['https://vrt.be/c']


def test_stop_ends_the_summary_with_the_pages_read(summary_standins, monkeypatch) -> None:
    monkeypatch.setattr(run, 'WORKERS', 1)
    rid = summary_started()
    real = pages.read
    monkeypatch.setattr(pages, 'read', lambda url, http: (stop.request(run.stop_key(rid)), real(url, http))[1])
    run.advance(rid)
    row = store.get(rid)
    assert (row['state'], row['result']['stopped_by']) == ('stopped', 'you stopped it') and len(summary_standins['read']) == 1
    assert row['result']['groups'], 'the summary is made from the one page read'
    assert run.stop_text(row) == 'You stopped the summary. It uses the pages read until then.'


def test_the_page_limit_ends_the_reading_of_a_summary(summary_standins) -> None:
    store.save_settings(limits={'searches': 20, 'pages': 2, 'cost': 1.0})
    rid = summary_started()
    run.advance(rid)
    row = store.get(rid)
    assert len(summary_standins['read']) == 2 and (row['state'], row['result']['stopped_by']) == ('done', 'the page limit')
    assert run.stop_text(row) == 'The summary stopped after reading 2 pages, your page limit. It uses the pages read until then.'


def test_the_search_limit_leaves_sites_unsearched_but_reads_the_pages_found(summary_standins) -> None:
    store.save_settings(limits={'searches': 1, 'pages': 30, 'cost': 1.0})
    rid = summary_started('vrt.be', 'hln.be')
    run.advance(rid)
    row = store.get(rid)
    assert summary_standins['search'] == ['vrt.be'] and len(summary_standins['read']) == 3
    assert run.stop_text(row) == 'The summary stopped after 1 search, your search limit. It uses the pages read until then.'


def test_continue_after_a_failed_summary_pays_no_search_again(summary_standins, monkeypatch) -> None:
    rid = summary_started()
    real = summary.ask
    monkeypatch.setattr(summary, 'ask', lambda system, user, schema, **k: (_ for _ in ()).throw(
        RuntimeError('OpenRouter returned 502')) if schema is summary.Compiled else real(system, user, schema, **k))
    run.advance(rid)
    assert (store.get(rid)['state'], store.get(rid)['step']) == ('failed', 'compile')
    monkeypatch.setattr(summary, 'ask', real)
    run.resume(rid)
    run.advance(rid)
    assert store.get(rid)['state'] == 'done' and summary_standins['search'] == ['vrt.be'] and len(summary_standins['read']) == 4


def test_a_summary_keeps_the_note_it_goes_to_and_cannot_be_scored(summary_standins) -> None:
    target = {'vault': '/v', 'note': 'Projects/roof.md', 'heading': {'level': 2, 'text': 'Costs'}, 'level': 2}
    rid = summary_started(target=target)
    assert store.get(rid)['target'] == target and store.get(rid)['plan'] == [{'kind': 'site', 'site': 'vrt.be'},
                                                                            {'kind': 'page', 'url': 'https://hln.be/b'}]
    run.advance(rid)
    with pytest.raises(run.WrongState, match='summary'):
        run.rescore(rid)
    assert store.get(rid)['state'] == 'done'


def test_go_on_reads_the_pages_left_and_makes_the_result_again(standins) -> None:
    """Research 5 stopped at its page limit with 120 found pages not read, and nothing let the user read on."""
    store.save_settings(limits={'searches': 20, 'pages': 2, 'cost': 1.0})
    rid = started()
    run.advance(rid)
    row = store.get(rid)
    assert (row['result']['stopped_by'], standins['read'], run.left(row)) == ('the page limit', 2, {'pages': 2, 'searches': 0})
    run.go_on(rid)
    row = store.get(rid)
    assert (row['state'], row['step'], row['limits']['pages']) == ('running', 'search', 4)
    run.advance(rid)
    row = store.get(rid)
    assert (row['state'], row['result']['stopped_by'], standins['read'], run.left(row)) == ('done', '', 4, {'pages': 0, 'searches': 0})
    with pytest.raises(run.WrongState, match='Nothing is left'):
        run.go_on(rid)


def test_websites_added_to_a_summary_are_searched_and_the_summary_written_again(summary_standins) -> None:
    rid = summary_started('vrt.be')
    run.advance(rid)
    run.go_on(rid, summary.parse_sites(['hln.be', 'vrt.be']))
    row = store.get(rid)
    assert row['plan'] == [{'kind': 'site', 'site': 'vrt.be'}, {'kind': 'site', 'site': 'hln.be'}] and row['state'] == 'running'
    run.advance(rid)
    assert summary_standins['search'] == ['vrt.be', 'hln.be'] and len(summary_standins['read']) == 6
    assert store.get(rid)['state'] == 'done' and len(summary_standins['compiled']) == 2
    with pytest.raises(run.WrongState, match='in the summary already'):
        run.go_on(rid, summary.parse_sites(['hln.be']))


def test_a_failure_message_has_one_full_stop(summary_standins, monkeypatch) -> None:
    """The page showed "The test copy makes no model calls.. Press Continue"."""
    monkeypatch.setattr(summary, 'ask', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('No model calls.')))
    monkeypatch.setattr(pages, 'read', lambda url, http: (_ for _ in ()).throw(RuntimeError('No model calls.')))
    monkeypatch.setattr(summary, 'search_site', lambda *a: (_ for _ in ()).throw(RuntimeError('No model calls.')))
    rid = summary_started('vrt.be')
    run.advance(rid)
    assert store.get(rid)['note'] == 'No model calls. Press Continue to try this step again.'


def test_a_summary_costs_less_around_its_pages_than_a_research() -> None:
    """A summary has one model step (the summary itself); the shown cost was about 20 times the real one."""
    nothing = {'searches': 0, 'pages': 0, 'cost': 1.0}
    assert run.estimate(nothing, 'summary') < run.estimate(nothing) / 4


def test_a_summary_without_websites_searches_the_web_for_its_subject(summary_standins, monkeypatch) -> None:
    """A summary needed websites; without them it searches the whole web, with searches the model writes once."""
    rid = run.start_summary('Rules for flat roofs', [], {})
    real = summary.ask
    monkeypatch.setattr(summary, 'ask', lambda system, user, schema, **k: (_ for _ in ()).throw(RuntimeError('502'))
                        if schema is summary.Compiled else real(system, user, schema, **k))
    run.advance(rid)
    row = store.get(rid)
    assert row['plan'] == [{'kind': 'web', 'query': 'flat roof rules'}, {'kind': 'web', 'query': 'flat roof permit'}]
    assert summary_standins['web'] == ['flat roof rules', 'flat roof permit'] and len(summary_standins['read']) == 6
    assert run.left(row) == {'pages': 0, 'searches': 0}
    monkeypatch.setattr(summary, 'ask', real)
    run.resume(rid)
    run.advance(rid)
    assert store.get(rid)['state'] == 'done' and summary_standins['queries'] == 1, 'Continue writes no new searches'
    assert summary_standins['web'] == ['flat roof rules', 'flat roof permit'], 'and pays for no search again'


def test_the_rates_are_fetched_only_when_a_shop_shows_dollars_or_pounds(standins, monkeypatch) -> None:
    """A US shop's dollar price competes in euros; a research with only euro prices fetches no rates."""
    from automation_desk.research import rates

    fetched = []
    monkeypatch.setattr(rates, 'current', lambda http=None, today=None: fetched.append(1) or {'date': '2026-10-02', 'USD': 1.25})
    rid = started()
    run.advance(rid)
    assert fetched == [], 'only euro prices: no download'
    monkeypatch.setattr(offers, 'ask', lambda *a, **k: offers.Facts(products=[offers.FoundProduct(
        name='US pump', brand='X', model='U1', price=100.0, specs=[], contact='')], ships_to_belgium='yes', delivery_cost=None))
    monkeypatch.setattr(pages, 'read', lambda url, http: pages.Read(url=url, text='US pump $100.00', products=[]))
    rid = started()
    run.advance(rid)
    best = store.get(rid)['result']['ranking'][0]
    assert fetched and best['total'] == 96.8 and best['original']['currency'] == 'USD'


def test_a_request_for_something_free_has_a_budget_of_nothing_and_no_budget_question(standins, monkeypatch) -> None:
    """The mindmap research asked for price classes that were all € 0, for nothing."""
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: questions.Form(kind='product', unknowns=[], free=True, questions=[]))
    rid = run.start('A free mindmap program for Linux', None, ['BE'])
    assert store.get(rid)['budget'] == 0
    run.answer(rid, {}, 'product')
    run.advance(rid)
    run.confirm_requirements(rid, store.get(rid)['requirements'])
    assert store.get(rid)['step'] == 'plan', 'no price classes'
