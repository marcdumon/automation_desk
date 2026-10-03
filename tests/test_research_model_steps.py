"""The model steps with a stand-in model: code checks and completes every answer."""

from automation_desk.research import advice, plan, questions
from automation_desk.research.search import Hit


def test_question_ids_are_made_by_code_and_the_count_is_kept(monkeypatch) -> None:
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: questions.Form(kind='product', unknowns=['Water depth'], questions=[
        questions.Question(id='x', text=f'Q{n}?', choices=['a', 'b']) for n in range(10)]))
    form = questions.make('A pump for the lift pit', None, ['BE'])
    assert [q.id for q in form.questions] == [f'q{n}' for n in range(1, 9)] and form.unknowns == ['Water depth']


def test_price_classes_are_sorted_and_checked(monkeypatch) -> None:
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: questions.PriceClasses(classes=[
        questions.PriceClass(name='Professional', low=350, high=900, difference='cast iron'),
        questions.PriceClass(name='Broken', low=500, high=100, difference=''),
        questions.PriceClass(name='Basic', low=80, high=150, difference='plastic')]))
    hits = [Hit(url='https://a.be', title='Pomp', snippet='€ 99', country='BE')]
    assert [c.name for c in questions.price_classes('pump', {}, hits)] == ['Basic', 'Professional']


def test_plan_keeps_selected_countries_and_the_search_limit(monkeypatch) -> None:
    monkeypatch.setattr(plan, 'ask', lambda *a, **k: plan.Plan(queries=[
        plan.Query(country='BE', text='dompelpomp vlotter'), plan.Query(country='BE', text='dompelpomp vlotter'),
        plan.Query(country='IT', text='pompa'), plan.Query(country='', text='DAB Nova 300 review'),
        *[plan.Query(country='DE', text=f'Tauchpumpe {n}') for n in range(30)]]))
    queries = plan.make('pump', 'product', {}, 200, ['BE', 'DE'], '', max_searches=5)
    assert queries[:2] == [{'country': 'BE', 'text': 'dompelpomp vlotter'}, {'country': '', 'text': 'DAB Nova 300 review'}]
    assert len(queries) == 5 and all(q['country'] in ('BE', 'DE', '') for q in queries)


COMPARISON = {
    'products': [{'n': 1, 'name': 'A', 'best_total': 120.0, 'offers': [{'price': 115.0, 'delivery': 5.0, 'total': 120.0}]},
                 {'n': 2, 'name': 'B', 'best_total': 180.0, 'offers': [{'price': 180.0, 'delivery': None, 'total': 180.0}]}],
    'over_budget': [{'n': 3, 'name': 'C', 'best_total': 300.0, 'offers': [{'price': 300.0, 'delivery': None, 'total': 300.0}]}],
    'no_price': [],
}


REQS = [{'id': 'r1', 'text': 'Pumps dirty water', 'weight': 'must'}, {'id': 'r2', 'text': 'Starts by itself', 'weight': 'nice'}]


def entry(title: str, total: float | None, tag: str = '') -> dict:
    """One ranked product as score.rank makes it."""
    return {'n': 1, 'title': title, 'total': total, 'tag': tag, 'checks': {'r1': 'yes', 'r2': 'unknown'}, 'pros': ['Strong'],
            'cons': [], 'url': 'https://a.be/p'}


RANKING = [entry('B', 180.0, 'recommended'), entry('A', 120.0, 'cheapest good choice'), entry('C', 300.0)]


def test_the_advice_is_about_the_recommended_product_only(monkeypatch) -> None:
    """The model gets the chosen product, its checks and its price; it never sees the other products."""
    seen = {}

    def ask(system: str, user: str, schema: type, **kwargs: object) -> advice.Advice:
        """Keep the prompt."""
        seen['user'] = user
        return advice.Advice(summary='B for € 180,00 is best. A was once € 99.', risks=['Ask the lift company.'], reasons='B fits.')

    monkeypatch.setattr(advice, 'ask', ask)
    result = advice.write('pump', {}, REQS, RANKING, stopped_by='')
    assert result['best']['title'] == 'B' and 'Chosen product: B' in seen['user'] and 'Pumps dirty water (must): meets' in seen['user']
    assert 'A' not in seen['user'].split('Chosen product')[1].replace('Answers', '')
    assert '180,00' in result['summary'] and '99' not in result['summary']


def test_product_numbers_are_removed_from_the_advice(monkeypatch) -> None:
    """Research 1 said "Product 21 is the strongest match": a number the page never shows."""
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: advice.Advice(
        summary='Product 21 is the strongest match. B handles dirt.', reasons='Unlike option 3, B lifts 10 m. It starts by '
        'itself.', risks=['Compare with #4 first.', 'Check the hose.']))
    result = advice.write('pump', {}, REQS, RANKING, stopped_by='')
    assert (result['summary'], result['reasons'], result['risks']) == ('B handles dirt.', 'It starts by itself.', ['Check the hose.'])


def test_without_a_recommendation_no_model_is_asked(monkeypatch) -> None:
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no model call')))
    result = advice.write('pump', {}, REQS, [entry('C', 300.0)], stopped_by='the page limit')
    assert result['best'] is None and 'must requirements' in result['summary'] and result['stopped_by'] == 'the page limit'


def test_followups_are_at_most_two(monkeypatch) -> None:
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: advice.Followups(questions=[
        questions.Question(id='x', text=f'F{n}?', choices=['yes', 'no']) for n in range(4)]))
    assert [q.id for q in advice.followups('pump', {}, COMPARISON)] == ['f1', 'f2']


def test_sentences_with_urls_and_domains_are_removed(monkeypatch) -> None:
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: advice.Advice(
        summary='B is best. Check www.example.com for details. Also good.',
        risks=['Verify https://shop.example for price.', 'Check warranty terms.'], reasons='B has good support.'))
    result = advice.write('pump', {}, REQS, RANKING, stopped_by='')
    assert result['summary'] == 'B is best. Also good.' and result['risks'] == ['Check warranty terms.']


def test_decimal_specs_not_treated_as_domains(monkeypatch) -> None:
    """Decimal specs (0.75 kW, 3.5mm) and abbreviations (e.g.) stay; domains and links go."""
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: advice.Advice(
        summary='Motor 0.75 kW. Hose 3.5mm. Also check pompen.nl.',
        risks=['Weighs 1.25 kg.', 'Distance 7.2 m.', 'E.g. check manual.', 'See www.shop.example for specs.'],
        reasons='Capacity 2.5 L per second.'))
    result = advice.write('pump', {}, REQS, RANKING, stopped_by='')
    assert result['summary'] == 'Motor 0.75 kW. Hose 3.5mm.' and result['reasons'] == 'Capacity 2.5 L per second.'
    assert result['risks'] == ['Weighs 1.25 kg.', 'Distance 7.2 m.', 'E.g. check manual.']


def test_dollar_and_pound_amounts_not_in_the_table_are_removed(monkeypatch) -> None:
    """Amounts in $, £, dollars or pounds the table does not have go too; a table amount in thousands stays."""
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: advice.Advice(
        risks=['In the US it is $899.', 'In the UK £ 750.', 'Was 999 dollars.', 'Or 650 pounds.', 'Check the hose.'],
        summary='B costs € 1.299,00 in total.', reasons=''))
    result = advice.write('pump', {}, REQS, [entry('B', 1299.0, 'recommended')], stopped_by='')
    assert result['risks'] == ['Check the hose.'] and result['summary'] == 'B costs € 1.299,00 in total.'


def test_a_service_company_without_a_price_can_be_recommended(monkeypatch) -> None:
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: advice.Advice(summary='Jan fits.', risks=[], reasons=''))
    result = advice.write('window cleaner', {}, REQS, [entry('Jan', None, 'recommended')], stopped_by='', kind='service')
    assert result['best']['title'] == 'Jan' and result['summary'] == 'Jan fits.'


def test_delivery_is_not_claimed_and_the_budget_may_be_named(monkeypatch) -> None:
    seen = {}

    def ask(system: str, user: str, schema: type, **kwargs: object) -> advice.Advice:
        """Keep the prompt."""
        seen['user'] = user
        return advice.Advice(summary='It fits within your € 500 budget. There is no 15 cm start problem.', risks=[], reasons='')

    monkeypatch.setattr(advice, 'ask', ask)
    best = entry('B', 180.0, 'recommended') | {'delivery_known': False}
    result = advice.write('pump', {}, REQS, [best], stopped_by='', budget=500)
    assert 'Price: € 180.00 (delivery cost not known)' in seen['user']
    assert result['summary'] == 'It fits within your € 500 budget. There is no 15 cm start problem.'


def test_without_any_product_the_advice_says_nothing_was_compared(monkeypatch) -> None:
    monkeypatch.setattr(advice, 'ask', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no model call')))
    assert advice.write('pump', {}, REQS, [], stopped_by='you stopped it')['summary'].startswith('No product could be compared')


def test_the_question_form_brings_a_short_title(monkeypatch) -> None:
    monkeypatch.setattr(questions, 'ask', lambda *a, **k: questions.Form(kind='product', unknowns=[], questions=[],
                                                                          title='  Lift pit   sump pump. '))
    assert questions.make('i live in a 8 story apartment building…', None, ['BE']).title == 'Lift pit sump pump'
