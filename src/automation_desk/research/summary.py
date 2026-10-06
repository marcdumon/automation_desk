"""A summary of a subject from websites the user names: each site searched for the subject, each page named read as is,
the important points of every page, and one English summary of them. Code links every key point to its pages, so no
point stands without a source."""

import re
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from automation_desk import llm
from automation_desk.llm import ask
from automation_desk.research import search
from automation_desk.research.pages import Read
from automation_desk.research.search import Hit, normal_url

MAX_POINTS = 8
SITE_RESULTS = 10
MAX_QUERIES = 3
# CLAUDE> a web search for a subject skips video and social sites, but an encyclopedia page is a good source here
WEB_SKIPPED = tuple(site for site in search.SKIPPED_SITES if site != 'wikipedia.org')
# CLAUDE> a domain name: labels of letters, digits and hyphens, a dot between them, and an ending of letters (or xn--)
DOMAIN = re.compile(r'^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$')
LINK_MARK = re.compile(r' \[L\d+\]')
# CLAUDE> the model sometimes repeats its point numbers in the text ("… (P4, P9)", "…[P1][P3]"): the links show them
POINT_REFS = re.compile(r'\s*[(\[]\s*P\d+(?:\s*(?:,|;|&|and)\s*P\d+)*\s*[)\]]')
POINTS_SYSTEM = '''You read one web page for a person who studies a subject. Say whether the page is about the subject
(relevant: true or false), and give the page's own title or headline. If it is about the subject, list the important points
the page states about it: at most 8, each one sentence of at most 35 words, with the numbers, names and dates the page gives.
Only what the page states: no own knowledge, no guesses. Leave out menus, adverts, cookie texts and links to other articles.
Always write in English, also when the page is in Dutch, French or another language.'''
QUERIES_SYSTEM = '''You write web searches that find good, factual pages about a subject a person wants summarised.
Give 1 to 3 short searches: the first in the subject's own words and language, the others with other wording. If the subject
is not in English, make one of them English.'''
COMPILE_SYSTEM = '''You write a summary of a subject from numbered points taken from web pages (P1, P2 ...).
Give a title of at most 6 words; a summary of 1 to 3 short paragraphs with the main findings; and the key points in 2 to 6
groups, each with a short heading. Each key point is one sentence of at most 35 words and names the numbers of the points it
uses. Join points that say the same thing into one key point with all their numbers. Where the pages disagree, say so.
Use only the points given: no own knowledge. Always write in English.'''


class SiteError(ValueError):
    """A line of the websites is not a site or a page; the message names the line and shows how to write it."""


class Queries(BaseModel):
    """The web searches for a subject."""

    queries: list[str]


class KeyPoints(BaseModel):
    """What one page says about the subject."""

    relevant: bool
    title: str = Field(description="The page's own title or headline.")
    points: list[str]


class KeyPoint(BaseModel):
    """One point of the summary."""

    text: str
    points: list[int] = Field(description='The numbers of the points used, e.g. [3, 12] for P3 and P12.')


class Group(BaseModel):
    """Key points under one heading."""

    heading: str
    items: list[KeyPoint]


class Compiled(BaseModel):
    """The summary."""

    title: str
    paragraphs: list[str]
    groups: list[Group]


def _host(netloc: str) -> str:
    """A host name without port and 'www.'."""
    return netloc.lower().rsplit('@', 1)[-1].split(':', 1)[0].removeprefix('www.')


def parse_sites(lines: list[str]) -> list[dict]:
    """Each line as a site to search ({'kind': 'site', 'site': 'vrt.be/nieuws'}) or a page to read ({'kind': 'page',
    'url': ...}), each once. A line with http(s):// and a path is a page; every other line is a site, a path in it keeps
    the search to that part of the site."""
    found: list[dict] = []
    for number, given in enumerate(lines, 1):
        line = given.strip()
        if not line:
            continue
        scheme = re.match(r'^([a-z][a-z0-9+.-]*)://', line, re.IGNORECASE)
        parts = urlsplit(line if scheme else f'https://{line}')
        host = _host(parts.netloc)
        if (scheme and scheme.group(1).lower() not in ('http', 'https')) or ' ' in line or not DOMAIN.match(host):
            raise SiteError(f'Line {number}, "{line}", is not a website. Write a site like vrt.be or a page like '
                            f'https://www.vrt.be/nl/nieuws/…')
        path = parts.path.strip('/')
        if scheme and (path or parts.query):
            item = {'kind': 'page', 'url': normal_url(line)}
        else:
            item = {'kind': 'site', 'site': f'{host}/{path}' if path else host}
        if item not in found:
            found.append(item)
    return found


def on_site(url: str, site: str) -> bool:
    """Whether a page is on the site (or a subdomain of it), and under its part when the site names one."""
    domain, _, part = site.partition('/')
    parts = urlsplit(url)
    host = _host(parts.netloc)
    if host != domain and not host.endswith(f'.{domain}'):
        return False
    path = parts.path.rstrip('/')
    return not part or path == f'/{part}' or path.startswith(f'/{part}/')


def search_site(subject: str, site: str, http: httpx.Client | None) -> list[Hit]:
    """One search for the subject inside one site: its pages, each once. Unlike a product search no site is skipped, and
    no country is checked: the user named the site."""
    hits, seen = [], set()
    for found in llm.search_web(subject, max_results=SITE_RESULTS, http=http, include_domains=[site]):
        url = normal_url(found['url'])
        # CLAUDE> OpenRouter calls include_domains "domains to prioritize": a page of another site is dropped here
        if url in seen or not on_site(url, site):
            continue
        seen.add(url)
        hits.append(Hit(url=url, title=found['title'], snippet=found['content'][:500], country=''))
    return hits


def queries(subject: str, http: httpx.Client | None = None) -> list[str]:
    """The web searches for a summary without websites: at most 3, each once; the subject itself when the model gives none."""
    answer = ask(QUERIES_SYSTEM, f'Subject: {subject}', Queries, http=http, purpose='summary: searches')
    found: list[str] = []
    for query in answer.queries:
        if (text := ' '.join(query.split())) and text.casefold() not in {f.casefold() for f in found}:
            found.append(text)
    return found[:MAX_QUERIES] or [' '.join(subject.split())]


def search_web_hits(query: str, http: httpx.Client | None) -> list[Hit]:
    """One search of the whole web: its pages, each once, without video and social sites."""
    hits, seen = [], set()
    for found in llm.search_web(query, max_results=SITE_RESULTS, http=http):
        url = normal_url(found['url'])
        if url in seen or search.skipped(url, WEB_SKIPPED):
            continue
        seen.add(url)
        hits.append(Hit(url=url, title=found['title'], snippet=found['content'][:500], country=''))
    return hits


def reading_order(pages: list[dict]) -> list[dict]:
    """The pages the user named first, then the hits of each site in turns (the first of each site, then the second …), so
    each site is read before the page limit ends the reading."""
    named = [p for p in pages if not p['query']]
    by_site: dict[str, list[dict]] = {}
    for page in pages:
        if page['query']:
            by_site.setdefault(page['query'], []).append(page)
    rounds = max((len(hits) for hits in by_site.values()), default=0)
    return named + [hits[n] for n in range(rounds) for hits in by_site.values() if n < len(hits)]


def _said(text: str) -> str:
    """A text on one line, without the point numbers the model may repeat in it."""
    return ' '.join(POINT_REFS.sub('', text).split())


def key_points(read: Read, subject: str, http: httpx.Client | None = None) -> dict:
    """What a page says about the subject: whether it is about it, its title and at most 8 points, in English."""
    text = LINK_MARK.sub('', read.text)
    answer = ask(POINTS_SYSTEM, f'Subject: {subject}\n\nPage: {read.url}\n\n{text}', KeyPoints, http=http,
                 purpose='summary: key points')
    points = [said for p in answer.points if (said := ' '.join(p.split()))][:MAX_POINTS] if answer.relevant else []
    return {'relevant': bool(points), 'title': ' '.join(answer.title.split()), 'points': points}


def compile_points(subject: str, pages: list[dict], http: httpx.Client | None = None) -> dict:
    """The summary of the points of the pages ({'url', 'title', 'points'}): title, paragraphs, key points in groups. Each
    key point keeps the sources its point numbers name; a key point without a valid number is left out. The sources are
    numbered in the order they are first used."""
    numbered = [(index, text) for index, page in enumerate(pages) for text in page['points']]
    if not numbered:
        return {'title': '', 'paragraphs': [], 'groups': [], 'sources': []}
    listing = '\n'.join(f'P{n}. {text}' for n, (_, text) in enumerate(numbered, 1))
    answer = ask(COMPILE_SYSTEM, f'Subject: {subject}\n\nPoints:\n{listing}', Compiled, http=http, purpose='summary: compile')
    order: dict[int, int] = {}
    groups = []
    for group in answer.groups:
        items = []
        for item in group.items:
            used = list(dict.fromkeys(numbered[n - 1][0] for n in item.points if 1 <= n <= len(numbered)))
            if used and (text := _said(item.text)):
                items.append({'text': text, 'sources': sorted(order.setdefault(index, len(order) + 1) for index in used)})
        if items:
            groups.append({'heading': _said(group.heading) or 'Key points', 'items': items})
    sources = [{'n': n, 'url': pages[index]['url'], 'title': pages[index]['title'],
                'site': _host(urlsplit(pages[index]['url']).netloc)} for index, n in sorted(order.items(), key=lambda pair: pair[1])]
    return {'title': _said(answer.title), 'paragraphs': [said for p in answer.paragraphs if (said := _said(p))], 'groups': groups,
            'sources': sources}
