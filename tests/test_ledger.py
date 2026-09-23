"""The SQLite ledger: jobs replace themselves, fields round-trip, totals add up, concurrent writers are fine."""

import threading

from llm_automation import jobs, ledger


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
