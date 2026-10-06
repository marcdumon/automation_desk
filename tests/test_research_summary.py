"""A subject summary from websites: the sites and pages named, the key points per page and the summary, with stand-ins."""

import pytest

from automation_desk import llm
from automation_desk.research import summary
from automation_desk.research.pages import Read


def test_each_line_is_a_site_to_search_or_a_page_to_read() -> None:
    lines = ['vrt.be', ' https://www.hln.be/ ', 'www.vrt.be', 'vrt.be/nieuws/', 'https://www.standaard.be/cnt/dmf2026?x=1#top',
             '', 'http://Example.ORG']
    assert summary.parse_sites(lines) == [
        {'kind': 'site', 'site': 'vrt.be'}, {'kind': 'site', 'site': 'hln.be'}, {'kind': 'site', 'site': 'vrt.be/nieuws'},
        {'kind': 'page', 'url': 'https://standaard.be/cnt/dmf2026?x=1'}, {'kind': 'site', 'site': 'example.org'}]


@pytest.mark.parametrize('line', ['solar panels', 'vrt', 'https://', 'ftp://vrt.be/a', 'vrt..be'])
def test_a_line_that_is_no_website_is_named(line: str) -> None:
    with pytest.raises(summary.SiteError, match=f'Line 2, "{line}", is not a website'):
        summary.parse_sites(['vrt.be', line])


def test_search_web_limits_the_search_to_the_site(monkeypatch) -> None:
    sent = {}

    def post(body: dict, http: object) -> dict:
        """Keep the request; answer with no citations."""
        sent['body'] = body
        return {'id': 'g', 'model': 'm', 'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}], 'usage': {}}

    monkeypatch.setattr(llm, '_post', post)
    llm.search_web('flat roof rules', include_domains=['vrt.be'])
    assert sent['body']['plugins'] == [{'id': 'web', 'engine': 'exa', 'max_results': 10, 'include_domains': ['vrt.be']}]


def test_a_site_search_keeps_only_pages_of_that_site(monkeypatch) -> None:
    """OpenRouter calls include_domains "domains to prioritize": code makes sure."""
    asked = {}

    def search_web(query: str, max_results: int = 10, http: object = None, include_domains: list | None = None) -> list[dict]:
        """Hits on the site, a subdomain, another site, a look-alike and the same page twice."""
        asked.update(query=query, domains=include_domains)
        return [{'url': u, 'title': f'T{n}', 'content': 'c'} for n, u in enumerate((
            'https://www.vrt.be/nl/a', 'https://sporza.vrt.be/b', 'https://hln.be/c', 'https://notvrt.be/d', 'https://vrt.be/nl/a/'))]

    monkeypatch.setattr(llm, 'search_web', search_web)
    hits = summary.search_site('flat roofs', 'vrt.be', http=None)
    assert [h.url for h in hits] == ['https://vrt.be/nl/a', 'https://sporza.vrt.be/b']
    assert asked == {'query': 'flat roofs', 'domains': ['vrt.be']}


def test_a_site_part_keeps_only_pages_under_that_part(monkeypatch) -> None:
    monkeypatch.setattr(llm, 'search_web', lambda *a, **k: [{'url': u, 'title': '', 'content': ''} for u in (
        'https://vrt.be/nieuws', 'https://vrt.be/nieuws/2026/a', 'https://vrt.be/nieuwsbrief', 'https://vrt.be/sport/b')])
    assert [h.url for h in summary.search_site('x', 'vrt.be/nieuws', http=None)] == [
        'https://vrt.be/nieuws', 'https://vrt.be/nieuws/2026/a']


def test_pages_named_are_read_first_then_each_site_in_turn() -> None:
    found = [{'url': 'a1', 'query': 'a.be'}, {'url': 'a2', 'query': 'a.be'}, {'url': 'a3', 'query': 'a.be'},
             {'url': 'b1', 'query': 'b.be'}, {'url': 'p1', 'query': ''}, {'url': 'b2', 'query': 'b.be'}]
    assert [p['url'] for p in summary.reading_order(found)] == ['p1', 'a1', 'b1', 'a2', 'b2', 'a3']


def test_key_points_come_from_the_page_text_without_link_marks(monkeypatch) -> None:
    seen = {}

    def ask(system: str, user: str, schema: type, **kwargs: object) -> summary.KeyPoints:
        """Keep what the model gets."""
        seen.update(system=system, user=user)
        return summary.KeyPoints(relevant=True, title=' Flat roofs  in Flanders ',
                                 points=['  Permits are  needed above 3 m. ', '', *[f'Point {n}.' for n in range(10)]])

    monkeypatch.setattr(summary, 'ask', ask)
    got = summary.key_points(Read(url='https://vrt.be/a', text='Roofs [L1] need permits [L22] now', products=[]), 'flat roofs')
    assert 'Roofs need permits now' in seen['user'] and 'flat roofs' in seen['user'] and 'English' in seen['system']
    assert got == {'relevant': True, 'title': 'Flat roofs in Flanders',
                   'points': ['Permits are needed above 3 m.', *[f'Point {n}.' for n in range(7)]]}


def test_a_page_not_about_the_subject_gives_no_points(monkeypatch) -> None:
    monkeypatch.setattr(summary, 'ask', lambda *a, **k: summary.KeyPoints(relevant=False, title='Sport', points=['Goal.']))
    assert summary.key_points(Read(url='u', text='t', products=[]), 'roofs') == {'relevant': False, 'title': 'Sport', 'points': []}


PAGES = [{'url': 'https://vrt.be/a', 'title': 'Roofs', 'points': ['P one.', 'P two.']},
         {'url': 'https://hln.be/b', 'title': '', 'points': ['P three.']},
         {'url': 'https://www.tijd.be/c', 'title': 'Costs', 'points': ['P four.']}]


def test_the_summary_links_each_key_point_to_its_pages(monkeypatch) -> None:
    seen = {}

    def ask(system: str, user: str, schema: type, **kwargs: object) -> summary.Compiled:
        """Use P4 first, a point twice, an unknown number, and an item without a valid number."""
        seen['user'] = user
        return summary.Compiled(title=' Flat roofs ', paragraphs=['First (P1).', ' ', 'Second.'], groups=[
            summary.Group(heading='Costs', items=[summary.KeyPoint(text='It costs a lot (P4, P9).', points=[4, 9])]),
            summary.Group(heading='Rules', items=[summary.KeyPoint(text='A permit is needed [P1][P3].', points=[1, 3, 2]),
                                                  summary.KeyPoint(text='Made up.', points=[12])]),
            summary.Group(heading='Empty', items=[summary.KeyPoint(text='Nothing.', points=[])])])

    monkeypatch.setattr(summary, 'ask', ask)
    made = summary.compile_points('flat roofs', PAGES)
    assert 'P1. P one.\nP2. P two.\nP3. P three.\nP4. P four.' in seen['user']
    assert made == {
        'title': 'Flat roofs', 'paragraphs': ['First.', 'Second.'],
        'groups': [{'heading': 'Costs', 'items': [{'text': 'It costs a lot.', 'sources': [1]}]},
                   {'heading': 'Rules', 'items': [{'text': 'A permit is needed.', 'sources': [2, 3]}]}],
        'sources': [{'n': 1, 'url': 'https://www.tijd.be/c', 'title': 'Costs', 'site': 'tijd.be'},
                    {'n': 2, 'url': 'https://vrt.be/a', 'title': 'Roofs', 'site': 'vrt.be'},
                    {'n': 3, 'url': 'https://hln.be/b', 'title': '', 'site': 'hln.be'}]}


def test_no_points_means_no_model_call(monkeypatch) -> None:
    monkeypatch.setattr(summary, 'ask', lambda *a, **k: pytest.fail('no points: nothing to pay for'))
    assert summary.compile_points('roofs', []) == {'title': '', 'paragraphs': [], 'groups': [], 'sources': []}


def test_the_web_searches_for_a_subject_come_from_the_model(monkeypatch) -> None:
    seen = {}

    def ask(system: str, user: str, schema: type, **kwargs: object) -> summary.Queries:
        """Five searches, one of them twice."""
        seen.update(system=system, user=user)
        return summary.Queries(queries=[' Dompelpomp  kiezen ', 'dompelpomp kiezen', 'how to choose a sump pump', 'fourth', 'fifth'])

    monkeypatch.setattr(summary, 'ask', ask)
    assert summary.queries('Hoe kies ik een dompelpomp') == ['Dompelpomp kiezen', 'how to choose a sump pump', 'fourth']
    assert 'English' in seen['system'] and 'Hoe kies ik een dompelpomp' in seen['user']


def test_without_searches_from_the_model_the_subject_is_the_search(monkeypatch) -> None:
    monkeypatch.setattr(summary, 'ask', lambda *a, **k: summary.Queries(queries=[' ']))
    assert summary.queries('  Flat   roofs ') == ['Flat roofs']


def test_a_web_search_skips_video_and_social_sites_but_keeps_wikipedia(monkeypatch) -> None:
    asked = {}

    def search_web(query: str, max_results: int = 10, http: object = None, include_domains: list | None = None) -> list[dict]:
        """A video, an encyclopedia page, a forum, and one guide twice."""
        asked.update(query=query, domains=include_domains)
        return [{'url': u, 'title': 'T', 'content': 'c'} for u in (
            'https://www.youtube.com/watch?v=1', 'https://en.wikipedia.org/wiki/Pump', 'https://www.reddit.com/r/x',
            'https://pump.be/guide', 'https://pump.be/guide/')]

    monkeypatch.setattr(llm, 'search_web', search_web)
    assert [h.url for h in summary.search_web_hits('sump pump', http=None)] == ['https://en.wikipedia.org/wiki/Pump',
                                                                                'https://pump.be/guide']
    assert asked == {'query': 'sump pump', 'domains': None}
