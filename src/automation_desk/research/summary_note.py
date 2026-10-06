"""Save a subject summary in Obsidian: as a new note in the Research folder, or as a block in a note the user chose, at the
top, at the end or after one of its headings, as a section (##) or a chapter (#). Two hidden Obsidian comments mark the
block, so a new save replaces it; the rest of the note is not changed."""

import json
import os
from pathlib import Path

from automation_desk.research.export import ExportError, _folder, _link, _quoted, _title, open_url
from automation_desk.research.export import save as save_new
from automation_desk.research.note_text import MY_NOTES, insert, marks, remove
from automation_desk.research.run import stop_text

MAX_NOTES = 30
NOTHING = 'None of the pages read is about the subject, so there is no summary.'
NO_PAGES = 'No page could be read, so there is no summary.'


def _quote(row: dict) -> list[str]:
    """The subject as the user wrote it, as a quote."""
    return [f'> {_quoted(line)}' for line in row['request'].strip().splitlines()]


def _paragraphs(row: dict) -> list[str]:
    """The summary's paragraphs, or why there is none; and why it stopped early."""
    result = row.get('result') or {}
    said = result.get('paragraphs') or [NOTHING if result.get('pages_read') or result.get('off_subject') else NO_PAGES]
    stopped = stop_text(row)
    return [part for p in said for part in (p, '')] + ([f'*{stopped}*', ''] if stopped else [])


def _key_points(result: dict, level: int) -> list[str]:
    """The groups of key points, each point with the links to its sources."""
    links = {s['n']: s['url'] for s in result.get('sources') or []}
    lines = []
    for group in result.get('groups') or []:
        lines += [f'{"#" * level} {group["heading"]}',
                  *(' '.join([f'- {item["text"]}', *(f'[{n}](<{links[n]}>)' for n in sorted(item['sources']) if n in links)])
                    for item in group['items']), '']
    return lines


def _sources(result: dict) -> list[str]:
    """The numbered sources: the page title (else its address) as a link, and the site."""
    return [f'{s["n"]}. {_link(s["title"] or s["url"], s["url"])} — {s["site"]}' for s in result.get('sources') or []]


def _part(row: dict, level: int, heading: str, unused: bool = False) -> list[str]:
    """The summary as one part of a note, under a heading of `level`: the subject, the text, then one level lower the key
    points, the sources and (`unused`) the pages not used. A new note and a block in another note both use it."""
    result = row.get('result') or {}
    lower = '#' * (level + 1)
    lines = [f'{"#" * level} {heading}', *_quote(row), '', *_paragraphs(row)]
    if result.get('groups'):
        lines += [f'{lower} Key points', *_key_points(result, level + 2)]
    if result.get('sources'):
        lines += [f'{lower} Sources', *_sources(result), '']
    gone = [f'- Not read: <{u}>' for u in result.get('unread') or []] + \
        [f'- Not about the subject: <{u}>' for u in result.get('off_subject') or []]
    if unused and gone:
        lines += [f'{lower} Pages not used', *gone, '']
    return lines


def markdown(row: dict) -> str:
    """A new note, after the user's project note template: frontmatter, related_to and sources, the title, the summary as
    one '##' part with its own parts as '###' under it, and My notes last."""
    result = row.get('result') or {}
    sites = ', '.join(item.get('site') or item.get('url') or f'web search: {item.get("query", "")}' for item in row.get('plan') or [])
    front = {'type': 'project note', 'creation_date': row['created'][:19].replace('T', ' '), 'author': '',
             'finished': str(row['state'] in ('done', 'stopped')).lower(), 'research_id': row['id'],
             'cost_usd': round(row['cost_usd'], 4), 'sites': json.dumps(sites, ensure_ascii=False)}
    lines = ['---', *(f'{k}: {v}'.rstrip() for k, v in front.items()), 'tags: [research, summary]', '---', 'related_to:',
             'sources:', *(f'- <{s["url"]}>' for s in result.get('sources') or []), '', '---', f'# {_title(row)}', '',
             *_part(row, 2, 'Summary', unused=True)]
    return '\n'.join(lines).rstrip() + f'\n\n{MY_NOTES}\n'


def block(row: dict, level: int) -> str:
    """The summary as a block for a note the user chose: a section (level 2) or a chapter (level 1), between its marks."""
    start, end = marks(row['id'])
    return '\n'.join([start, *_part(row, level, _title(row)), end])


def notes(vault: str, query: str = '') -> list[dict]:
    """The notes of the vault whose path holds every word of the query, newest first, without Obsidian's own folders."""
    root = Path(vault).expanduser()
    if not vault.strip() or not root.is_dir():
        return []
    words = query.casefold().split()
    found = []
    for folder, folders, files in os.walk(root):
        folders[:] = [f for f in folders if not f.startswith('.')]
        for name in files:
            path = Path(folder) / name
            inside = path.relative_to(root).as_posix()
            if name.endswith('.md') and all(word in inside.casefold() for word in words):
                found.append((path.stat().st_mtime, inside))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [{'path': inside, 'name': Path(inside).stem} for _, inside in found[:MAX_NOTES]]


def note_path(vault: str, note: str) -> Path:
    """A note of the vault, checked: inside the vault, a Markdown file, and there."""
    root, _ = _folder(vault, '')
    path = (root / note).resolve()
    if Path(note).is_absolute() or not path.is_relative_to(root):
        raise ExportError(f'The note "{note}" must be a note inside the vault.')
    if path.suffix.lower() != '.md':
        raise ExportError(f'"{note}" is not a Markdown note (.md). Choose a note.')
    if not path.is_file():
        raise ExportError(f'The note "{note}" does not exist. Choose another note.')
    return path


def save(row: dict, vault: str, subdir: str, exact: bool = False, fresh: bool = False) -> dict:
    """Save the summary: into the note the user chose, in the vault it was chosen in; else as a new note in the vault and
    folder of the Research settings. In a note that has the summary already it is written where it is, or with `fresh` (a
    place the user chose now) taken out and put at that place."""
    target = row.get('target') or {}
    if not target.get('note'):
        return save_new(row, vault, subdir, markdown(row), exact) | {'message': ''}
    return write_block(target, f'summary {row["id"]}', block(row, target['level']), fresh)


def write_block(target: dict, block_id: str, text: str, fresh: bool = False) -> dict:
    """Put a block ('summary 7', 'chat 3') into the note of `target` (vault, note, place, heading, level): where it is
    already, or with `fresh` (a place the user chose now) taken out and put at that place."""
    root, _ = _folder(target['vault'], '')
    path = note_path(target['vault'], target['note'])
    try:
        old = path.read_text()
    except UnicodeDecodeError as error:
        raise ExportError(f'The note "{target["note"]}" is not plain text the app can read, so it is not changed.') from error
    # CLAUDE> a summary started before 'top' existed has no place: with a heading it goes after it, else to the end
    place = target.get('place') or ('after' if target.get('heading') else 'end')
    new, message = insert(remove(old, block_id) if fresh else old, text, block_id, target['level'], place, target.get('heading'))
    path.write_text(new)
    return {'path': str(path), 'obsidian_url': open_url(root, path), 'message': message}

