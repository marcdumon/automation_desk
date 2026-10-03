"""Weighted requirements and the scores: the model gives verdicts, code makes the ranking and the reasons."""

from automation_desk.research import requirements, score

REQS = requirements.clean([{'text': 'Pumps dirty water', 'weight': 'must'}, {'text': 'Lifts 3 m', 'weight': 'must'},
                           {'text': 'Starts by itself', 'weight': 'important'}, {'text': 'Quiet', 'weight': 'nice'}])


def row(n: int, name: str, total: float | None) -> dict:
    """A product of the comparison with one offer."""
    return {'n': n, 'name': name, 'title': name, 'best_total': total, 'specs': {'Head': '5 m'},
            'offers': [{'url': f'https://shop.be/{n}', 'shop': 'shop.be', 'total': total, 'price_seen': True}]}


def rated(**verdicts: str) -> dict:
    """Checks for r1..r4 from a short string per product: y(es) p(artly) n(o) u(nknown)."""
    names = {'y': 'yes', 'p': 'partly', 'n': 'no', 'u': 'unknown'}
    return {int(k[1:]): {'checks': {f'r{i}': names[c] for i, c in enumerate(v, 1)}, 'pros': [], 'cons': []}
            for k, v in verdicts.items()}


def test_requirements_are_cleaned_and_numbered() -> None:
    kept = requirements.clean([{'text': '  Pumps   dirty water. ', 'weight': 'must'}, {'text': 'pumps dirty water', 'weight': 'nice'},
                               {'text': '', 'weight': 'must'}, {'text': 'Quiet', 'weight': 'loud'}])
    assert kept == [{'id': 'r1', 'text': 'Pumps dirty water', 'weight': 'must'}, {'id': 'r2', 'text': 'Quiet', 'weight': 'important'}]


def test_the_model_proposes_requirements_musts_first(monkeypatch) -> None:
    monkeypatch.setattr(requirements, 'ask', lambda *a, **k: requirements.Requirements(requirements=[
        requirements.Requirement(text='Quiet', weight='nice'), requirements.Requirement(text='Lifts 3 m', weight='must')]))
    assert [(r['id'], r['text']) for r in requirements.make('pump', 'product', {'Height?': '3 m'})] == [('r1', 'Lifts 3 m'),
                                                                                                        ('r2', 'Quiet')]


def test_scores_weigh_the_requirements() -> None:
    """must 3, important 2, nice 1; yes 1, partly 0.5, unknown 0.25, no 0."""
    assert score._score(REQS, {'r1': 'yes', 'r2': 'yes', 'r3': 'yes', 'r4': 'yes'}) == 100
    assert score._score(REQS, {'r1': 'yes', 'r2': 'partly', 'r3': 'unknown', 'r4': 'no'}) == round(100 * (3 + 1.5 + 0.5) / 9)


def test_the_ranking_puts_the_recommendation_first_and_says_why_the_others_are_not() -> None:
    rows = [row(1, 'Cheap but weak', 60.0), row(2, 'Good', 150.0), row(3, 'Fails dirt', 80.0), row(4, 'Not confirmed', 90.0),
            row(5, 'Over budget', 600.0), row(6, 'Good and cheaper', 120.0)]
    comparison = {'products': rows[:5] + rows[5:], 'over_budget': [rows[4]], 'no_price': []}
    verdicts = rated(r1='yyuu', r2='yyyy', r3='nyyy', r4='yuyy', r5='yyyy', r6='yyyn')
    ranking = score.rank(REQS, rows, verdicts, comparison, 500, 'product')
    assert [(e['title'], e['tag']) for e in ranking] == [
        ('Good', 'recommended'), ('Good and cheaper', 'cheapest good choice'), ('Cheap but weak', ''), ('Not confirmed', ''),
        ('Over budget', 'best above your budget'), ('Fails dirt', '')]
    why = {e['title']: e['why_not'] for e in ranking}
    # CLAUDE> requirements by their number (R1 …): the full texts repeated in every row were long and unreadable
    assert why['Good'] == '' and why['Good and cheaper'] == 'Weaker on: R4'
    assert why['Cheap but weak'] == 'Weaker on: R3, R4'
    assert why['Not confirmed'] == 'Not confirmed: R2', 'a requirement not confirmed is not also listed as weaker'
    assert why['Over budget'] == '€ 100.00 over your budget' and why['Fails dirt'] == 'Fails a must: R1'


def test_a_product_without_a_price_is_never_recommended_but_a_company_may_be() -> None:
    rows = [row(1, 'No price', None), row(2, 'Priced', 100.0)]
    comparison = {'products': rows, 'over_budget': [], 'no_price': []}
    product = score.rank(REQS, rows, rated(r1='yyyy', r2='yypn'), comparison, None, 'product')
    assert [(e['title'], e['tag']) for e in product] == [('Priced', 'recommended'), ('No price', '')]
    service = score.rank(REQS, rows, rated(r1='yyyy', r2='yypn'), comparison, None, 'service')
    assert service[0]['title'] == 'No price' and service[0]['tag'] == 'recommended'


def test_nothing_is_recommended_when_every_product_fails_a_must() -> None:
    rows = [row(1, 'A', 50.0)]
    ranking = score.rank(REQS, rows, rated(r1='nyyy'), {'products': rows, 'over_budget': [], 'no_price': []}, 100, 'product')
    assert ranking[0]['tag'] == '' and ranking[0]['why_not'] == 'Fails a must: R1'


def test_ratings_come_in_batches_and_a_missing_check_is_unknown(monkeypatch) -> None:
    """Forty products at once can pass the output limit: ten per call; what the model leaves out is 'unknown'."""
    calls = []

    def ask(system: str, user: str, schema: type, **kwargs: object) -> score.Ratings:
        """Rate the first product of each batch only, and only on r1."""
        numbers = [int(line.split('.')[0]) for line in user.split('Products:\n', 1)[1].splitlines()]
        calls.append(numbers)
        return score.Ratings(products=[score.Rated(n=numbers[0], checks=[score.Check(requirement='r1', verdict='yes'),
                                                                         score.Check(requirement='r9', verdict='yes')],
                                                   pros=['Strong', ' ', 'a', 'b', 'c'], cons=[])])

    monkeypatch.setattr(score, 'ask', ask)
    rows = [row(n, f'P{n}', 10.0 * n) for n in range(1, 24)]
    result = score.rate('pump', 'product', REQS, rows)
    assert [len(c) for c in calls] == [10, 10, 3]
    assert result[1] == {'checks': {'r1': 'yes', 'r2': 'unknown', 'r3': 'unknown', 'r4': 'unknown'},
                         'notes': {'r1': '', 'r2': '', 'r3': '', 'r4': ''}, 'pros': ['Strong', 'a', 'b'], 'cons': []}
    assert result[2]['checks'] == {f'r{i}': 'unknown' for i in range(1, 5)}


def test_a_requirement_written_with_its_text_still_counts(monkeypatch) -> None:
    """The real model wrote "r1: Handles muddy water…" instead of "r1" (research 1): every check came out 'unknown'."""
    monkeypatch.setattr(score, 'ask', lambda *a, **k: score.Ratings(products=[score.Rated(n=1, checks=[
        score.Check(requirement='r1: Pumps dirty water', verdict='yes'), score.Check(requirement=' R2 ', verdict='no'),
        score.Check(requirement='(r3) Starts by itself', verdict='partly')], pros=[], cons=[])]))
    result = score.rate('pump', 'product', REQS, [row(1, 'A', 10.0)])
    assert result[1]['checks'] == {'r1': 'yes', 'r2': 'no', 'r3': 'partly', 'r4': 'unknown'}


def test_delivery_is_known_only_when_the_shop_gave_it() -> None:
    """Research 1's advice said "€ 209.99 with delivery" while no shop showed a delivery cost."""
    rows = [row(1, 'A', 100.0)]
    rows[0]['offers'][0]['delivery'] = None
    entry = score.rank(REQS, rows, rated(r1='yyyy'), {'products': rows, 'over_budget': [], 'no_price': []}, None, 'product')[0]
    assert entry['delivery_known'] is False
    rows[0]['offers'][0]['delivery'] = 5.0
    assert score.rank(REQS, rows, rated(r1='yyyy'), {'products': rows, 'over_budget': [], 'no_price': []}, None,
                      'product')[0]['delivery_known'] is True


def test_the_shown_price_decides_if_it_was_seen() -> None:
    """The cheapest offer gives the price: only its own page counts for 'not seen on the page'."""
    rows = [row(1, 'A', 100.0)]
    rows[0]['offers'].append({'url': 'https://b.be/1', 'shop': 'b.be', 'total': 120.0, 'price_seen': False})
    assert score.rank(REQS, rows, rated(r1='yyyy'), {'products': rows, 'over_budget': [], 'no_price': []}, None,
                      'product')[0]['price_seen'] is True


def test_pros_and_cons_lose_prices_links_and_numbers(monkeypatch) -> None:
    monkeypatch.setattr(score, 'ask', lambda *a, **k: score.Ratings(products=[score.Rated(n=1, checks=[], pros=[
        'Cheaper than product 5', 'Sold at bol.com', 'Strong motor of 0.75 kW'], cons=['Costs € 20 more', 'Loud'])]))
    result = score.rate('pump', 'product', REQS, [row(1, 'A', 10.0)])
    assert (result[1]['pros'], result[1]['cons']) == (['Strong motor of 0.75 kW'], ['Loud'])


def test_follow_up_answers_reach_the_scores(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(score, 'ask', lambda system, user, *a, **k: (seen.setdefault('user', user), score.Ratings(products=[]))[1])
    score.rate('pump', 'product', REQS, [row(1, 'A', 10.0)], answers={'How deep is the pit?': '6 m'})
    assert '- How deep is the pit?: 6 m' in seen['user']


def test_every_candidate_is_scored() -> None:
    """Thirty pages of five products can give more than forty products inside the budget: none may vanish unscored."""
    many = [{'n': n} for n in range(1, 121)]
    assert len(score.candidates({'products': many, 'over_budget': [], 'no_price': []}, 'product')) == 120


def test_equal_products_are_not_said_to_score_lower() -> None:
    rows = [row(1, 'A', 100.0), row(2, 'B', 100.0)]
    comparison = {'products': rows, 'over_budget': [], 'no_price': []}
    ranking = score.rank(REQS, rows, rated(r1='yyyy', r2='yyyy'), comparison, None, 'product')
    assert ranking[1]['why_not'] == 'Same score and price'


def test_each_check_keeps_its_short_reason(monkeypatch) -> None:
    """"½ partly" alone means nothing to the user: each check keeps why, from the page, cleaned like the pros."""
    monkeypatch.setattr(score, 'ask', lambda *a, **k: score.Ratings(products=[score.Rated(n=1, checks=[
        score.Check(requirement='r1', verdict='yes', note='Particles up to 35 mm'),
        score.Check(requirement='r2', verdict='partly', note='Lifts 10 m; see bol.com for the route')], pros=[], cons=[])]))
    rated = score.rate('pump', 'product', REQS, [row(1, 'A', 10.0)])
    assert rated[1]['notes'] == {'r1': 'Particles up to 35 mm', 'r2': '', 'r3': '', 'r4': ''}
    entry = score.rank(REQS, [row(1, 'A', 10.0)], rated, {'products': [row(1, 'A', 10.0)], 'over_budget': [], 'no_price': []},
                       None, 'product')[0]
    assert entry['notes']['r1'] == 'Particles up to 35 mm'


def test_each_product_shows_the_points_it_earns_per_requirement() -> None:
    """The score must be traceable: per requirement the points earned out of its weight, the total, then the score."""
    rows = [row(1, 'A', 100.0)]
    entry = score.rank(REQS, rows, rated(r1='ypun'), {'products': rows, 'over_budget': [], 'no_price': []}, None, 'product')[0]
    assert entry['points'] == {'r1': 3.0, 'r2': 1.5, 'r3': 0.5, 'r4': 0.0}
    assert (entry['points_total'], entry['points_max'], entry['score']) == (5.0, 9, 56)
