"""The followed sites as one list: saving it looks up the feed of each new site and drops the ones left out."""

import httpx

from automation_desk import stop
from automation_desk.groups.news import store
from automation_desk.groups.news.feeds import find_feed


def _address(site: str) -> str:
    """A site address with https:// and without a trailing slash."""
    site = site.strip().rstrip('/')
    return site if site.startswith('http') else f'https://{site}'


def _key(site: str) -> str:
    """A site without scheme, 'www.' or trailing slash, lower case: how the list matches known sites."""
    site = site.strip().casefold().removeprefix('https://').removeprefix('http://').removeprefix('www.')
    return site.rstrip('/')


def save_site_list(lines: list[str]) -> list[str]:
    """Make the followed sites exactly the list: look up the feed of each new site, drop the ones no longer listed.

    Returns a line per site that could not be added; the rest of the list is saved anyway.
    """
    stop.begin('news-sites')
    known = {_key(s.site): s for s in store.sources()}
    wanted = list(dict.fromkeys(_key(line) for line in lines if line.strip()))
    problems = []
    new = [key for key in wanted if key not in known]
    with httpx.Client(timeout=20.0) as http:
        for i, key in enumerate(new):
            if stop.requested('news-sites'):
                left = new[i:]
                names = ', '.join(left[:-1]) + f' and {left[-1]}' if len(left) > 1 else left[0]
                problems.append(f'Stopped: {names} {"were" if len(left) > 1 else "was"} not added. Save again to add them.')
                break
            try:
                name, feed, kind = find_feed(_address(key), http)
            except httpx.HTTPError as error:
                problems.append(f'{key} was not added: it could not be reached ({error}).')
                continue
            store.add_source(_address(key), name, feed, kind)
    for key, source in known.items():
        if key not in wanted:
            store.remove_source(source.id)
    return problems
