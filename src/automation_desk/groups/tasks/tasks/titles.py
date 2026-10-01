"""Standard task: give Todoist task titles the form 'Verb: subject' ('Ramen poetsen' -> 'Clean: Ramen').

Rewording takes language, so the model proposes the new titles: unlike the other Tasks actions, the titles of the tasks
to rename are sent to it. Code decides which titles need it, keeps only answers of the right form, and the user sees and
can edit every new title in the preview before anything changes.
"""

import re

from pydantic import BaseModel, Field

from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs
from automation_desk.llm import ask

# CLAUDE> an English verb (one or two words), a colon and the subject: 'Buy: Smoking', 'Back up: Google'
FORM = re.compile(r'^[A-Z][A-Za-z]+(?: [a-z]+)?: \S')
COLUMN = 'New title'

SYSTEM = """You rewrite to-do titles into the form "Verb: subject".
- The verb is ONE English verb (two words only for phrasal verbs like "Back up"), capitalised, followed by a colon.
  Prefer these: Book, Buy, Call, Cancel, Check, Clean, Collect, Configure, Contact, Create, Deepclean, Email, File, Find,
  Fix, Install, Meet, Message, Move, Order, Organise, Pay, Plan, Print, Process, Remove, Renew, Reorganise, Repair, Replace,
  Reply, Research, Return, Review, Sell, Service, Update, Write.
- "Clean" is only for physical cleaning (rooms, appliances, windows: "Clean: Keuken"); clearing out or ordering digital
  things (mail, files, notes, accounts, bookmarks, a phone) is "Reorganise" ("Reorganise: Google Drive").
- The subject keeps the user's own words and language (Dutch stays Dutch); do not translate it, keep names, numbers and
  details; start it with a capital letter. "Ramen poetsen" -> "Clean: Ramen", "Afspraak tandarts maken" -> "Book: Tandarts",
  "Research investment brokers" -> "Research: Investment brokers".
- A title that is not a task but a heading or a place (e.g. "Kitchen", "Bathroom") stays exactly as it is.
Answer for every numbered title, with its number."""


class Proposal(BaseModel):
    """One proposed title."""

    n: int = Field(description='The number of the title in the list.')
    title: str = Field(description='The title in the form "Verb: subject", or the title unchanged when it is not a task.')


class Proposals(BaseModel):
    """A proposal for every numbered title."""

    titles: list[Proposal]


class TitleArgs(TaskArgs):
    """Nothing to fill: every open task whose title is not yet 'Verb: subject' is offered."""


def _needs_verb(title: str) -> bool:
    """A title to reword: not yet 'Verb: subject', and not a saved link."""
    title = title.strip()
    return bool(title) and not FORM.match(title) and not title.startswith('*') and 'http' not in title


class VerbTitles(StandardTask):
    """Give task titles the form 'Verb: subject'."""

    id = 'verb_titles'
    name = 'Give tasks a Verb: title'
    description = ('Proposes a title of the form "Verb: subject" for every open task that does not have one yet ("Ramen poetsen" '
                   'becomes "Clean: Ramen"); you can edit each new title before it is applied. The titles go to the language '
                   'model for this.')
    example = 'give my tasks a verb: title'
    Args = TitleArgs

    def resolve(self, args: TitleArgs, ctx: Context) -> tuple[Preview, dict]:
        """Ask the model once for all titles that need it; keep the answers of the right form."""
        todoist = ctx.todoist()
        projects = {p['id']: p['name'] for p in todoist.projects()}
        candidates = [t for t in todoist.open_tasks() if _needs_verb(t.get('content', ''))]
        if not candidates:
            return Preview(summary='Nothing to rename: every task title already reads Verb: subject.', columns=[], rows=[],
                           read_only=True), {'renames': {}}
        numbered = '\n'.join(f'{n}. {t["content"].strip()}' for n, t in enumerate(candidates, 1))
        answer = ask(SYSTEM, f'Titles:\n{numbered}', Proposals, purpose='reword task titles as Verb: subject')
        proposed = {p.n: ' '.join(p.title.split()) for p in answer.titles}
        renames = {}
        for n, task in enumerate(candidates, 1):
            new = proposed.get(n, '')
            # CLAUDE> only a changed title of the right form is offered; a heading the model left alone is not
            if new and new != task['content'].strip() and FORM.match(new) and len(new) <= 150:
                renames[task['id']] = {'old': task['content'], 'new': new, 'project': projects.get(task['project_id'], '?')}
        return self._compose(renames, len(candidates))

    @staticmethod
    def _compose(renames: dict, asked: int) -> tuple[Preview, dict]:
        """The preview: old and new title per task, the new one editable."""
        rows = [Row(id=task_id, cells={'Title': r['old'], 'Project': r['project'], COLUMN: r['new']}, inputs={COLUMN: r['new']})
                for task_id, r in renames.items()]
        summary = (f'Rename {len(rows)} task(s) to "Verb: subject".' if rows
                   else 'Nothing to rename: the model kept every title as it is.')
        notes = [f'{asked - len(rows)} other title(s) stay as they are (headings, or no proposal of the right form).'] \
            if asked > len(rows) else []
        return Preview(summary=summary, columns=['Title', 'Project', COLUMN], rows=rows, notes=notes,
                       read_only=not rows), {'renames': renames, 'asked': asked}

    def adjust(self, payload: dict, options: dict[str, str], ctx: Context) -> tuple[Preview, dict]:
        """Take the titles the user edited in the preview."""
        renames = {task_id: dict(r) for task_id, r in payload['renames'].items()}
        for name, value in options.items():
            column, _, task_id = name.partition(':')
            if column == COLUMN and task_id in renames and value.strip():
                renames[task_id]['new'] = ' '.join(value.split())
        return self._compose(renames, payload.get('asked', len(renames)))

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Rename each selected task, skipping any whose title changed since the preview."""
        todoist, results = ctx.todoist(), []
        for task_id, r in payload['renames'].items():
            if task_id not in selected:
                continue
            if todoist.task(task_id).get('content') != r['old']:
                results.append(f'Skipped "{r["old"]}": it changed since the preview.')
                continue
            todoist.update(task_id, {'content': r['new']})
            results.append(f'Renamed "{r["old"]}" to "{r["new"]}".')
        return results
