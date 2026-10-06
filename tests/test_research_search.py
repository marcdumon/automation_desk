"""Web search through OpenRouter: hits from the answer's citations, filtered and deduplicated."""

from automation_desk import llm
from automation_desk.research import search


def reply(*urls: str) -> dict:
    """An OpenRouter answer with one citation per url."""
    notes = [{'type': 'url_citation', 'url_citation': {'url': u, 'title': f'T {n}', 'content': f'C {n}'}}
             for n, u in enumerate(urls)]
    return {'id': 'g1', 'model': 'm', 'choices': [{'message': {'content': 'OK', 'annotations': notes}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'cost': 0.008}}


def test_search_web_sends_the_exa_plugin_and_returns_citations(monkeypatch) -> None:
    sent = {}
    monkeypatch.setattr(llm, '_post', lambda body, http: sent.setdefault('body', body) and reply('https://a.be/p'))
    assert llm.search_web('dompelpomp vlotter') == [{'url': 'https://a.be/p', 'title': 'T 0', 'content': 'C 0'}]
    assert sent['body']['plugins'] == [{'id': 'web', 'engine': 'exa', 'max_results': 10}]


def test_duplicate_urls_are_one_page(monkeypatch) -> None:
    monkeypatch.setattr(llm, '_post', lambda body, http: reply(
        'https://www.shop.be/p?utm_source=x', 'http://shop.be/p', 'https://shop.be/p/', 'https://www.youtube.com/watch?v=1',
        'https://pompen.de/tauch'))
    hits = search.run('dompelpomp')
    assert [(h.url, h.country) for h in hits] == [('https://shop.be/p', 'BE'), ('https://pompen.de/tauch', 'DE')]


def test_country_from_the_domain() -> None:
    assert [search.country_of(u) for u in ('https://x.nl/a', 'https://x.fr', 'https://x.co.uk/b', 'https://x.com')] == [
        'NL', 'FR', 'UK', '']


def test_skip_filter_no_false_positives(monkeypatch) -> None:
    """Skip filter matches exact domains, not substrings. wix.com should not match x.com skip rule."""
    monkeypatch.setattr(llm, '_post', lambda body, http: reply(
        'https://wix.com/x', 'https://reddit.com/r/test', 'https://xreddit.com/page'))
    hits = search.run('shop')
    assert [(h.url, h.title) for h in hits] == [('https://wix.com/x', 'T 0'), ('https://xreddit.com/page', 'T 2')]


def test_malformed_citations_are_skipped(monkeypatch) -> None:
    """An annotation without its url is left out instead of failing the search."""
    good = reply('https://a.be/p')
    good['choices'][0]['message']['annotations'] += [{'type': 'url_citation'}, {'type': 'url_citation', 'url_citation': {}},
                                                     {'type': 'url_citation', 'url_citation': 'x'}]
    monkeypatch.setattr(llm, '_post', lambda body, http: good)
    assert [h['url'] for h in llm.search_web('pomp')] == ['https://a.be/p']


def test_a_us_address_is_from_the_usa() -> None:
    assert search.country_of('https://shop.us/pump') == 'US'
