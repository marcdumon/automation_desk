"""News standard tasks: sites with feed lookup, subjects, making a digest now."""

import httpx
import pytest

from automation_desk.groups.base import UserError
from automation_desk.groups.news import store
from automation_desk.groups.news.tasks import make, sites
from automation_desk.groups.news.tasks.blocked import BlockedArgs, ManageBlocked
from automation_desk.groups.news.tasks.sites import ManageSites, SitesArgs
from automation_desk.groups.news.tasks.subjects import ManageSubjects, SubjectsArgs

from .conftest import FakeGoogle


def test_add_and_remove_sites(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    found = {'https://standaard.be': ('De Standaard', 'https://www.standaard.be/rss', 'feed'),
             'https://simonwillison.net': ('Simon Willison', '', 'frontpage')}
    monkeypatch.setattr(sites, 'find_feed', lambda site, http: found[site])
    ctx = make_ctx(FakeGoogle({}), '')
    args = SitesArgs(status='ok', message='', add=['standaard.be', 'https://simonwillison.net/'], remove=[])
    preview, payload = ManageSites().resolve(args, ctx)
    assert [(r.cells['Site'], r.cells['Feed']) for r in preview.rows] == [
        ('De Standaard', 'https://www.standaard.be/rss'), ('Simon Willison', 'no feed: front page')]
    ManageSites().execute(payload, {r.id for r in preview.rows}, ctx)
    assert [s.name for s in store.sources()] == ['De Standaard', 'Simon Willison']
    preview, payload = ManageSites().resolve(SitesArgs(status='ok', message='', add=[], remove=['standaard']), ctx)
    ManageSites().execute(payload, {r.id for r in preview.rows}, ctx)
    assert [s.name for s in store.sources()] == ['Simon Willison']


def test_unreachable_site_is_said_plainly(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(site: str, http: object) -> tuple:
        """No answer."""
        raise httpx.ConnectError('no route')

    monkeypatch.setattr(sites, 'find_feed', down)
    with pytest.raises(UserError, match=r'Could not reach nope\.example'):
        ManageSites().resolve(SitesArgs(status='ok', message='', add=['nope.example'], remove=[]), make_ctx(FakeGoogle({}), ''))


def test_subjects(make_ctx) -> None:
    store.set_subjects(['Tech', 'AI'])
    ctx = make_ctx(FakeGoogle({}), '')
    args = SubjectsArgs(status='ok', message='', add=['Housing'], remove=['AI'], rename_from=['Tech'], rename_to=['Technology'])
    _preview, payload = ManageSubjects().resolve(args, ctx)
    ManageSubjects().execute(payload, {'subjects'}, ctx)
    assert store.subjects() == ['Technology', 'Housing']


def test_make_now_runs_a_digest(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    store.add_source('https://a.be', 'A', 'https://a.be/rss', 'feed')
    ran = []
    monkeypatch.setattr(make, 'make_digest', lambda trigger, allow_browser: ran.append((trigger, allow_browser)) or 7)
    ctx = make_ctx(FakeGoogle({}), '')
    _preview, payload = make.MakeDigest().resolve(make.MakeArgs(status='ok', message=''), ctx)
    assert make.MakeDigest().execute(payload, {'digest'}, ctx) == ['Digest made: open it on the News page.']
    assert ran == [('button', True)]


def test_block_and_unblock_topics(make_ctx) -> None:
    ctx = make_ctx(FakeGoogle({}), '')
    _preview, payload = ManageBlocked().resolve(BlockedArgs(status='ok', message='', add=['sports', 'Showbiz', 'TV programs'],
                                                            remove=[]), ctx)
    ManageBlocked().execute(payload, {'blocked'}, ctx)
    assert store.blocked() == ['sports', 'Showbiz', 'TV programs']
    preview, payload = ManageBlocked().resolve(BlockedArgs(status='ok', message='', add=[], remove=['SHOWBIZ']), ctx)
    assert preview.rows[0].cells == {'Before': 'sports, Showbiz, TV programs', 'After': 'sports, TV programs'}
    ManageBlocked().execute(payload, {'blocked'}, ctx)
    assert store.blocked() == ['sports', 'TV programs']
