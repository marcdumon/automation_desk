"""Save a chat in Obsidian: as a new note in the Research folder, or as a block in a note the user chose, by the same rules
as a summary (at the top, at the end or after a heading; a section or a chapter; a new save replaces the block). Each
question is a part of its own with its answers under it; the headings of an answer move down under the answer, so the
note's outline stays right."""

import json

from automation_desk.research.export import _link, _quoted, _title
from automation_desk.research.export import save as save_new
from automation_desk.research.note_text import HEADING, MY_NOTES, marks
from automation_desk.research.summary_note import write_block

STOPPED = 'Stopped before the end.'


def _shift(text: str, top: int) -> str:
    """An answer whose highest heading is at level `top` and the others keep their depth under it (at most 6); lines in
    code blocks are left alone."""
    lines = text.split('\n')
    fence, found = '', []
    for number, line in enumerate(lines):
        mark = line.lstrip()[:3]
        if mark in ('```', '~~~') and (not fence or mark == fence):
            fence = '' if fence else mark
        elif not fence and (match := HEADING.match(line)):
            found.append((number, len(match.group(1)), match.group(2)))
    highest = min((level for _, level, _ in found), default=1)
    for number, level, title in found:
        lines[number] = f'{"#" * min(top + level - highest, 6)} {title}'
    return '\n'.join(lines)


def _turns(chat: dict) -> list[tuple[dict, list[dict]]]:
    """Each question with its answers that have text: the chosen one (else the first) first, then the others as they came."""
    turns = []
    for question in (m for m in chat['messages'] if m['role'] == 'user'):
        answers = [m for m in chat['messages'] if m['turn'] == question['turn'] and m['role'] == 'assistant' and m['content'].strip()]
        main = next((a for a in answers if a['chosen']), answers[0] if answers else None)
        turns.append((question, [main, *(a for a in answers if a is not main)] if main else []))
    return turns


def _answer(answer: dict, level: int, label: str) -> list[str]:
    """One answer under its heading, why it ended early, and its sources."""
    lines = [f'{"#" * level} {label} — {answer["model"]}', '', _shift(answer['content'].strip(), level + 1), '']
    if answer['state'] in ('stopped', 'failed'):
        lines += [f'*{answer["error"] or STOPPED}*', '']
    if answer['sources']:
        lines += ['Sources:', *(f'- {_link(s["title"] or s["url"], s["url"])}' for s in answer['sources']), '']
    return lines


def _part(chat: dict, level: int, heading: str) -> list[str]:
    """The chat as one part of a note under a heading of `level`: each question one level lower, its answers lower again."""
    lines = [f'{"#" * level} {heading}', '']
    for number, (question, answers) in enumerate(_turns(chat), 1):
        quote = [f'> {_quoted(line)}' for line in question['content'].strip().splitlines()]
        lines += [f'{"#" * (level + 1)} Question {number}', *quote, '']
        for index, answer in enumerate(answers):
            lines += _answer(answer, level + 2, 'Answer' if index == 0 else 'Second opinion')
    return lines


def _row(chat: dict) -> dict:
    """The chat as the note code of the research names and finds its notes."""
    return {'id': chat['id'], 'title': chat['title'], 'request': chat['title'], 'created': chat['created']}


def markdown(chat: dict) -> str:
    """A new note, after the user's project note template: frontmatter, related_to and the web sources, the title, the chat
    as one '##' part, and My notes last."""
    answers = [a for _, given in _turns(chat) for a in given]
    models = list(dict.fromkeys(a['model'] for a in answers))
    sources = list(dict.fromkeys(s['url'] for a in answers for s in a['sources']))
    front = {'type': 'project note', 'creation_date': chat['created'][:19].replace('T', ' '), 'author': '', 'finished': 'true',
             'chat_id': chat['id'], 'cost_usd': round(chat['cost_usd'], 4),
             'models': json.dumps(', '.join(models), ensure_ascii=False)}
    lines = ['---', *(f'{k}: {v}'.rstrip() for k, v in front.items()), 'tags: [chat]', '---', 'related_to:', 'sources:',
             *(f'- <{url}>' for url in sources), '', '---', f'# {_title(_row(chat))}', '', *_part(chat, 2, 'Chat')]
    return '\n'.join(lines).rstrip() + f'\n\n{MY_NOTES}\n'


def block(chat: dict, level: int) -> str:
    """The chat as a block for a note the user chose: a section (level 2) or a chapter (level 1), between its marks."""
    start, end = marks(f'chat {chat["id"]}')
    return '\n'.join([start, *_part(chat, level, _title(_row(chat))), end])


def save(chat: dict, vault: str, subdir: str, exact: bool = False, fresh: bool = False) -> dict:
    """Save the chat: into the note of its target (where it is already, or with `fresh` at the place chosen now); else as a
    new note in the vault and folder of the Research settings, found again by its chat_id."""
    target = chat.get('target') or {}
    if not target.get('note'):
        return save_new(_row(chat), vault, subdir, markdown(chat), exact, own=f'chat_id: {chat["id"]}') | {'message': ''}
    return write_block(target, f'chat {chat["id"]}', block(chat, target['level']), fresh)
