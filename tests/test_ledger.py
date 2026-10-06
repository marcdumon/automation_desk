"""The SQLite ledger: jobs replace themselves, fields round-trip, totals add up, concurrent writers are fine."""

import threading

from automation_desk import jobs, ledger


def call(cost: float, model: str = 'm', stage: str = 'preview') -> jobs.LLMCall:
    """A model call with a cost and full request/response."""
    return jobs.LLMCall(purpose='p', model_requested=model, model_used=model, provider='Google', prompt_tokens=100,
                        completion_tokens=10, cost_usd=cost, latency_ms=5, finish_reason='stop', generation_id='gen',
                        system='s', user='u', reply='r', request={'messages': [{'role': 'user', 'content': 'é ✓'}]},
                        response={'id': 'gen', 'usage': {'cost': cost}}, stage=stage)


def test_saving_again_replaces_the_job_and_fields_round_trip() -> None:
    job = jobs.Job(group='tasks', sentence='zin', task_id='t', task_name='T')
    job.llm_calls.append(call(0.001))
    job.google_calls.append(jobs.GoogleCall(method='tasks.tasks.list', params={'tasklist': 'L1', 'x': [1, None]}, latency_ms=3))
    job.preview = {'summary': 'Set 1 task(s)', 'rows': [{'id': 'a', 'cells': {'Task': 'Zalando'}}]}
    jobs.save(job)
    job.llm_calls.append(call(0.002, stage='apply'))
    job.applied_at, job.apply_status, job.results, job.applied_rows = '2026-09-23T10:00:00+02:00', 'ok', ['done'], ['a']
    jobs.save(job)

    stored = ledger.all_jobs()
    assert len(stored) == 1, 'a second save replaces, never duplicates'
    full = stored[0]
    assert [c['stage'] for c in full['llm_calls']] == ['preview', 'apply']
    assert full['llm_calls'][0]['request'] == {'messages': [{'role': 'user', 'content': 'é ✓'}]}
    assert full['google_calls'][0]['params'] == {'tasklist': 'L1', 'x': [1, None]}
    assert (full['preview']['rows'][0]['cells'], full['results'], full['applied_rows']) == ({'Task': 'Zalando'}, ['done'], ['a'])
    summary = full['summary']
    assert (round(summary['cost_usd'], 6), summary['llm_calls'], summary['google_calls'], summary['results']) == (0.003, 2, 1, 1)
    assert summary['apply_status'] == 'ok' and summary['models'] == ['m']


def test_totals_per_group_and_model_and_group_filter() -> None:
    for group, costs in (('tasks', [0.001, 0.002]), ('gmail', [0.004])):
        job = jobs.Job(group=group, sentence=group)
        job.llm_calls += [call(c, model='a' if c < 0.004 else 'b') for c in costs]
        jobs.save(job)
    totals = ledger.totals()
    assert round(totals['total_cost_usd'], 6) == 0.007
    assert {k: round(v['cost_usd'], 6) for k, v in totals['by_group'].items()} == {'gmail': 0.004, 'tasks': 0.003}
    assert {k: v['calls'] for k, v in totals['by_model'].items()} == {'a': 2, 'b': 1}
    assert [s['group'] for s in ledger.summaries('gmail')] == ['gmail']
    assert ledger.detail('nope') is None


def test_writers_in_parallel_do_not_lose_jobs() -> None:
    errors: list[BaseException] = []

    def write(n: int) -> None:
        """Save twenty jobs, each twice (as a preview and after its apply); keep any error for the test to see."""
        try:
            for i in range(20):
                job = jobs.Job(group='g', sentence=f'{n}-{i}')
                job.llm_calls.append(call(0.001))
                jobs.save(job)
                job.apply_status = 'ok'
                jobs.save(job)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], 'a writer failed instead of waiting its turn'
    assert len(ledger.summaries()) == 160
    assert round(ledger.totals()['total_cost_usd'], 6) == 0.16


def test_web_search_fees_are_a_line_of_their_own_next_to_the_models() -> None:
    """A search call costs OpenRouter's search fee plus the model's tokens; the fee is the cost minus the model part
    (cost_details.upstream_inference_cost) and shows apart, not hidden in the model's cost."""
    job = jobs.Job(group='research', sentence='r')
    search = call(0.007421675, model='openai/gpt-6-luna')
    search.request = {'plugins': [{'id': 'web', 'engine': 'exa', 'max_results': 10}], 'messages': []}
    search.response = {'usage': {'cost': 0.007421675, 'cost_details': {'upstream_inference_cost': 0.000421675}}}
    job.llm_calls += [search, call(0.0003, model='openai/gpt-6-luna')]
    jobs.save(job)
    by_model = ledger.totals()['by_model']
    assert round(by_model['Exa web search (via OpenRouter)']['cost_usd'], 6) == 0.007
    assert by_model['Exa web search (via OpenRouter)']['calls'] == 1
    assert round(by_model['openai/gpt-6-luna']['cost_usd'], 6) == round(0.000421675 + 0.0003, 6)
    assert round(ledger.totals()['total_cost_usd'], 6) == round(0.007421675 + 0.0003, 6), 'the total stays the same'


def test_a_job_with_nothing_to_tick_cannot_be_applied() -> None:
    """News digests and research showed 'Applied: no' while they have nothing to apply."""
    tickable = jobs.Job(group='calendar', sentence='add events')
    tickable.preview = {'summary': '2 new', 'rows': [{'id': 'a', 'selectable': True}, {'id': 'b', 'selectable': False}]}
    answer = jobs.Job(group='news', sentence='digest')
    answer.preview = {'summary': 'Digest made', 'rows': []}
    for job in (tickable, answer):
        jobs.save(job)
    assert {s['sentence']: s['appliable'] for s in ledger.summaries()} == {'add events': True, 'digest': False}
    assert tickable.summary()['appliable'] is True and jobs.Job(group='research', sentence='r').summary()['appliable'] is False
