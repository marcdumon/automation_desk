"""Save a research as a Markdown note in an Obsidian vault: the request, the answers, the requirements, the recommendation,
every product compared and the risks. The same research always goes to the same file."""

import json
import re
from pathlib import Path
from urllib.parse import quote

from automation_desk.research.note_text import MY_NOTES, lift_blocks, put_back
from automation_desk.research.requirements import WEIGHTS
from automation_desk.research.run import stop_text

OBSIDIAN_CONFIG = Path.home() / '.config' / 'obsidian' / 'obsidian.json'
TITLE_CHARS = 60
PART_NAMES = {'product': 'Product research', 'service': 'Service research'}
VERDICT_MARKS = {'yes': 'Yes', 'partly': 'Partly', 'no': 'No', 'unknown': 'Its page does not say'}


class ExportError(ValueError):
    """The note cannot be saved; the message says what to do."""


def vaults(config_file: Path = OBSIDIAN_CONFIG) -> list[dict]:
    """The vaults Obsidian knows on this computer that still exist, and the vaults next to them (a folder with an
    .obsidian folder: Obsidian's own list held only clippings_vault, not work_vault beside it), by folder name."""
    try:
        known = json.loads(config_file.read_text())['vaults']
        found = list(known.values())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    paths = {Path(v['path']) for v in found if isinstance(v, dict) and isinstance(v.get('path'), str)}
    paths = {p for p in paths if p.is_dir()}
    paths |= {child for p in list(paths) for child in p.parent.iterdir() if (child / '.obsidian').is_dir()}
    return [{'name': p.name, 'path': str(p)} for p in sorted(paths, key=lambda p: p.name.casefold())]


def _cell(text: object) -> str:
    """Text that is safe in a Markdown table cell."""
    return ' '.join(str(text).split()).replace('|', '\\|')


def _euro(value: float | None) -> str:
    """An amount, 'free' for nothing, or a dash."""
    return '—' if value is None else 'free' if value == 0 else f'€ {value:,.2f}'.replace(',', ' ')


def _price(entry: dict) -> str:
    """A ranked product's total in euros, with its dollar or pound original under it when it was converted."""
    original = entry.get('original')
    return _euro(entry.get('total')) + (f'<br>from {original["currency"]} {original["total"]:.2f} + VAT' if original else '')


def _pts(value: float | None) -> str:
    """Points without a needless '.0', or a dash."""
    return '—' if value is None else f'{value:g}'


def _bullets(items: list[str]) -> str:
    """A list in a table cell: a cell holds no real list, so each item is a bullet on a line of its own."""
    return '<br>'.join(f'• {_cell(item)}' for item in items)


def _link(title: str, url: str) -> str:
    """A Markdown link, or the plain title without a url."""
    text = _cell(title).replace('[', '\\[').replace(']', '\\]')
    return f'[{text}](<{url}>)' if url else text


def _title(row: dict) -> str:
    """The note's title: the research's short title, else the request's first line, without the characters a file name
    or Obsidian link cannot hold."""
    first = (row.get('title') or '').strip() or (row['request'].strip().splitlines() or [f'Research {row["id"]}'])[0]
    clean = ' '.join(re.sub(r'[\\/:*?"<>|#^\[\]]', ' ', first).split())
    if len(clean) > TITLE_CHARS:
        clean = clean[:TITLE_CHARS].rsplit(' ', 1)[0]
    return clean or f'Research {row["id"]}'


def _stem(row: dict) -> str:
    """The file name as the user's project notes have it: '2026-10-03_lift_pit_sump_pump'."""
    words = re.sub(r'[^\w]+', '_', _title(row).casefold()).strip('_')
    return f'{row["created"][:10]}_{words or f"research_{row['id']}"}'


def _quoted(line: str) -> str:
    """A line of the request for a quote: without the indent and frame signs a paste from a terminal brings along."""
    return line.strip().lstrip('│|').strip()


def _answers(row: dict) -> list[tuple[str, str]]:
    """Each answer with the text of its question."""
    texts = {q['id']: q['text'] for q in (row.get('questions') or []) + (row.get('followups') or [])}
    given = (row.get('answers') or {}) | (row.get('followup_answers') or {})
    return [(texts.get(key, key), value) for key, value in given.items()]


def markdown(row: dict) -> str:
    """The note: the title, the research as one '##' part with its own parts as '###' under it, and My notes last."""
    result = row.get('result') or {}
    reqs = result.get('requirements') or row.get('requirements') or []
    # CLAUDE> the user's project note template: type, creation_date and finished first; then related_to and sources
    # under the frontmatter, a rule, and the title. Free text in the frontmatter is quoted, so a colon cannot break it.
    quoted = {'kind': row['kind'], 'countries': ', '.join(row['countries'])}
    created = row['created'][:19].replace('T', ' ')
    finished = str(row['state'] in ('done', 'stopped')).lower()
    frontmatter = {'type': 'project note', 'creation_date': created, 'author': '', 'finished': finished,
                   'research_id': row['id'], 'budget': row['budget'], 'cost_usd': round(row['cost_usd'], 4)} | \
        {k: json.dumps(v, ensure_ascii=False) for k, v in quoted.items()}
    best_url = (result.get('best') or {}).get('url')
    lines = ['---', *(f'{k}: {v}'.rstrip() for k, v in frontmatter.items() if v is not None), 'tags: [research]', '---',
             'related_to:', 'sources:', *([f'- <{best_url}>'] if best_url else []), '', '---',
             f'# {_title(row)}', '', f'## {PART_NAMES.get(row["kind"], "Research")}',
             *(f'> {_quoted(line)}' for line in row['request'].strip().splitlines()), '']
    if answers := _answers(row):
        lines += ['### Your answers', *(f'- {q}: {a}' for q, a in answers), '']
    if reqs:
        lines += ['### Requirements', '| # | Requirement | Weight | Points |', '|---|---|---|---|',
                  *(f'| R{n} | {_cell(r["text"])} | {r["weight"]} | {WEIGHTS[r["weight"]]} |' for n, r in enumerate(reqs, 1)),
                  f'| | Total | | {sum(WEIGHTS[r["weight"]] for r in reqs)} |', '']
    lines += ['### Recommendation']
    best = result.get('best')
    if best:
        where = f' at {best["shop"]}' if best.get('shop') else ''
        original = best.get('original')
        converted = (f' ({original["currency"]} {original["total"]:.2f} at the ECB rate of {original["date"]}, plus 21% import VAT; '
                     f'customs duties not included)') if original else ''
        lines += [f'**{_link(best["title"], best.get("url", ""))}** — {_euro(best.get("total"))}{where} · score '
                  f'{best.get("score", "—")}/100{converted}', '']
    lines += [part for p in (result.get('summary', ''), result.get('reasons', '')) if p for part in (p, '')]
    if best and reqs:
        notes, points = best.get('notes') or {}, best.get('points') or {}
        lines += ['', '| # | Requirement | Does it meet it? | What its page shows | Points |', '|---|---|---|---|---|',
                  *(f'| R{n} | {_cell(r["text"])} | {VERDICT_MARKS[best["checks"].get(r["id"], "unknown")]} | '
                    f'{_cell(notes.get(r["id"], "")) or "—"} | {_pts(points.get(r["id"]))} of {WEIGHTS[r["weight"]]} |'
                    for n, r in enumerate(reqs, 1)),
                  f'| | Total | | | {_pts(best.get("points_total"))} of {best.get("points_max", "—")} = '
                  f'score {best.get("score")}/100 |']
    if best and (best.get('pros') or best.get('cons')):
        lines += ['', *(f'- Pro: {p}' for p in best.get('pros', [])), *(f'- Con: {c}' for c in best.get('cons', []))]
    if stopped := stop_text(row):
        lines += ['', f'*{stopped}*']
    if ranking := result.get('ranking'):
        heads = ''.join(f' R{n} |' for n in range(1, len(reqs) + 1))
        lines += ['', '### All products compared', 'R1, R2 …: the points each product earns on that requirement (see Requirements).',
                  '', f'| Product | Price |{heads} Points | Score | Pros | Cons |',
                  '|---|---|' + '---|' * len(reqs) + '---|---|---|---|']
        for e in ranking:
            # CLAUDE> as on the page: the tag under the name, and why it is not the recommendation as its first cons
            why = [] if e.get('tag') == 'recommended' else [part for part in e.get('why_not', '').split('. ') if part]
            name = _link(e['title'], e.get('url', '')) + (f'<br>*{_cell(e["tag"])}*' if e.get('tag') else '')
            earned = ''.join(f' {_pts((e.get("points") or {}).get(r["id"]))} |' for r in reqs)
            lines.append(f'| {name} | {_price(e)} |{earned} {_pts(e.get("points_total"))} of {e.get("points_max", "—")} | '
                         f'{e.get("score", "—")} | {_bullets(e.get("pros", []))} | {_bullets(why + e.get("cons", []))} |')
    no_price = (result.get('comparison') or {}).get('no_price') or []
    if no_price and row['kind'] == 'product':
        lines += ['', '### Products without a price',
                  *(f'- {_link(p.get("title", p.get("name", "")), (p.get("offers") or [{}])[0].get("url", ""))}' for p in no_price)]
    if result.get('risks'):
        lines += ['', '### Risks and points to check', *(f'- {r}' for r in result['risks'])]
    if result.get('unread'):
        lines += ['', '### Shops not read', *(f'- {u}' for u in result['unread'])]
    return '\n'.join(lines).rstrip() + f'\n\n{MY_NOTES}\n'


def _folder(vault: str, subdir: str) -> tuple[Path, Path]:
    """The vault and the folder in it, checked: the vault exists and the folder stays inside it."""
    if not vault.strip():
        raise ExportError('No Obsidian vault is set. Choose one under Settings on the Research page.')
    root = Path(vault).expanduser()
    if not root.is_dir():
        raise ExportError(f'The vault {root} does not exist. Choose another one under Settings on the Research page.')
    folder = (root / subdir.strip().strip('/')).resolve() if subdir.strip() else root.resolve()
    if Path(subdir.strip()).is_absolute() or not folder.is_relative_to(root.resolve()):
        raise ExportError(f'The subfolder "{subdir}" must be a folder inside the vault, like "Research".')
    return root.resolve(), folder


def open_url(root: Path, path: Path) -> str:
    """The link that opens a note of the vault in Obsidian."""
    inside = path.relative_to(root).with_suffix('').as_posix()
    return f'obsidian://open?vault={quote(root.name)}&file={quote(inside, safe="")}'


def save(row: dict, vault: str, subdir: str, text: str | None = None, exact: bool = False, own: str = '') -> dict:
    """Write the note (`text`, else the research's own note) named after its title; the same research overwrites its own
    note, found by its frontmatter line `own` ('research_id: 7' unless given, 'chat_id: 3' for a chat). A note of that name
    that is not this research's gets '_2', or with `exact` (a title the user chose) is refused. The user's text under My
    notes and the blocks put in the note stay."""
    root, folder = _folder(vault, subdir)
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = _stem(row), 1
    mine = f'\n{own or f"research_id: {row["id"]}"}\n'
    what = 'this chat' if own.startswith('chat_id') else 'this research'
    while True:
        path = folder / (f'{stem}.md' if n == 1 else f'{stem}_{n}.md')
        if not path.exists() or mine in path.read_text(errors='replace')[:500]:
            break
        if exact:
            # CLAUDE> the user chose this title: a note of that name that is not this research's is never written over
            raise ExportError(f'A note named "{path.name}" is in {subdir.strip() or "the vault"} already, and it is not a note of '
                              f'{what}. Choose another title.')
        n += 1
    # CLAUDE> the summaries' blocks are taken out first, each with the heading after it, and put back there: a block on top
    # or before My notes was lost by a new save, and a block under My notes would be kept twice
    rest, kept = lift_blocks(path.read_text(errors='replace') if path.exists() else '')
    mine = rest.split(MY_NOTES, 1)[1].strip('\n') if MY_NOTES in rest else ''
    path.write_text(put_back((markdown(row) if text is None else text) + (f'\n{mine}\n' if mine else ''), kept))
    return {'path': str(path), 'obsidian_url': open_url(root, path)}
