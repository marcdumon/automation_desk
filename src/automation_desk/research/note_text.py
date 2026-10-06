"""The outline of a Markdown note: its headings, the summary and chat blocks in it (between two hidden Obsidian comments),
and where a new block goes, so that no heading of the note moves under it. Shared by the research notes, the summaries and
the chats."""

import re

# CLAUDE> 1 to 6 '#' signs and a space: a tag line like '#research' is no heading
HEADING = re.compile(r'^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$')
# CLAUDE> a block's key names its kind and number: 'summary 7', 'chat 3'
START_MARK = re.compile(r'%% ((?:summary|chat) \d+) %%')
END_MARK = re.compile(r'%% end of ((?:summary|chat) \d+) %%')
# CLAUDE> everything under this heading is the user's own: a new save keeps it, and it stays the last part of a note
MY_NOTES = '## My notes'


def key(block_id: int | str) -> str:
    """A block's key: 'chat 3' as is; a number alone ('7', 7) is a summary."""
    return f'summary {block_id}' if str(block_id).isdigit() else str(block_id)


def marks(block_id: int | str) -> tuple[str, str]:
    """The hidden lines around a summary's or a chat's block in a note."""
    return f'%% {key(block_id)} %%', f'%% end of {key(block_id)} %%'


def heading_lines(lines: list[str]) -> list[tuple[int, int, str]]:
    """Each heading as (line number, level, text), without the frontmatter and code blocks."""
    start = _body_start(lines)
    found, fence = [], ''
    for number in range(start, len(lines)):
        line = lines[number].rstrip()
        mark = line.lstrip()[:3]
        if mark in ('```', '~~~') and (not fence or mark == fence):
            fence = '' if fence else mark
            continue
        if not fence and (match := HEADING.match(line)):
            found.append((number, len(match.group(1)), match.group(2)))
    return found


def headings(text: str, skip: int | str | None = None) -> list[dict]:
    """The headings of a note, for the user to choose where a block goes; without those in the block `skip` (a save puts
    that block in afresh, so a place inside it means nothing)."""
    lines = text.split('\n')
    skipped = key(skip) if skip is not None else None
    own = [(first, last) for block_key, first, last in blocks([line.strip() for line in lines]) if block_key == skipped]
    return [{'level': level, 'text': title} for number, level, title in heading_lines(lines)
            if not any(first < number < last for first, last in own)]


def _body_start(lines: list[str]) -> int:
    """The first line after the frontmatter."""
    if lines and lines[0].strip() == '---':
        return next((n + 1 for n in range(1, len(lines)) if lines[n].strip() == '---'), 0)
    return 0


def blocks(bare: list[str]) -> list[tuple[str, int, int]]:
    """Each block in a note as (key, start line, end line): a start mark and the next end mark of the same block. A start
    mark whose end mark the user deleted is left alone: a later start mark of that block replaces it."""
    started: dict[str, int] = {}
    found = []
    for number, line in enumerate(bare):
        if start := START_MARK.fullmatch(line):
            started[start.group(1)] = number
        elif (end := END_MARK.fullmatch(line)) and end.group(1) in started:
            found.append((end.group(1), started.pop(end.group(1)), number))
    return found


def _outside(found: list[tuple[int, int, str]], inside: list[tuple[str, int, int]]) -> list[tuple[int, int, str]]:
    """The headings that are not in a summary block."""
    return [h for h in found if not any(first < h[0] < last for _, first, last in inside)]


def _place(lines: list[str], level: int, place: str, heading: dict | None) -> tuple[int, str]:
    """The line a block of `level` goes before, and a message when the chosen heading is gone. No heading of the note may
    end up under the block, and the block never goes inside another summary's block."""
    found = heading_lines(lines)
    inside = blocks([line.strip() for line in lines])
    free = _outside(found, inside)
    at, message, chosen_line = len(lines), '', None
    if place == 'after' and heading:
        chosen = next((h for h in found if (h[1], h[2]) == (heading['level'], heading['text'])), None)
        if chosen is None:
            message, place = f'The heading "{heading["text"]}" is no longer in the note, so the summary went to the end.', 'end'
        else:
            # CLAUDE> a chapter (#) after '## X' must not go before '## Y': Y would leave its chapter for the summary
            chosen_line, top = chosen[0], min(chosen[1], level)
            at = next((number for number, lvl, _ in found if number > chosen_line and lvl <= top), len(lines))
            if at == len(lines):
                # CLAUDE> the chosen part runs to the end of the note (the title's part does): My notes stays last there too
                place = 'end'
    if place == 'top':
        # CLAUDE> a section goes under the note's title and its intro text, before the title's first part; a chapter
        # goes before the title itself: any later place would make the parts that follow children of the summary
        parent = next((h for h in free if h[1] < level), None)
        if parent:
            at = next((number for number, lvl, _ in found if number > parent[0] and lvl <= level), len(lines))
        else:
            at = next((number for number, lvl, _ in found if lvl <= level), _body_start(lines))
    if place == 'end':
        # CLAUDE> My notes stays the last part: a section goes before it; a chapter after it, else My notes would be its child
        parts = [h for h in free if h[1] <= 2]
        # CLAUDE> a heading chosen under My notes keeps the block under it
        if level >= 2 and parts and (parts[-1][1], parts[-1][2]) == (2, MY_NOTES[3:]) and \
                (chosen_line is None or parts[-1][0] > chosen_line):
            at = parts[-1][0]
    for _, first, last in inside:
        # CLAUDE> never inside another summary's block: saving that summary again would delete this one. A heading chosen
        # inside that block puts this one after it, else this one goes before it.
        if first < at <= last:
            at = last + 1 if chosen_line is not None and first < chosen_line < last else first
    return at, message


def _put(lines: list[str], at: int, block_text: str) -> str:
    """The note with the block before line `at`, one blank line around it."""
    before, after = lines[:at], lines[at:]
    while before and not before[-1].strip():
        before.pop()
    while after and not after[0].strip():
        after.pop(0)
    return '\n'.join([*before, *([''] if before else []), *block_text.split('\n'), '', *after])


def insert(text: str, block_text: str, block_id: int | str, level: int, place: str = 'end',
           heading: dict | None = None) -> tuple[str, str]:
    """The note with the block (`block_id`: a summary's number or 'chat 3') in it, and a message when the chosen heading
    was not found ('' otherwise). A block already in the note is replaced where it is. `place` is 'top', 'end' or 'after'
    (`heading`): after a heading the block follows all text under it."""
    lines = text.split('\n')
    # CLAUDE> a lone start mark (its end mark deleted) is no block: pairing it with a later end mark would remove the
    # user's text between them
    found = blocks([line.strip() for line in lines])
    own = next(((first, last) for block_key, first, last in found if block_key == key(block_id)), None)
    if own:
        return '\n'.join(lines[:own[0]] + block_text.split('\n') + lines[own[1] + 1:]), ''
    # CLAUDE> Obsidian hides the marks: deleting a block's text there leaves its marks alone; they go before it comes back
    lines = [line for line in lines if line.strip() not in marks(block_id)]
    at, message = _place(lines, level, place, heading)
    return _put(lines, at, block_text), message


def remove(text: str, block_id: int | str) -> str:
    """The note without the block's complete text; a lone mark is left alone."""
    lines = text.split('\n')
    found = blocks([line.strip() for line in lines])
    own = next(((first, last) for block_key, first, last in found if block_key == key(block_id)), None)
    if not own:
        return text
    before, after = lines[:own[0]], lines[own[1] + 1:]
    while before and not before[-1].strip():
        before.pop()
    while after and not after[0].strip():
        after.pop(0)
    return '\n'.join([*before, '', *after] if after else [*before, ''])


def lift_blocks(text: str) -> tuple[str, list[tuple[str, tuple[int, str] | None]]]:
    """The note without its summary blocks, and each block with the heading right after it outside the blocks (level and
    text, None at the end), in their order: a new save of the note puts them back at their places."""
    lines = text.split('\n')
    inside = blocks([line.strip() for line in lines])
    if not inside:
        return text, []
    free = _outside(heading_lines(lines), inside)
    kept = [('\n'.join(lines[first:last + 1]), next(((lvl, title) for number, lvl, title in free if number > last), None))
            for _, first, last in inside]
    taken = {number for _, first, last in inside for number in range(first, last + 1)}
    return '\n'.join(line for number, line in enumerate(lines) if number not in taken), kept


def put_back(text: str, kept: list[tuple[str, tuple[int, str] | None]]) -> str:
    """The note with the blocks lifted from its earlier version, each before its heading (by level and text, else by text:
    a part's level may have changed), else at the end."""
    for block_text, anchor in kept:
        lines = text.split('\n')
        level = next((lvl for _, lvl, _ in heading_lines(block_text.split('\n'))), 2)
        free = _outside(heading_lines(lines), blocks([line.strip() for line in lines]))
        at = None
        if anchor:
            at = next((number for number, lvl, title in free if (lvl, title) == anchor), None)
            at = at if at is not None else next((number for number, _, title in free if title == anchor[1]), None)
        if at is not None:
            # CLAUDE> before a deeper heading ('## Pump guide' before '### Recommendation') the block would take that part
            # and the ones after it in: it goes on to the next heading of its own level or higher
            at = next((number for number, lvl, _ in free if number >= at and lvl <= level), len(lines))
        else:
            at, _ = _place(lines, level, 'end', None)
        text = _put(lines, at, block_text)
    return text
