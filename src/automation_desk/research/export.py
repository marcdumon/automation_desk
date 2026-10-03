"""Save a research as a Markdown note in an Obsidian vault: the request, the answers, the requirements, the recommendation,
every product compared and the risks. The same research always goes to the same file."""

import json
import re
from pathlib import Path
from urllib.parse import quote

from automation_desk.research.requirements import WEIGHTS
from automation_desk.research.run import stop_text

OBSIDIAN_CONFIG = Path.home() / '.config' / 'obsidian' / 'obsidian.json'
TITLE_CHARS = 60
# CLAUDE> everything under this heading is the user's own: a new save keeps it
MY_NOTES = '## My notes'
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
    """An amount, or a dash."""
    return '—' if value is None else f'€ {value:,.2f}'.replace(',', ' ')


def _pts(value: float | None) -> str:
    """Points without a needless '.0', or a dash."""
    return '—' if value is None else f'{value:g}'


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
    """The note."""
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
             f'# {_title(row)}', '', *(f'> {_quoted(line)}' for line in row['request'].strip().splitlines()), '']
    if answers := _answers(row):
        lines += ['## Your answers', *(f'- {q}: {a}' for q, a in answers), '']
    if reqs:
        lines += ['## Requirements', '| # | Requirement | Weight | Points |', '|---|---|---|---|',
                  *(f'| R{n} | {_cell(r["text"])} | {r["weight"]} | {WEIGHTS[r["weight"]]} |' for n, r in enumerate(reqs, 1)),
                  f'| | Total | | {sum(WEIGHTS[r["weight"]] for r in reqs)} |', '']
    lines += ['## Recommendation']
    best = result.get('best')
    if best:
        where = f' at {best["shop"]}' if best.get('shop') else ''
        lines += [f'**{_link(best["title"], best.get("url", ""))}** — {_euro(best.get("total"))}{where} · score '
                  f'{best.get("score", "—")}/100', '']
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
        lines += ['', '## All products compared', 'R1, R2 …: the points each product earns on that requirement (see Requirements).',
                  '', f'| Product | Price |{heads} Points | Score | Pros | Cons | Why not |',
                  '|---|---|' + '---|' * len(reqs) + '---|---|---|---|---|']
        for e in ranking:
            note = ' — '.join(part for part in (e.get('tag', ''), e.get('why_not', '')) if part)
            earned = ''.join(f' {_pts((e.get("points") or {}).get(r["id"]))} |' for r in reqs)
            lines.append(f'| {_link(e["title"], e.get("url", ""))} | {_euro(e.get("total"))} |{earned} '
                         f'{_pts(e.get("points_total"))} of {e.get("points_max", "—")} | {e.get("score", "—")} | '
                         f'{_cell("; ".join(e.get("pros", [])))} | {_cell("; ".join(e.get("cons", [])))} | {_cell(note)} |')
    no_price = (result.get('comparison') or {}).get('no_price') or []
    if no_price and row['kind'] == 'product':
        lines += ['', '## Products without a price',
                  *(f'- {_link(p.get("title", p.get("name", "")), (p.get("offers") or [{}])[0].get("url", ""))}' for p in no_price)]
    if result.get('risks'):
        lines += ['', '## Risks and points to check', *(f'- {r}' for r in result['risks'])]
    if result.get('unread'):
        lines += ['', '## Shops not read', *(f'- {u}' for u in result['unread'])]
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


def save(row: dict, vault: str, subdir: str) -> dict:
    """Write the note; the same research overwrites its own note, another one with the same title gets '_2'."""
    root, folder = _folder(vault, subdir)
    folder.mkdir(parents=True, exist_ok=True)
    stem, n = _stem(row), 1
    while True:
        path = folder / (f'{stem}.md' if n == 1 else f'{stem}_{n}.md')
        if not path.exists() or f'\nresearch_id: {row["id"]}\n' in path.read_text(errors='replace')[:500]:
            break
        n += 1
    mine = ''
    if path.exists() and MY_NOTES in (old := path.read_text(errors='replace')):
        mine = old.split(MY_NOTES, 1)[1].strip('\n')
    path.write_text(markdown(row) + (f'\n{mine}\n' if mine else ''))
    inside = path.relative_to(root).with_suffix('').as_posix()
    return {'path': str(path), 'obsidian_url': f'obsidian://open?vault={quote(root.name)}&file={quote(inside, safe="")}'}
