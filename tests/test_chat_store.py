"""Chats and their messages in the ledger: questions, answers, the chosen answer of each turn and what the model gets."""

from automation_desk.chat import store


def answered(chat_id: int, turn: int, model: str, text: str, cost: float = 0.01, chosen: bool = True) -> int:
    """An answer of `turn` that is done."""
    mid = store.add_answer(chat_id, turn, model, chosen=chosen)
    store.update_message(mid, content=text, state='done', cost_usd=cost)
    return mid


def test_a_chat_starts_with_its_first_question_and_a_title_from_it() -> None:
    chat_id = store.create('Propose a supplement regime for a 65 year old male, with doses and the reasons for each one',
                           'openai/gpt-6.1-sol', web=True)
    chat = store.get(chat_id)
    assert chat['title'] == 'Propose a supplement regime for a 65 year old male, with…'
    assert chat['model'] == 'openai/gpt-6.1-sol' and chat['cost_usd'] == 0
    [question] = chat['messages']
    assert question | {'id': 0, 'created': ''} == {
        'id': 0, 'chat_id': chat_id, 'turn': 1, 'role': 'user', 'model': '', 'web': True, 'sources': [], 'chosen': False,
        'state': 'done', 'error': '', 'cost_usd': None, 'seconds': None, 'job_id': '', 'generation_id': '', 'created': '',
        'content': 'Propose a supplement regime for a 65 year old male, with doses and the reasons for each one'}


def test_the_model_gets_each_turn_with_its_chosen_answer_then_the_new_question() -> None:
    chat_id = store.create('Q1', 'm1', web=False)
    answered(chat_id, 1, 'm1', 'A1 first')
    second = answered(chat_id, 1, 'm2', 'A1 second opinion', chosen=False)
    store.add_question(chat_id, 'Q2', web=False)
    failed = store.add_answer(chat_id, 2, 'm1', chosen=True)
    store.update_message(failed, state='failed', error='OpenRouter returned 500')
    store.add_question(chat_id, 'Q3', web=True)
    assert store.history(chat_id, 3) == [{'role': 'user', 'content': 'Q1'}, {'role': 'assistant', 'content': 'A1 first'},
                                         {'role': 'user', 'content': 'Q3'}], 'a turn without an answer is left out'
    store.choose(second)
    assert store.history(chat_id, 3)[1] == {'role': 'assistant', 'content': 'A1 second opinion'}
    assert store.get(chat_id)['model'] == 'm2', 'the chat goes on with the model of the chosen answer'
    assert store.history(chat_id, 1) == [{'role': 'user', 'content': 'Q1'}], 'a second opinion gets only the history before it'


def test_a_stopped_answer_with_text_counts_but_an_empty_one_does_not() -> None:
    chat_id = store.create('Q1', 'm1', web=False)
    mid = store.add_answer(chat_id, 1, 'm1', chosen=True)
    store.update_message(mid, state='stopped', content='')
    store.add_question(chat_id, 'Q2', web=False)
    assert store.history(chat_id, 2) == [{'role': 'user', 'content': 'Q2'}]
    store.update_message(mid, content='Part of it')
    assert store.history(chat_id, 2)[1] == {'role': 'assistant', 'content': 'Part of it'}


def test_the_list_shows_each_chat_newest_first_with_its_cost() -> None:
    old = store.create('Old question', 'm1', web=False)
    answered(old, 1, 'm1', 'A', cost=0.002)
    new = store.create('New question', 'm2', web=False)
    answered(new, 1, 'm2', 'A', cost=0.01)
    answered(new, 1, 'm3', 'B', cost=0.03, chosen=False)
    store.update(old, title='Renamed')
    assert [(c['id'], c['title'], c['model'], round(c['cost_usd'], 4), c['questions']) for c in store.listing()] == [
        (old, 'Renamed', 'm1', 0.002, 1), (new, 'New question', 'm2', 0.04, 1)], 'a rename counts as a change'
    assert store.last_model() == 'm1'


def test_answers_still_written_when_the_app_stopped_become_failed() -> None:
    chat_id = store.create('Q', 'm', web=False)
    mid = store.add_answer(chat_id, 1, 'm', chosen=True)
    store.update_message(mid, content='Half')
    assert store.writing(chat_id)['id'] == mid
    assert store.fail_writing() == 1
    message = store.message(mid)
    assert (message['state'], message['content'], message['error']) == ('failed', 'Half', store.RESTARTED)
    assert store.writing(chat_id) is None


def test_deleting_a_chat_deletes_its_messages() -> None:
    chat_id = store.create('Q', 'm', web=False)
    mid = answered(chat_id, 1, 'm', 'A')
    assert store.delete(chat_id) and store.get(chat_id) is None and store.message(mid) is None
    assert not store.delete(chat_id)


def test_the_real_cost_of_each_model_is_the_average_of_its_answers() -> None:
    chat_id = store.create('Q', 'm1', web=False)
    answered(chat_id, 1, 'm1', 'A', cost=0.01)
    answered(chat_id, 1, 'm1', 'B', cost=0.03, chosen=False)
    answered(chat_id, 1, 'm2', 'C', cost=0.5, chosen=False)
    web = store.add_question(chat_id, 'Q2', web=True)
    answered(chat_id, 2, 'm1', 'D', cost=0.9)
    assert store.message(web)['web'] is True
    assert store.model_costs() == {'m1': 0.02, 'm2': 0.5}, 'answers with web search cost more: they are left out'
