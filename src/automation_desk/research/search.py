"""Searches for a research: pages from OpenRouter web search, without social and video sites, each page once."""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from automation_desk import llm

# CLAUDE> sites that never sell or describe a product in a form the app can compare
SKIPPED_SITES = ('youtube.com', 'facebook.com', 'instagram.com', 'pinterest.', 'tiktok.com', 'reddit.com', 'x.com',
                 'twitter.com', 'wikipedia.org', 'linkedin.com')
COUNTRY_ENDINGS = {'.be': 'BE', '.nl': 'NL', '.de': 'DE', '.fr': 'FR', '.lu': 'LU', '.at': 'AT', '.it': 'IT', '.es': 'ES',
                   '.co.uk': 'UK', '.uk': 'UK', '.pl': 'PL', '.us': 'US'}


@dataclass(frozen=True)
class Hit:
    """One page a search found."""

    url: str
    title: str
    snippet: str
    country: str


def normal_url(url: str) -> str:
    """One spelling per page: https, no www, no tracking query, no trailing slash, no fragment."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix('www.')
    query = '&'.join(p for p in parts.query.split('&') if p and not p.startswith(('utm_', 'gclid', 'fbclid')))
    return urlunsplit(('https', host, parts.path.rstrip('/') or '/', query, '')).removesuffix('/')


def country_of(url: str) -> str:
    """The country a shop's domain belongs to, '' for .com and other general endings."""
    host = urlsplit(url).netloc.lower()
    return next((country for ending, country in COUNTRY_ENDINGS.items() if host.endswith(ending)), '')


def skipped(url: str, sites: tuple[str, ...] = SKIPPED_SITES) -> bool:
    """Whether a page is on one of the sites skipped (the site or a subdomain of it; any Pinterest domain)."""
    host = urlsplit(url).hostname or ''
    return any(
        (site == 'pinterest.' and any('pinterest' in label for label in host.split('.')))
        or (site != 'pinterest.' and (host == site or host.endswith('.' + site)))
        for site in sites
    )


def run(query: str, http: httpx.Client | None = None) -> list[Hit]:
    """One search: the pages found, each once, without skipped sites."""
    hits, seen = [], set()
    for found in llm.search_web(query, http=http):
        url = normal_url(found['url'])
        if url in seen or skipped(url):
            continue
        seen.add(url)
        hits.append(Hit(url=url, title=found['title'], snippet=found['content'][:500], country=country_of(url)))
    return hits
