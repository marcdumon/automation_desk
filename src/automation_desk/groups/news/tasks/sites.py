"""Standard task: add or remove news sites; adding finds each site's feed."""

import httpx
from pydantic import Field

from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, UserError, match_name
from automation_desk.groups.news import store
from automation_desk.groups.news.feeds import find_feed


class SitesArgs(TaskArgs):
    """Sites to add and to remove, as the user named them."""

    add: list[str] = Field(description="Sites to add, as written: addresses or domains, e.g. ['lemonde.fr']. Else [].")
    remove: list[str] = Field(description='Sites to remove, as the user named them. Else [].')


def _address(site: str) -> str:
    """A site address with https:// and without a trailing slash."""
    site = site.strip().rstrip('/')
    return site if site.startswith('http') else f'https://{site}'


class ManageSites(StandardTask):
    """Follow or stop following news sites and blogs."""

    id = 'manage_sites'
    name = 'Add or remove news sites'
    description = 'Adds news sites or blogs to the daily digest (finding their news feed) or removes them.'
    example = 'add sites lemonde.fr, standaard.be and simonwillison.net'
    Args = SitesArgs

    def resolve(self, args: SitesArgs, ctx: Context) -> tuple[Preview, dict]:
        """Look up each new site's feed; match the ones to remove by name or address."""
        rows, adds, removes = [], [], []
        with httpx.Client(timeout=20.0) as http:
            for site in (_address(s) for s in args.add if s.strip()):
                try:
                    name, feed, kind = find_feed(site, http)
                except httpx.HTTPError as error:
                    raise UserError(f'Could not reach {site.removeprefix("https://")}: {error}') from error
                adds.append({'site': site, 'name': name, 'feed': feed, 'kind': kind})
                rows.append(Row(id=f'add:{site}', cells={'Change': 'add', 'Site': name, 'Address': site,
                                                         'Feed': feed or 'no feed: front page'}))
        known = [{'id': s.id, 'name': s.name, 'site': s.site} for s in store.sources()]
        for wanted in (w for w in args.remove if w.strip()):
            try:
                found = match_name(wanted, known, 'name', 'site')
            except UserError:
                found = match_name(wanted, known, 'site', 'site')
            removes.append(found['id'])
            rows.append(Row(id=f'remove:{found["id"]}', cells={'Change': 'remove', 'Site': found['name'],
                                                               'Address': found['site'], 'Feed': ''}))
        if not rows:
            raise UserError('Name the sites to add or remove.')
        return Preview(summary=f'{len(adds)} site(s) to add, {len(removes)} to remove', columns=['Change', 'Site', 'Address', 'Feed'],
                       rows=rows), {'adds': adds, 'removes': removes}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Add and remove the ticked sites."""
        results = []
        for add in payload['adds']:
            if f'add:{add["site"]}' in selected:
                store.add_source(add['site'], add['name'], add['feed'], add['kind'])
                results.append(f'Added {add["name"]}.')
        for source_id in payload['removes']:
            if f'remove:{source_id}' in selected:
                store.remove_source(source_id)
                results.append('Removed a site.')
        return results
