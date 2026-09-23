"""Who runs an agenda website: the short name events are titled with, and its address.

One profile per website, kept in data/organisers.json inside the project and editable from the preview. A site seen
for the first time gets a profile proposed by the model from the site's own signals (domain, site name, page title,
organisation data, footer). The name is a naming choice; the address must literally appear on the site, or it is dropped.
"""

import json
import re
import threading
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from automation_desk.config import ROOT
from automation_desk.llm import ask

STORE = ROOT / 'data' / 'organisers.json'
SIGNAL_CHARS = 3_000
_lock = threading.Lock()


@dataclass(frozen=True)
class Organiser:
    """The organisation behind an agenda website."""

    site: str
    name: str
    address: str


def site_of(url: str) -> str:
    """The website an address belongs to, e.g. 'kmska.be'."""
    return urlparse(url).netloc.lower().removeprefix('www.')


def stored(site: str) -> Organiser | None:
    """The saved profile of a site, if any."""
    if not STORE.exists():
        return None
    entry = json.loads(STORE.read_text()).get(site)
    return Organiser(site=site, **entry) if entry else None


def save(organiser: Organiser) -> None:
    """Remember a site's profile."""
    with _lock:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        profiles = json.loads(STORE.read_text()) if STORE.exists() else {}
        profiles[organiser.site] = {k: v for k, v in asdict(organiser).items() if k != 'site'}
        STORE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2, sort_keys=True))


def signals(html: str, site: str) -> str:
    """What a page says about who runs the site: domain, site name, title, organisation data and footer."""
    soup = BeautifulSoup(html, 'lxml')
    lines = [f'Website: {site}']
    if soup.title and soup.title.string:
        lines.append(f'Page title: {soup.title.string.strip()}')
    for meta in soup.find_all('meta', attrs={'property': 'og:site_name'}):
        lines.append(f"Site name: {meta.get('content', '')}")
    for script in soup.find_all('script', type='application/ld+json'):
        for name, address in re.findall(r'"name"\s*:\s*"([^"]{2,80})"[^{}]*?"address"\s*:\s*"([^"]{5,120})"', script.string or ''):
            lines.append(f'Organisation data: {name}, {address}')
    footer = soup.find('footer')
    text = ' '.join((footer or soup.body or soup).get_text(' ').split())
    lines.append(f'Footer: {text[-SIGNAL_CHARS:] if not footer else text[:SIGNAL_CHARS]}')
    return '\n'.join(lines)


class OrganiserFound(BaseModel):
    """The model's proposal for a site's profile."""

    short_name: str = Field(description="The short name people commonly use for the organisation that runs the site: "
                                        "'MoMA' for The Museum of Modern Art, 'Tate' for Tate Gallery, 'MAS' for Museum aan "
                                        "de Stroom, 'KMSKA', 'Kanal', 'Bozar'. Keep its usual capitals.")
    address: str = Field(description='Its street address copied exactly as written in the text, or empty when absent.')


def _norm(text: str) -> str:
    """Case- and space-insensitive form."""
    return ' '.join(text.split()).casefold()


def propose(html: str, site: str, http: httpx.Client | None = None) -> Organiser:
    """A first profile for a site, from its own signals; an address not found in them is dropped."""
    evidence = signals(html, site)
    found = ask('You identify the organisation that runs an agenda website, from what the site says about itself.',
                evidence, OrganiserFound, http=http, purpose='identify organiser of website')
    address = ' '.join(found.address.split())
    parts = [p.strip() for p in address.split(',') if p.strip()]
    if not parts or not all(_norm(p) in _norm(evidence) for p in parts):
        address = ''
    return Organiser(site=site, name=' '.join(found.short_name.split()) or site, address=address)


def organiser_for(url: str, html: str, http: httpx.Client | None = None) -> Organiser:
    """The saved profile of the site `url` belongs to, or a new proposal (saved) for a site seen the first time."""
    site = site_of(url)
    known = stored(site)
    if known:
        return known
    fresh = propose(html, site, http)
    save(fresh)
    return fresh


# CLAUDE> short linking words stay lowercase inside a title (English, Dutch, French)
SMALL_WORDS = {'a', 'an', 'and', 'at', 'by', 'for', 'from', 'in', 'of', 'on', 'or', 'the', 'to', 'with',
               'aan', 'bij', 'de', 'een', 'en', 'het', 'met', 'naar', 'om', 'op', 'over', 'tot', 'uit', 'van', 'voor',
               'au', 'aux', 'avec', 'dans', 'des', 'du', 'et', 'la', 'le', 'les', 'par', 'pour', 'sur', 'un', 'une'}


def fix_caps(title: str, keep: set[str] = frozenset()) -> str:
    """An all-caps title in normal capitals; single letters (X) and `keep` (KMSKA) stay as they are."""
    letters = [c for c in title if c.isalpha()]
    if len(title.split()) < 2 or not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return title
    keep_upper = {k.upper() for k in keep}
    words = []
    for index, word in enumerate(title.split(' ')):
        core = word.strip('.,:;!?()"\'\u2019')
        if core.upper() in keep_upper:
            words.append(word)
        elif index and core.lower() in SMALL_WORDS:
            words.append(word.lower())
        elif len(core) == 1:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return ' '.join(words)


def titled(title: str, organiser: Organiser) -> str:
    """The event title starting with the organiser's short name, unless it already does."""
    title = fix_caps(title, {organiser.name, *organiser.name.split()})
    if not organiser.name or _norm(title).startswith(_norm(organiser.name)):
        return title
    return f'{organiser.name}: {title}'


def placed(place: str, organiser: Organiser) -> str:
    """The event's place, completed with the organiser's address when it is missing or only names the organiser."""
    if not organiser.address:
        return place or organiser.name
    if not place:
        return f'{organiser.name}, {organiser.address}'
    names_organiser = _norm(organiser.name) in _norm(place)
    has_address = bool(re.search(r'\d', place)) or _norm(organiser.address) in _norm(place)
    return f'{place}, {organiser.address}' if names_organiser and not has_address else place
