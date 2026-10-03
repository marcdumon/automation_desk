"""Research rows, page rows and settings in the ledger."""

from automation_desk.research import store


def test_a_research_keeps_its_request_answers_and_state() -> None:
    rid = store.create('A pump for the lift pit', None, ['BE', 'NL'])
    store.update(rid, state='budget', answers={'q1': 'Sewer'}, kind='product')
    row = store.get(rid)
    assert (row['request'], row['budget'], row['countries'], row['state'], row['answers'], row['kind']) == (
        'A pump for the lift pit', None, ['BE', 'NL'], 'budget', {'q1': 'Sewer'}, 'product')
    assert [r['id'] for r in store.listing()] == [rid]
    assert store.delete(rid) and store.get(rid) is None and not store.delete(rid)


def test_pages_are_added_once_and_updated() -> None:
    rid = store.create('x', 100, ['BE'])
    assert store.add_page(rid, 'https://shop.be/p', 'pomp', 'Pomp', 'snippet', 'BE')
    assert not store.add_page(rid, 'https://shop.be/p', 'pomp 2', 'Pomp', 'snippet', 'BE')
    store.update_page(rid, 'https://shop.be/p', status='read', facts={'products': []})
    assert [(p['url'], p['status'], p['facts']) for p in store.pages(rid, 'read')] == [
        ('https://shop.be/p', 'read', {'products': []})]


def test_settings_have_defaults_and_keep_changes() -> None:
    assert store.settings() == {'countries': ['BE', 'NL', 'DE', 'FR'], 'municipality': '',
                                'limits': {'searches': 20, 'pages': 30, 'cost': 1.0}, 'vault': '', 'subdir': 'Research'}
    store.save_settings(municipality='Antwerpen', limits={'searches': 10, 'pages': 30, 'cost': 0.5})
    assert store.settings()['municipality'] == 'Antwerpen' and store.settings()['limits']['cost'] == 0.5


def test_a_research_that_was_running_turns_failed() -> None:
    rid = store.create('x', 100, ['BE'])
    store.update(rid, state='running', step='read')
    assert store.fail_running() == 1
    row = store.get(rid)
    assert (row['state'], row['step']) == ('failed', 'read') and 'stopped' in row['note']


def test_the_list_shows_each_research_by_its_title_and_result() -> None:
    rid = store.create('A pump for the lift pit when it rains, long text', 500, ['BE'])
    store.update(rid, title='Lift pit pump', state='done', kind='product', result={'best': {'title': 'Gardena 19500', 'score': 75,
                                                                                           'total': 209.99}})
    item = store.listing()[0]
    assert (item['title'], item['kind'], item['best']) == ('Lift pit pump', 'product', {'title': 'Gardena 19500', 'score': 75,
                                                                                       'total': 209.99})
